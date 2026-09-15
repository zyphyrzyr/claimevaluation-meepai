"""
L2 全链路 E2E（HTTP + SSE 脚本）
覆盖：三案由×双目标矩阵、SSE 事件契约、节点重跑、顾问闭环、内嵌法庭回写、独立模式隔离、版本快照
前置：后端 http://localhost:8000 运行中（USE_MOCK=True）
运行：backend/ 目录 python tests/e2e_l2_http.py

⚠️ 防污染：本脚本会向「当前运行中后端」的数据库写入 E2E 案件与全局库条目
（含 /knowledge/deposit 沉淀）。不要对着真实数据的 dev server 跑——应先以隔离
数据目录启动后端再跑本脚本：
  SOFT_IP_DATA_DIR=/tmp/softip_e2e USE_MOCK=True bash start.sh
否则 E2E 沉淀会污染真实全局经验库（曾积压 19 条，见 scripts/cleanup_knowledge.py）。
"""
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://localhost:8000/api"
RESULTS = []


def req(path, method="GET", body=None, raw=False, timeout=180):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    r.add_header("Content-Type", "application/json")
    resp = urllib.request.urlopen(r, timeout=timeout)
    text = resp.read().decode()
    return text if raw else json.loads(text)


def sse(path, body=None, timeout=180):
    """读取 SSE 流，返回事件列表"""
    text = req(path, "POST", body or {}, raw=True, timeout=timeout)
    return [json.loads(l[5:]) for l in text.split("\n") if l.startswith("data:")]


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    RESULTS.append((status, name, detail))
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    return condition


def new_case(name, cause, goal, desc, evidence_texts=""):
    return req("/cases", "POST", {
        "name": name, "cause_type": cause, "goal_type": goal,
        "case_description": desc,
        "client_org": "我方权利人公司",   # 必填：主诉评估由权利人发起
        "defendant_name": "某某公司",
        "evidence_texts": evidence_texts,
    })


# ============================================================
CASES = {
    "商标侵权": ("原告持有第1234567号注册商标，核定使用商品为第25类服装。"
             "被告在天猫旗舰店销售印有近似标识的卫衣，月销量三万件，单价199元，已公证取证。",
             "商标注册证第1234567号，核定使用商品第25类服装，续展有效。\n\n"
             "侵权公证书（2026）沪字第888号，载明被告店铺月销量三万件、单价199元。"),
    "著作权侵权": ("原告为美术作品《幻彩森林》的著作权人，已做作品登记。"
               "被告在其网店商品详情页未经许可使用了该作品作为背景图案，且销售印有该图案的手机壳。",
               "作品登记证书（国作登字-2026-F-00012345）。\n\n"
               "被告店铺页面截图及可信时间戳认证证书。"),
    "不正当竞争": ("原告主营'清源'品牌茶饮，在全国拥有600家门店，具有较高市场知名度。"
               "被告在其门店使用与原告高度近似的装潢与'清原'标识，容易导致消费者混淆。",
               "原告门店照片与加盟合同，证明在先使用与知名度。\n\n"
               "被告门店装潢对比照片及消费者混淆评价截图。"),
}


def test_matrix():
    print("\n" + "=" * 70)
    print("一、三案由 × 双目标 矩阵")
    print("=" * 70)
    matrix_rows = []
    for cause, (desc, ev) in CASES.items():
        for goal in ("要钱", "要名"):
            label = f"{cause} × {goal}"
            case = new_case(f"E2E-{cause[:3]}-{goal}", cause, goal, desc, ev)
            cid = case["id"]
            events = sse(f"/evaluation/{cid}/run")
            kinds = [e["event"] for e in events]
            result = req(f"/evaluation/{cid}/result")
            scores = result["scores"]

            ok_event = "flow_blocked" not in kinds
            ok_score = scores.get("final") is not None
            matrix_rows.append({
                "案由": cause, "目标": goal, "事件数": len(events),
                "法律": scores.get("legal_feasibility"),
                "业务": scores.get("business_expectation"),
                "决策分": scores.get("final"),
                "置信度": result.get("confidence"),
                "建议": result["recommendation"]["recommendation"],
                "缺口": len(result["evidence"]["gap_list"]),
                "回款": result["defendant_profile"].get("recovery_ability"),
            })
            check(f"{label} 评估完成",
                  ok_event and ok_score,
                  f"事件 {len(events)} 个，决策分 {scores.get('final')}，"
                  f"建议「{result['recommendation']['recommendation']}」")

    print("\n  矩阵汇总：")
    print(f"  {'案由':<8}{'目标':<6}{'法律':>7}{'业务':>7}{'决策分':>8}{'置信度':>8}{'回款':>7}{'缺口':>6}  建议")
    print("  " + "-" * 74)
    for r in matrix_rows:
        print(f"  {r['案由']:<8}{r['目标']:<6}{r['法律'] or 0:>7.1f}{r['业务'] or 0:>7.1f}"
              f"{r['决策分'] or 0:>8.1f}{r['置信度'] or 0:>8.1f}{r['回款'] or 0:>7.0f}"
              f"{r['缺口']:>6}  {r['建议']}")
    return matrix_rows


def test_sse_contract():
    print("\n" + "=" * 70)
    print("二、SSE 事件契约")
    print("=" * 70)
    case = new_case("E2E-SSE契约", "商标侵权", "要钱", CASES["商标侵权"][0],
                    CASES["商标侵权"][1])
    cid = case["id"]
    events = sse(f"/evaluation/{cid}/run")

    started = [e["node"] for e in events if e["event"] == "node_started"]
    finished = [e["node"] for e in events if e["event"] == "node_finished"]
    expected = ["evidence_review", "red_gate", "rights", "infringement",
                "procedure", "business", "synthesize"]

    check("节点启动顺序符合 NODE_ORDER", started == expected, f"{started}")
    check("每节点都有 node_finished", finished == expected, f"{finished}")
    check("事件携带中文标签", all("label" in e for e in events))
    check("存在终态事件 flow_finished",
          any(e["event"] == "flow_finished" for e in events),
          f"末事件：{events[-1].get('event') if events else '无'}")
    print(f"  事件类型分布：{ {e['event'] for e in events} }")


def test_rerun():
    print("\n" + "=" * 70)
    print("三、节点级重跑 + 下游失效传播")
    print("=" * 70)
    case = new_case("E2E-重跑", "商标侵权", "要钱", CASES["商标侵权"][0],
                    CASES["商标侵权"][1])
    cid = case["id"]
    sse(f"/evaluation/{cid}/run")
    before = req(f"/evaluation/{cid}/result")
    before_final = before["scores"]["final"]

    out = req(f"/evaluation/{cid}/rerun", "POST",
              {"node": "rights", "guidance": "商标续展证明已补齐，请重新评估权利基础"})
    dims = out["dimension_results"]

    check("下游侵权认定标记 stale", dims.get("infringement", {}).get("status") == "stale",
          f"实际 {dims.get('infringement', {}).get('status')}")
    check("决策合成立即重算（非 stale）",
          dims.get("synthesize", {}).get("status") != "stale",
          f"实际 {dims.get('synthesize', {}).get('status')}")
    check("引导意见注入观点",
          any("续展" in v for v in req(f"/cases/{cid}")["context"].get("user_viewpoints", [])),
          f"{req(f'/cases/{cid}')['context'].get('user_viewpoints')}")

    # P1-2 已修：依赖图指向真实的子维度键，重跑证据盘点应失效判赔规模
    out2 = req(f"/evaluation/{cid}/rerun", "POST",
               {"node": "evidence_review", "guidance": "补充了销量公证书"})
    damages_status = out2["dimension_results"].get("damages", {}).get("status")
    check("重跑证据盘点后判赔规模失效", damages_status == "stale",
          f"实际 {damages_status}")

    # 前端展示契约：stale_nodes / effect / thresholds 必须齐全
    check("rerun 回传待重跑节点清单",
          any(s["node"] == "damages" for s in out2.get("stale_nodes", [])),
          f"{out2.get('stale_nodes')}")
    check("rerun 回传审计 effect 文案", bool(out2.get("effect")),
          f"{out2.get('effect')}")

    result2 = req(f"/evaluation/{cid}/result")
    th = result2.get("thresholds", {})
    check("结果回传档位与四象限中线",
          th.get("go") == 78 and th.get("patch") == 62 and th.get("quadrant_mid") == 78,
          f"{th}")
    check("结果回传案由与业务目标",
          result2.get("cause_type") == "商标侵权" and result2.get("goal_type") == "要钱",
          f"{result2.get('cause_type')}/{result2.get('goal_type')}")

    check("未知节点返回 400", _expect_error(
        lambda: req(f"/evaluation/{cid}/rerun", "POST", {"node": "不存在"})), "")
    return before_final


def _expect_error(fn):
    try:
        fn()
        return False
    except urllib.error.HTTPError as e:
        return e.code == 400


def test_advisor_loop():
    print("\n" + "=" * 70)
    print("四、伴随式追问顾问闭环")
    print("=" * 70)
    case = new_case("E2E-顾问", "商标侵权", "要钱", CASES["商标侵权"][0],
                    CASES["商标侵权"][1])
    cid = case["id"]
    sse(f"/evaluation/{cid}/run")

    events = sse(f"/advisor/{cid}/chat",
                 {"question": "我们的商标注册证能否覆盖被告的商品类别？判赔大概什么水平？"})
    kinds = {}
    for e in events:
        kinds[e["event"]] = kinds.get(e["event"], 0) + 1
    answer = "".join(e.get("text", "") for e in events if e["event"] == "delta")

    check("顾问流含 recall 事件（自动召回）", kinds.get("recall", 0) > 0, f"{kinds}")
    check("顾问流含 delta 流式增量", kinds.get("delta", 0) > 0, f"共 {kinds.get('delta',0)} 段")
    check("回答非空", len(answer) > 20, f"{len(answer)} 字")

    hist = req(f"/advisor/{cid}/history")
    check("对话历史落库", len(hist.get("messages", [])) >= 2,
          f"{len(hist.get('messages', []))} 条")

    # 沉淀 → 全局库可检索
    dep = req(f"/knowledge/cases/{cid}/deposit", "POST",
              {"title": "E2E沉淀-判赔口径",
               "content": "天猫店铺月销三万件、单价199元的商标侵权案，判赔可争取30万元以上。"})
    check("观点沉淀成功", dep.get("ok") is True or dep.get("id"), f"{dep}")

    hits = req("/knowledge/search", "POST",
               {"query": "天猫店铺判赔 三十万", "top_k": 5})
    check("沉淀内容可被全局检索命中",
          any("E2E沉淀" in h["title"] for h in hits),
          f"命中：{[h['title'][:16] for h in hits]}")

    # 跨案隔离
    other = new_case("E2E-隔离对照案", "商标侵权", "要钱",
                     "另一个完全无关的案件，被告为个体工商户。", "无相关证据材料。")
    oid = other["id"]
    leak = req("/knowledge/search", "POST",
               {"query": "商标注册证 核定使用商品 第25类", "case_id": oid, "top_k": 5})
    leaked = [h for h in leak if h["scope"] == "case" and h["case_id"] != oid]
    check("跨案材料零泄漏", not leaked, f"泄漏 {[h['title'][:16] for h in leaked]}")
    return cid


def test_moot():
    print("\n" + "=" * 70)
    print("五、模拟法庭双模式")
    print("=" * 70)
    case = new_case("E2E-法庭", "商标侵权", "要钱", CASES["商标侵权"][0],
                    CASES["商标侵权"][1])
    cid = case["id"]
    sse(f"/evaluation/{cid}/run")
    before = req(f"/evaluation/{cid}/result")
    before_final = before["scores"]["final"]
    before_coeff = before.get("correction_coeff", 1.0)

    events = sse(f"/moot/{cid}/run")
    kinds = {}
    for e in events:
        kinds[e["event"]] = kinds.get(e["event"], 0) + 1
    check("内嵌法庭含 recall 自动召回", kinds.get("recall", 0) > 0, f"{kinds}")
    check("内嵌法庭产出多轮发言", kinds.get("speech", 0) + kinds.get("round", 0) > 0, f"{kinds}")

    after = req(f"/evaluation/{cid}/result")
    after_coeff = after.get("correction_coeff", 1.0)
    after_final = after["scores"]["final"]
    check("修正系数已回写", after_coeff != before_coeff,
          f"{before_coeff} → {after_coeff}")
    check("决策分随系数重算", after_final != before_final,
          f"{before_final} → {after_final}")

    record = req(f"/moot/{cid}")
    check("庭审记录可回读", len(record.get("transcript", [])) > 0,
          f"{len(record.get('transcript', []))} 轮")

    # 独立模式：不回写任何案件
    stand = new_case("E2E-法庭独立对照", "商标侵权", "要钱", CASES["商标侵权"][0])
    sid = stand["id"]
    sse(f"/evaluation/{sid}/run")
    before_s = req(f"/evaluation/{sid}/result")
    sev = sse("/moot/standalone", {
        "case_description": CASES["著作权侵权"][0], "cause_type": "著作权侵权",
        "viewpoints": ["被告有明显抄袭故意"], "plaintiff_points": "作品登记证书齐全"})
    fin = [e for e in sev if e["event"] == "moot_finished"]
    drill = fin[0].get("drill_report") if fin else None
    check("独立模式产出演练报告", bool(drill and drill.get("weak_points")),
          f"事件：{[e['event'] for e in sev]}")

    # 案由是否真正生效（P1-4：曾因 cause_type 未下传而固定输出商标剧本）
    all_text = json.dumps(fin[0], ensure_ascii=False) if fin else ""
    check("独立演练尊重案由参数（著作权内容）",
          "著作权" in all_text or "作品" in all_text,
          f"庭审文本前 80 字：{all_text[:80]}")
    check("独立演练不串入商标话术", "注册商标" not in all_text and "《商标法》" not in all_text,
          f"串入：{'注册商标' in all_text or '《商标法》' in all_text}")
    after_s = req(f"/evaluation/{sid}/result")
    check("独立模式不回写评分",
          after_s["scores"]["final"] == before_s["scores"]["final"]
          and after_s.get("correction_coeff") == before_s.get("correction_coeff"),
          f"final {before_s['scores']['final']} → {after_s['scores']['final']}")


def test_versioning():
    print("\n" + "=" * 70)
    print("六、报告版本化")
    print("=" * 70)
    case = new_case("E2E-版本", "商标侵权", "要钱", CASES["商标侵权"][0],
                    CASES["商标侵权"][1])
    cid = case["id"]
    sse(f"/evaluation/{cid}/run")

    v1 = req(f"/report/{cid}/snapshot", "POST", {})
    sse(f"/moot/{cid}/run")           # 触发分数变化
    v2 = req(f"/report/{cid}/snapshot", "POST", {})

    versions = req(f"/report/{cid}/versions")
    nums = sorted(v["version"] for v in versions)
    check("版本号自增", nums == [1, 2], f"{nums}")

    d1 = req(f"/report/{cid}/versions/1")
    d2 = req(f"/report/{cid}/versions/2")
    m1, m2 = d1.get("markdown") or "", d2.get("markdown") or ""
    check("两版内容可分别读取", bool(m1) and bool(m2),
          f"v1 {len(m1)} 字 / v2 {len(m2)} 字")
    check("两版内容有差异（庭审回写后应变化）", m1 != m2,
          f"v1 {len(m1)} 字 / v2 {len(m2)} 字，相同={m1 == m2}")

    try:
        req(f"/report/{cid}/versions/99")
        check("读取不存在版本返回 404", False, "未抛错")
    except urllib.error.HTTPError as e:
        check("读取不存在版本返回 404", e.code == 404, f"{e.code}")


def main():
    print("L2 全链路 E2E 评测（mock 模式）")
    print(f"目标服务：{BASE}")
    t0 = time.time()
    try:
        req("/health")
    except Exception as e:
        print(f"后端未就绪：{e}")
        sys.exit(1)

    test_matrix()
    test_sse_contract()
    test_rerun()
    test_advisor_loop()
    test_moot()
    test_versioning()

    print("\n" + "=" * 70)
    print("评测汇总")
    print("=" * 70)
    passed = sum(1 for s, _, _ in RESULTS if s == "PASS")
    failed = [r for r in RESULTS if r[0] == "FAIL"]
    print(f"  通过 {passed} / {len(RESULTS)}")
    if failed:
        print(f"\n  失败项（{len(failed)}）：")
        for _, name, detail in failed:
            print(f"    ✗ {name}" + (f" — {detail}" if detail else ""))
    print(f"\n  总耗时 {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
