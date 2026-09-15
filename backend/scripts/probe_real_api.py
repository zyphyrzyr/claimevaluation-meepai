"""
真实 API 探针（接 key 后的一键体检）

用法：
    cd backend && python scripts/probe_real_api.py                 # 跑默认两条用例
    cd backend && python scripts/probe_real_api.py --all           # 跑全部 6 条（三案由 × 双目标）
    cd backend && python scripts/probe_real_api.py --cases 商标侵权-要钱
    cd backend && python scripts/probe_real_api.py --log docs/demo-run.md
    cd backend && python scripts/probe_real_api.py -v              # 失败时打完整栈

逐项体检 4 个外部依赖与真实模式主流程，单项失败不中断，最后给一份诊断结论。
目的是把「接上 key 之后到底哪里不通」从猜测变成一份清单。

【判据为什么是现在这样】
早期版本的主流程判据只是「没有节点 status == failed」，而硬门禁拦截时下游节点
压根不进 dimension_results —— 拦截路径永远不会计为失败。结果是探针能在只跑了
1 个节点、分数全 None 的情况下报「12 项通过 / 0 失败」。现在改成正面断言：
关键节点必须在场、三个分数必须非 None、synthesize 必须是 ok。

与 pytest 的关系：本脚本打真实网络、花钱、耗时长，不能进回归测试；
tests/test_l2_real_path.py 用假 HTTP 覆盖同一条代码路径，那个是零成本的版本。

演示素材固化在 scripts/demo_materials.py，不在本脚本里现编。
"""
import argparse
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # demo_materials 与脚本同目录

# 环境里若配了 HTTP 代理，请求国内 API 与 localhost 都可能被挡成 502。
# 探针只打公网外部服务，不碰本地端口，所以直接放掉代理最省事。
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
           "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)

from core.config import get_runtime_settings

OK, WARN, FAIL = "  [OK]  ", "  [WARN]", "  [FAIL]"
results = []

_args = argparse.ArgumentParser(description="真实 API 探针（接 key 后的一键体检）")
_args.add_argument("--all", action="store_true", help="跑全部 6 条演示用例")
_args.add_argument("--cases", default="", help="逗号分隔的用例 key，见 demo_materials.DEMO_CASES")
_args.add_argument("--log", default="", help="把本轮结果写成 markdown 存档")
_args.add_argument("-v", "--verbose", action="store_true", help="失败时打印完整栈")
ARGS = _args.parse_known_args()[0]

run_log = []  # 主流程每条用例的明细，供 --log 存档


def record(status: str, name: str, detail: str = "") -> None:
    results.append((status, name, detail))
    print(f"{status} {name}" + (f" — {detail}" if detail else ""))


def section(title: str) -> None:
    print(f"\n{'=' * 66}\n{title}\n{'=' * 66}")


def _locked_entity(profile: dict) -> str:
    """从企查查画像摘要里取回「实际锁定的主体名」。"""
    first = str(profile.get("_summary") or "").splitlines()[:1]
    if not first or "锁定主体" not in first[0]:
        return ""
    return first[0].split("锁定主体:")[-1].strip()


# ============================================================
section("一、配置检查")

env_file = Path(__file__).resolve().parent.parent.parent / ".env"
print(f"配置文件：{env_file}（{'存在' if env_file.exists() else '缺失'}）")

settings = get_runtime_settings()
use_mock = str(settings.get("USE_MOCK", "")).strip().lower() == "true"
if use_mock:
    record(WARN, "USE_MOCK", "当前为 Mock 模式，真实链路不会被调用（改 .env 的 USE_MOCK=False）")
else:
    record(OK, "USE_MOCK", "真实模式")

KEYS = [
    ("LLM_API_KEY", "LLM 评估节点", True),
    ("QCC_API_TOKEN", "企查查被告画像 / 回款能力", False),
    ("PKULAW_API_TOKEN", "北大法宝检索与引用核验", False),
    ("SILICONFLOW_API_KEY", "bge-m3 向量化（RAG 知识库）", False),
]
missing_required = []
for key, purpose, required in KEYS:
    # 兼容旧 .env：填了 DEEPSEEK_API_KEY 也算配好了
    val = (settings.get(key.lower()) or "").strip()
    if not val and key == "LLM_API_KEY":
        val = (settings.get("DEEPSEEK_API_KEY") or "").strip()
    if val:
        record(OK, f"{key}", f"{purpose}（已配置，长度 {len(val)}）")
    elif required:
        missing_required.append(key)
        record(FAIL, f"{key}", f"{purpose} —— 缺失，真实模式无法运行")
    else:
        record(WARN, f"{key}", f"{purpose} —— 未配置，对应链路会跳过")


# ============================================================
section("二、LLM 连通性")

if missing_required:
    record(FAIL, "LLM 连通性", f"缺少 {missing_required}，跳过")
    llm_ok = False
else:
    from core import llm_gateway

    # node 必须区分开：强模型只在 STRONG_MODEL_NODES 里才被路由到，
    # 两次都传 node=None 的话测的是同一个模型，「强模型」这项等于假的。
    probes = (
        ("普通模型", settings["llm_fast_model"], None),
        ("强模型", settings["llm_strong_model"], "infringement"),
    )
    for label, model, node in probes:
        t0 = time.time()
        try:
            text = llm_gateway.call_text(
                "你是助手。", "只回复两个字：正常", node=node, max_tokens=16)
            cost = time.time() - t0
            record(OK, f"{label} {model}", f"{cost:.1f}s，回复：{text[:20]!r}")
            llm_ok = True
        except Exception as e:
            record(FAIL, f"{label} {model}", f"{type(e).__name__}: {e}")
            llm_ok = False

    # JSON 模式是最容易炸的一环：reasoner 不接受 response_format
    t0 = time.time()
    try:
        payload = llm_gateway.call_json(
            "只输出 JSON。", '输出 {"score": 75, "note": "ok"}', node="rights")
        cost = time.time() - t0
        if "error" in payload:
            record(FAIL, "call_json 结构化输出", f"{payload['error']}")
        else:
            record(OK, "call_json 结构化输出", f"{cost:.1f}s，score={payload.get('score')}")
    except Exception as e:
        record(FAIL, "call_json 结构化输出", f"{type(e).__name__}: {e}")

    # 校验层是否按预期拦下坏输出
    try:
        bad = llm_gateway._validate_payload({"score": 850}, "rights")
        record(OK, "坏分数校验", f"已拦下：{bad}")
    except Exception as e:
        record(FAIL, "坏分数校验", str(e))


# ============================================================
section("三、外部数据源")

# 连通 ≠ 可用。 token 无效、查无此企业、返回空列表都会让「接口调用成功」，
# 但演示时该章节照样是空的、回款照样算不出来。所以空结果一律降级为 WARN，
# 绝不再算 OK —— 早期版本把「法条 0 条」「回款能力 None」都记成了通过。

if (settings.get("pkulaw_api_token") or "").strip():
    from core.pkulaw import pkulaw_api
    try:
        res = pkulaw_api.search_for_rights_foundation()
        laws = res.get("laws", [])
        err = res.get("error")
        if res.get("status") == "skipped":
            record(WARN, "北大法宝", "未生效（token 未被识别）")
        elif err:
            record(WARN, "北大法宝", f"返回错误：{str(err)[:60]}")
        elif not laws:
            record(WARN, "北大法宝", "连通但法条 0 条——报告增强章节会整段跳过")
        else:
            record(OK, "北大法宝", f"法条 {len(laws)} 条")
    except Exception as e:
        record(FAIL, "北大法宝", f"{type(e).__name__}: {e}")
else:
    record(WARN, "北大法宝", "未配置 token，跳过（报告不出现法宝章节）")

if (settings.get("qcc_api_token") or "").strip():
    from core import qcc_api
    try:
        # 参数键是 name 不是 company_name。早期版本写成了 company_name，
        # 结果整段走的是「未提供被告名称」分支——企查查一次都没调用，
        # 却因为不报错而记成了通过。
        target = "华为技术有限公司"
        profile = qcc_api.search_for_financial_qcc_full(
            {"name": target, "type": "enterprise"})
        err = profile.get("error")
        prob = profile.get("metrics", {}).get("recovery_probability")
        if err:
            # 「要钱」路径没有回款能力 → business 为 None → 总分算不出来。
            # 这不是可降级的告警，演示当场出不了分，所以记 FAIL。
            record(FAIL, "企查查", f"回款能力取不到：{str(err)[:60]}")
        elif prob is None:
            record(WARN, "企查查", "画像为空（查无此企业或字段缺失），回款能力为 None")
        else:
            record(OK, "企查查", f"回款能力 {prob}（查 {target}）")
            # 主体锁定是否精确匹配。模糊匹配到同名/近名企业极其危险：
            # 一旦撞上一家已注销的企业，回款能力会被一票否决成 0，
            # 业务预期随之归零，而报告上看不出「查错了人」。
            locked = _locked_entity(profile)
            if locked and locked != target:
                record(WARN, "企查查主体锁定",
                       f"模糊匹配：查「{target}」实际锁定「{locked}」，回款分可能张冠李戴")
    except Exception as e:
        record(FAIL, "企查查", f"{type(e).__name__}: {e}")
else:
    record(WARN, "企查查", "未配置 token，跳过（回款能力 None，「要钱」路径出不了总分）")


# ============================================================
section("四、真实模式主流程")

if use_mock:
    record(WARN, "主流程", "Mock 模式下跳过；设 USE_MOCK=False 后重跑本脚本")
elif missing_required:
    record(FAIL, "主流程", f"缺少 {missing_required}，跳过")
else:
    import demo_materials
    from core.case_context import CaseContext
    from core import orchestrator

    if ARGS.all:
        cases = demo_materials.DEMO_CASES
    elif ARGS.cases:
        cases = demo_materials.pick(ARGS.cases)
    else:
        cases = demo_materials.pick(",".join(demo_materials.DEFAULT_PROBE_KEYS))

    # 关键节点：少一个都说明流程没跑完，而不是「跑完了但有点小问题」。
    COMMON_NODES = ["evidence_review", "red_gate", "rights", "infringement",
                    "procedure", "business", "synthesize"]

    def required_nodes(goal: str):
        return COMMON_NODES + (["damages", "recovery"] if goal == "要钱" else ["precedent"])

    def problems_of(ctx, goal: str):
        """
        正面断言「跑完了」。

        旧判据只看有没有 status == failed 的节点，但硬门禁拦截时下游节点根本
        不会被写进 dimension_results —— 拦截路径永远不会计为失败，于是探针能
        在只跑了 1 个节点、分数全 None 的情况下报全绿。
        """
        dims = ctx.dimension_results
        problems = []

        absent = [n for n in required_nodes(goal) if n not in dims]
        if absent:
            problems.append("缺失节点 " + "/".join(absent))

        failed = {k: (v.get("error") or "")[:50]
                  for k, v in dims.items() if v.get("status") == "failed"}
        if failed:
            problems.append(f"失败节点 {failed}")

        if dims.get("red_gate", {}).get("blocked"):
            blocks = [h.get("rule_code") for h in ctx.red_flags
                      if h.get("severity") == "block"]
            problems.append("硬门禁拦截 " + "/".join(str(b) for b in blocks))

        none_scores = [k for k in ("legal_feasibility", "business_expectation", "final")
                       if ctx.scores.get(k) is None]
        if none_scores:
            problems.append("分数为 None " + "/".join(none_scores))

        return problems

    for case in cases:
        ctx = CaseContext(
            case_id=f"probe-{case.key}",
            case_description=case.case_description,
            cause_type=case.cause_type,
            goal_type=case.goal_type,
            parties=case.parties(),
            defendant_info=case.defendant_info(),
        )
        t0 = time.time()
        try:
            orchestrator.Orchestrator(ctx).run_all()
            cost = time.time() - t0
            problems = problems_of(ctx, case.goal_type)

            print(f"\n  ── {case.label}  {cost:.1f}s")
            for node in required_nodes(case.goal_type):
                d = ctx.dimension_results.get(node, {})
                print(f"       {node:16} {d.get('status', '（未执行）'):8} "
                      f"{(d.get('error') or '')[:40]}")
            print(f"       证据完整度 {ctx.evidence_completeness}  "
                  f"置信度 {ctx.confidence}")

            # 「要钱」路径把回款能力单独摊开：它是唯一一个能把整条业务预期
            # 打成 0 的输入，而报告界面上只显示一个分数，看不出是谁干的。
            if case.goal_type == "要钱":
                profile = ctx.defendant_profile or {}
                locked = _locked_entity(profile)
                flags = (profile.get("metrics") or {}).get("red_flags") or []
                print(f"       回款能力 {ctx.recovery_ability}"
                      f"（查「{case.defendant_name}」→ 锁定「{locked or '未锁定'}」"
                      f" 红灯 {flags}）")
                if locked and locked != case.defendant_name:
                    record(WARN, f"主体匹配 {case.label}",
                           f"查「{case.defendant_name}」实际锁定「{locked}」，"
                           f"回款分可能张冠李戴")
                if ctx.recovery_ability == 0:
                    record(WARN, f"回款能力 {case.label}",
                           f"回款 0（{flags}）→ 业务预期与总分被拉到 0，"
                           f"演示会显示「法律 {ctx.scores.get('legal_feasibility')} 分却建议暂缓」")

            if problems:
                record(FAIL, f"主流程 {case.label}", f"{cost:.1f}s，"
                       + "；".join(problems))
            else:
                record(OK, f"主流程 {case.label}",
                       f"{cost:.1f}s，法律 {ctx.scores.get('legal_feasibility')} / "
                       f"业务 {ctx.scores.get('business_expectation')} / "
                       f"决策 {ctx.scores.get('final')} / "
                       f"{ctx.recommendation.get('recommendation')}")

            run_log.append({"case": case, "cost": cost, "ctx": ctx,
                            "problems": problems})
        except Exception as e:
            record(FAIL, f"主流程 {case.label}", f"{type(e).__name__}: {e}")
            if ARGS.verbose:
                traceback.print_exc()


# ============================================================
section("体检结论")

fails = [r for r in results if r[0] == FAIL]
warns = [r for r in results if r[0] == WARN]
print(f"通过 {len([r for r in results if r[0] == OK])} 项，"
      f"告警 {len(warns)} 项，失败 {len(fails)} 项")

if warns:
    print("\n告警项（不阻断，但演示时会以「降级」形式体现出来）：")
    for _, name, detail in warns:
        print(f"  - {name}：{detail}")

if fails:
    print("\n需要处理的失败项：")
    for _, name, detail in fails:
        print(f"  - {name}：{detail}")
    print("\nMock 模式下的回归测试仍然可用：")
    print("  cd backend && USE_MOCK=True python -m pytest tests/ -q")
else:
    if use_mock or missing_required:
        print("\n主流程未实际运行，以上结论不完整。")
    else:
        print(f"\n真实模式可用（{len(run_log)} 条用例全部跑完 7 节点）。"
              "演示前再按实际脚本跑一遍确认耗时与结论稳定。")

# ------------------------------------------------------------ 存档
if ARGS.log and run_log:
    lines = [
        f"# 真实链路体检存档",
        "",
        f"- 时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 模式：{'Mock' if use_mock else '真实'}",
        f"- 结论：通过 {len([r for r in results if r[0] == OK])} 项，"
        f"告警 {len(warns)} 项，失败 {len(fails)} 项",
        "",
    ]
    for entry in run_log:
        case, ctx, problems = entry["case"], entry["ctx"], entry["problems"]
        lines += [
            f"## {case.label}（{entry['cost']:.1f}s）",
            "",
            f"- 结论：{'通过' if not problems else '未通过 —— ' + '；'.join(problems)}",
            f"- 分数：法律 {ctx.scores.get('legal_feasibility')} / "
            f"业务 {ctx.scores.get('business_expectation')} / "
            f"决策 {ctx.scores.get('final')}",
            f"- 建议：{ctx.recommendation.get('recommendation')}",
            f"- 证据完整度 {ctx.evidence_completeness}，置信度 {ctx.confidence}",
            "",
            "| 节点 | 状态 | 说明 |",
            "| --- | --- | --- |",
        ]
        for node, d in ctx.dimension_results.items():
            lines.append(f"| {node} | {d.get('status')} | {(d.get('error') or '')[:60]} |")
        lines.append("")
    log_path = Path(ARGS.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n存档已写入：{log_path}")

sys.exit(1 if fails else 0)
