"""
L0 法官归纳结构化结果落库（moot_judge）

此前 run_embedded 回写 ctx 只存 transcript + correction_coeff，法官归纳正文与
结构化明细（weak_points/系数推导等）只在 SSE moot_finished 里推给直播，
刷新页面后历史回填拿不到——前端只能显示「历史记录没存正文」的不实提示。

这里钉住：跑完内嵌庭审后，ctx.moot_judge 必须携带法官归纳全文与结构化明细，
这样历史记录才能完整回显判决书，而不是只剩一个系数。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("USE_MOCK", "True")

from core import moot_service
from core.case_context import CaseContext


def test_embedded_run_persists_judge_result():
    ctx = CaseContext(
        case_id="moot-judge-probe",
        case_description="被告在电商平台销售带有我方注册商标标识的商品，已公证取证。",
        cause_type="商标侵权",
        goal_type="要钱",
    )
    # 消费生成器到底（mock 模式，不调真实 LLM）
    for _ev in moot_service.run_embedded(ctx):
        pass

    # 法官归纳正文已落库
    assert ctx.moot_judge.get("judge_summary"), "法官归纳正文未落库"
    # 结构化明细一并落库：系数推导 + 来源 + 抗辩强度
    assert ctx.moot_judge.get("coefficient_source") in ("derived", "model", "fallback")
    assert "coefficient_detail" in ctx.moot_judge
    assert ctx.moot_judge.get("defense_strength", 0) > 0
    # transcript 第 5 轮仍是法官归纳正文（与前向兼容一致）
    judge_round = next(r for r in ctx.moot_transcript if r["role"] == "judge")
    assert judge_round["content"]


def test_moot_judge_survives_roundtrip():
    """moot_judge 经 to_dict/from_dict 往返后不丢——历史接口靠它回显"""
    ctx = CaseContext(case_id="roundtrip", cause_type="商标侵权", goal_type="要钱")
    for _ev in moot_service.run_embedded(ctx):
        pass
    restored = CaseContext.from_dict(ctx.to_dict())
    assert restored.moot_judge.get("judge_summary") == ctx.moot_judge["judge_summary"]
    assert restored.moot_judge.get("coefficient_source") == ctx.moot_judge["coefficient_source"]
