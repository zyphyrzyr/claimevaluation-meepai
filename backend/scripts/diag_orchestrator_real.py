"""
真实链路端到端验证：带完整证据矩阵的商标「要钱」案，跑完整 orchestrator.run_all。
目的：证明 Deepseek LLM 5 个评分节点（evidence_review/rights/infringement/procedure/
damages/judge）在真实模式下能产出真实分数，而非被红线门禁误拦成全 None。
QCC/北大法宝鉴权失败应被诚实标记（recovery_ability=None / laws=[]+status=error），
不再是假 50% 或假 0 条。
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)

from core.case_context import CaseContext
from core import orchestrator

EVIDENCE = """
【证据1-权利基础】原告《商标注册证》（注册号：第12345678号，核定使用商品第25类服装），
核准注册日2019-03-21，有效期至2029-03-20，商标状态有效，原告系该商标专用权人。
【证据2-权利基础】国家知识产权局商标档案查询页，显示该商标无撤销/无效宣告记录。
【证据3-侵权固定】某公证处《公证书》（2026-05-12出具，文号(2026)京某证经字第0888号），
对被告在电商平台开设的店铺销售被诉侵权服装的页面、商品链接、销量数据进行了网页公证保全。
【证据4-侵权固定】联合信任时间戳服务中心可信时间戳认证证书，固化被告侵权商品详情页截图，
取证时间2026-05-10。
【证据5-侵权固定】被告商品与原告商标对比图，被诉标识与原告注册商标在视觉上基本无差别，
使用于同种商品。
【证据6-损害赔偿】原告同类正品近一年销售发票及被告店铺标价页面，拟证明侵权获利/原告损失规模约80万元。
"""

ctx = CaseContext(
    case_id="real-e2e-1",
    case_description=(
        "原告持有第12345678号注册商标（第25类服装），被告在电商平台开设店铺，"
        "销售使用与原告商标视觉基本无差别标识的服装，已通过公证+时间戳完成侵权页面取证，"
        "拟主张赔偿约80万元。"),
    cause_type="商标侵权",
    goal_type="要钱",
    parties=[
        {"role": "plaintiff", "name": "原告服饰有限公司", "party_type": "法人"},
        {"role": "defendant", "name": "被告服装店", "party_type": "企业"},
    ],
    defendant_info={
        "name": "被告服装店",
        "type": "enterprise",
        "evidence_texts": EVIDENCE,
    },
)

orchestrator.Orchestrator(ctx).run_all()

print("=" * 66)
print("各节点结果明细（真实链路）")
print("=" * 66)
for node, d in ctx.dimension_results.items():
    status = d.get("status")
    err = d.get("error")
    result = d.get("result") or {}
    score = result.get("score") if isinstance(result, dict) else None
    extra = ""
    if node == "recovery":
        extra = f" recovery_ability={ctx.recovery_ability}"
    if node == "evidence_review":
        extra = f" completeness={ctx.evidence_completeness}"
    if node == "red_gate":
        blk = d.get("blocked")
        extra = f" blocked={blk}"
    print(f"[{status:7}] {node:16} score={score} err={err}{extra}")

print()
print("scores:", json.dumps(ctx.scores, ensure_ascii=False))
print("recommendation:", ctx.recommendation)
print("red_flags:", json.dumps(
    [{"rule": h.get("rule_code"), "sev": h.get("severity")} for h in ctx.red_flags],
    ensure_ascii=False))
