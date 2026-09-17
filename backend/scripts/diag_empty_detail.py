"""
诊断：评估详情是否真的“全部为空”。

只读打开 data/soft_ip.db（immutable 模式，不写 journal、不抢锁），
逐案打印 context_json 里的 dimension_results 状态，定位根因：

  - 若“全部为空 / 全部缺失” → 说明 DB 里确实没存下维度结果
    （多半是评估中途异常，被 evaluation.py 的 except 分支存了个空的 context）；
  - 若“部分有、部分空” → 说明是某些节点/轴没跑完；
  - 若“其实都有” → 那问题在前端渲染或 API 返回，不在数据层。

用法：
  cd backend
  python scripts/diag_empty_detail.py                 # 默认 data/soft_ip.db
  python scripts/diag_empty_detail.py /path/to.db     # 指定库
"""
import json
import os
import sqlite3
import sys

# 期望的维度键（与 orchestrator.NODE_ORDER / active_business_dimensions 对应）
EXPECTED = {
    "eval-prep": ["prep"],
    "eval-legal": ["legal"],
    "eval-business": ["damages", "recovery"],   # 要钱
    "eval-synth": ["synthesis"],
    "eval-moot": ["moot"],
}
# 要名时 business 维度是 precedent 而非 damages/recovery


def classify(dr: dict | None, expected_keys: list[str]) -> str:
    if dr is None:
        return "missing"          # context 里根本没有 dimension_results
    if not isinstance(dr, dict):
        return f"weird:{type(dr).__name__}"
    present = [k for k in expected_keys if k in dr and dr[k] is not None]
    if not present:
        return "empty"            # 有这个字段但全是空
    if len(present) < len(expected_keys):
        return f"partial({len(present)}/{len(expected_keys)})"
    return "full"


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    default_db = os.path.normpath(os.path.join(here, "..", "..", "data", "soft_ip.db"))
    db_path = sys.argv[1] if len(sys.argv) > 1 else default_db

    if not os.path.exists(db_path):
        print(f"[ERROR] 找不到数据库：{db_path}")
        sys.exit(1)

    # immutable=1：完全只读，不写 -wal/-journal，也不发锁，避免与运行中后端冲突
    uri = f"file:{db_path}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    rows = cur.execute(
        "select id, name, goal_type, status, context_json from cases order by id"
    ).fetchall()

    print(f"数据库：{db_path}")
    print(f"案件总数：{len(rows)}\n")
    print(f"{'case_id':<10} {'goal':<6} {'status':<11} {'ctx?':<5} {'dr状态':<16} dimension_results 键")
    print("-" * 100)

    tally = {"missing": 0, "empty": 0, "partial": 0, "full": 0, "weird": 0}
    for r in rows:
        cid = r["id"] or ""
        goal = r["goal_type"] or "?"
        status = r["status"] or "?"
        raw = r["context_json"]
        if raw is None:
            print(f"{cid:<10} {goal:<6} {status:<11} {'no':<5} {'no-context':<16} （context_json 为 NULL）")
            tally["missing"] += 1
            continue

        try:
            ctx = json.loads(raw) if isinstance(raw, str) else raw
        except Exception as e:
            print(f"{cid:<10} {goal:<6} {status:<11} {'bad':<5} {'ctx-parse-err':<16} {e}")
            tally["weird"] += 1
            continue

        dr = ctx.get("dimension_results")
        # 要名时 business 期望 precedent
        biz_keys = ["precedent"] if goal == "要名" else ["damages", "recovery"]
        expected = ["prep", "legal"] + biz_keys + ["synthesis", "moot"]
        state = classify(dr, expected)
        if state.startswith("partial"):
            tally["partial"] += 1
        elif state in tally:
            tally[state] += 1
        else:
            tally["weird"] += 1

        keys = sorted(dr.keys()) if isinstance(dr, dict) else str(dr)
        scores = ctx.get("scores")
        score_flag = "有scores" if scores else "无scores"
        print(f"{cid:<10} {goal:<6} {status:<11} {'yes':<5} {state:<16} {keys}  [{score_flag}]")

    print("-" * 100)
    print("汇总：", tally)

    # 抽样打印一个“空详情”但 status 非 pending 的案例，确认是空存还是没存
    print("\n--- 抽查：status 非 pending 但 dimension_results 为空的案例 ---")
    found = False
    for r in rows:
        status = r["status"] or "?"
        if status in ("pending",):
            continue
        raw = r["context_json"]
        if raw is None:
            continue
        try:
            ctx = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            continue
        dr = ctx.get("dimension_results")
        if dr is None or (isinstance(dr, dict) and not any(v for v in dr.values())):
            found = True
            cid = r["id"]
            print(f"\n[{cid}] goal={r['goal_type']} status={status}")
            print("  context_json 顶层键：", sorted(ctx.keys()))
            # 看看有没有 error / traceback / phase 线索
            for k in ("phase", "error", "evaluation_status", "eval_status"):
                if k in ctx:
                    print(f"  {k} = {ctx[k]}")
    if not found:
        print("（无：空详情的案例 status 都是 pending，或根本不存在）")

    conn.close()


if __name__ == "__main__":
    main()
