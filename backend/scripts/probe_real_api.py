"""
真实 API 探针（接 key 后的一键体检）

用法：
    cd backend && python scripts/probe_real_api.py

逐项体检 4 个外部依赖与真实模式主流程，单项失败不中断，最后给一份诊断结论。
目的是把「接上 key 之后到底哪里不通」从猜测变成一份清单。

与 pytest 的关系：本脚本打真实网络、花钱、耗时长，不能进回归测试；
tests/test_l2_real_path.py 用假 HTTP 覆盖同一条代码路径，那个是零成本的版本。
"""
import os
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 环境里若配了 HTTP 代理，请求国内 API 与 localhost 都可能被挡成 502。
# 探针只打公网外部服务，不碰本地端口，所以直接放掉代理最省事。
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
           "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)

from core.config import get_runtime_settings, LLM_FAST_MODEL, LLM_STRONG_MODEL

OK, WARN, FAIL = "  [OK]  ", "  [WARN]", "  [FAIL]"
results = []


def record(status: str, name: str, detail: str = "") -> None:
    results.append((status, name, detail))
    print(f"{status} {name}" + (f" — {detail}" if detail else ""))


def section(title: str) -> None:
    print(f"\n{'=' * 66}\n{title}\n{'=' * 66}")


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
    ("DEEPSEEK_API_KEY", "LLM 评估节点", True),
    ("QCC_API_TOKEN", "企查查被告画像 / 回款能力", False),
    ("PKULAW_API_TOKEN", "北大法宝检索与引用核验", False),
    ("SILICONFLOW_API_KEY", "bge-m3 向量化（RAG 知识库）", False),
]
missing_required = []
for key, purpose, required in KEYS:
    val = (settings.get(key.lower()) or "").strip()
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

    for label, model in (("普通模型", LLM_FAST_MODEL), ("强模型", LLM_STRONG_MODEL)):
        t0 = time.time()
        try:
            text = llm_gateway.call_text(
                "你是助手。", "只回复两个字：正常", node=None, max_tokens=16)
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

if (settings.get("pkulaw_api_token") or "").strip():
    from core.pkulaw import pkulaw_api
    try:
        res = pkulaw_api.search_for_rights_foundation()
        if res.get("status") == "skipped":
            record(WARN, "北大法宝", "未生效（token 未被识别）")
        else:
            record(OK, "北大法宝", f"法条 {len(res.get('laws', []))} 条")
    except Exception as e:
        record(FAIL, "北大法宝", f"{type(e).__name__}: {e}")
else:
    record(WARN, "北大法宝", "未配置 token，跳过")

if (settings.get("qcc_api_token") or "").strip():
    from core import qcc_api
    try:
        profile = qcc_api.search_for_financial_qcc_full({"company_name": "测试企业"})
        record(OK, "企查查",
               f"回款能力 {profile.get('metrics', {}).get('recovery_probability')}")
    except Exception as e:
        record(FAIL, "企查查", f"{type(e).__name__}: {e}")
else:
    record(WARN, "企查查", "未配置 token，跳过（回款能力会标 failed）")


# ============================================================
section("四、真实模式主流程")

if use_mock:
    record(WARN, "主流程", "Mock 模式下跳过；设 USE_MOCK=False 后重跑本脚本")
elif missing_required:
    record(FAIL, "主流程", f"缺少 {missing_required}，跳过")
else:
    from core.case_context import CaseContext
    from core import orchestrator

    CASES = [
        ("商标侵权 / 要钱", "商标侵权", "要钱"),
        ("著作权侵权 / 要名", "著作权侵权", "要名"),
    ]
    for label, cause, goal in CASES:
        ctx = CaseContext(
            case_id=f"probe-{cause}",
            case_description=(
                f"原告持有相关权利（{cause}），被告在电商平台销售被诉侵权商品，"
                "已做公证取证与时间戳存证，涉诉金额约 50 万元。"),
            cause_type=cause,
            goal_type=goal,
            parties=[{"role": "plaintiff", "name": "我方权利人公司", "party_type": "法人"},
                     {"role": "defendant", "name": "被诉店铺", "party_type": "个体工商户"}],
        )
        t0 = time.time()
        try:
            orchestrator.Orchestrator(ctx).run_all()
            cost = time.time() - t0
            failed = {k: (v.get("error") or "")[:40]
                      for k, v in ctx.dimension_results.items()
                      if v.get("status") == "failed"}
            if failed:
                record(FAIL, f"主流程 {label}", f"{cost:.1f}s，失败节点：{failed}")
            else:
                record(OK, f"主流程 {label}",
                       f"{cost:.1f}s，法律 {ctx.scores.get('legal_feasibility')} / "
                       f"业务 {ctx.scores.get('business_expectation')} / "
                       f"决策 {ctx.scores.get('final')} / "
                       f"{ctx.recommendation.get('recommendation')}")
        except Exception as e:
            record(FAIL, f"主流程 {label}", f"{type(e).__name__}: {e}")
            if "-v" in sys.argv:
                traceback.print_exc()


# ============================================================
section("体检结论")

fails = [r for r in results if r[0] == FAIL]
warns = [r for r in results if r[0] == WARN]
print(f"通过 {len([r for r in results if r[0] == OK])} 项，"
      f"告警 {len(warns)} 项，失败 {len(fails)} 项")

if fails:
    print("\n需要处理的失败项：")
    for _, name, detail in fails:
        print(f"  - {name}：{detail}")
    print("\nMock 模式下的回归测试仍然可用：")
    print("  cd backend && USE_MOCK=True python -m pytest tests/ -q")
    sys.exit(1)

print("\n真实模式可用。演示前建议先跑一遍完整评估确认耗时可接受。")
