"""
诊断 orchestrator.run_all：把每个节点的 status / error / result 关键字段全 dump 出来，
定位「分数为何全 None」。仅排查用。
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

ctx = CaseContext(
    case_id="diag-1",
    case_description=(
        "原告持有相关权利（商标侵权），被告在电商平台销售被诉侵权商品，"
        "已做公证取证与时间戳存证，涉诉金额约 50 万元。"),
    cause_type="商标侵权",
    goal_type="要钱",
    parties=[{"role": "plaintiff", "name": "我方权利人公司", "party_type": "法人"},
             {"role": "defendant", "name": "被诉店铺", "party_type": "个体工商户"}],
)

orchestrator.Orchestrator(ctx).run_all()

print("=" * 66)
print("各节点结果明细")
print("=" * 66)
for node, d in ctx.dimension_results.items():
    status = d.get("status")
    err = d.get("error")
    result = d.get("result") or {}
    score = result.get("score") if isinstance(result, dict) else None
    extra = ""
    if node == "red_gate":
        extra = f" blocked={d.get('blocked')} hits_keys={[h.get('rule') for h in (d.get('hits') or [])]}"
    if node == "recovery":
        extra = f" recovery_ability={ctx.recovery_ability}"
    if node == "evidence_review":
        extra = f" matrix={json.dumps(d.get('result') or {}, ensure_ascii=False)[:200]}"
    print(f"[{status:7}] {node:16} score={score} err={err}{extra}")

print()
print("scores:", json.dumps(ctx.scores, ensure_ascii=False))
print("recommendation:", ctx.recommendation)
print("red_flags:", json.dumps(ctx.red_flags, ensure_ascii=False)[:400])
