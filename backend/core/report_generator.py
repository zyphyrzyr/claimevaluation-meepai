"""
决策备忘录生成器（P2）
- generate_memo：从 CaseContext 生成结构化决策备忘录（markdown + 一页纸摘要）
- 报告版本化：定稿快照存 Report 表（version 递增，v1/v2 并存）
输出物形态（§8）：决策备忘录 + 向上汇报一页纸摘要 + 庭审记录
"""

from typing import Any, Dict

from .case_context import CaseContext
from .config import QUADRANT_AXIS_MID


def _fmt(v, suffix: str = "") -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.1f}{suffix}"
    return f"{v}{suffix}"


def quadrant(legal: float, business: float) -> str:
    """
    二维四象限。

    注意：这里的中线是「法律轴 / 业务轴各自的中线」，语义上不同于
    scoring.generate_recommendation 用的「总分档位线」，两者独立命名
    （QUADRANT_AXIS_MID vs SCORE_THRESHOLD_GO），不可合并为一个常量。

    取值之所以与 SCORE_THRESHOLD_GO 对齐：幂平均恒满足
    min(x,y) <= M_p(x,y) <= max(x,y)，故两轴均 >= 78 时总分必 >= 78，
    不会出现「四象限判双优、决策卡片判补短板」的同屏矛盾。
    """
    if legal is None or business is None:
        return "—"
    hi_l, hi_b = QUADRANT_AXIS_MID, QUADRANT_AXIS_MID
    if legal >= hi_l and business >= hi_b:
        return "双优区（强推起诉）"
    if legal >= hi_l:
        return "可行低回报区（可诉，控制成本）"
    if business >= hi_b:
        return "高回报风险区（先补证据短板）"
    return "双低区（暂缓，评估替代方案）"


def build_memo_data(case_name: str, ctx: CaseContext) -> Dict[str, Any]:
    """结构化备忘录数据（前端渲染用，同时用于生成 markdown）"""
    scores = ctx.scores or {}
    rec = ctx.recommendation or {}
    dim = ctx.dimension_results or {}

    def node_result(node):
        d = dim.get(node, {})
        return d.get("result") or {}, d.get("status")

    rights, _ = node_result("rights")
    infr, _ = node_result("infringement")
    proc, _ = node_result("procedure")
    damages, _ = node_result("damages")
    precedent, _ = node_result("precedent")
    recovery, _ = node_result("recovery")

    return {
        "case_name": case_name,
        "cause_type": ctx.cause_type,
        "goal_type": ctx.goal_type,
        "scores": {
            "legal_feasibility": scores.get("legal_feasibility"),
            "business_expectation": scores.get("business_expectation"),
            "final": scores.get("final"),
            "correction_coeff": ctx.correction_coeff,
        },
        "confidence": ctx.confidence,
        "quadrant": quadrant(scores.get("legal_feasibility"), scores.get("business_expectation")),
        "recommendation": rec,
        "red_flags": ctx.red_flags,
        "evidence": {
            "completeness": ctx.evidence_completeness,
            "gap_list": ctx.gap_list,
            "extra_evidence": ctx.extra_evidence,
        },
        "dimensions": {
            "rights": rights, "infringement": infr, "procedure": proc,
            "damages": damages, "precedent": precedent, "recovery": recovery,
        },
        "moot": {
            "rounds": ctx.moot_transcript,
            "correction_coeff": ctx.correction_coeff,
        },
        "audit_trail": ctx.audit_trail,
        "one_pager": build_one_pager(case_name, ctx),
    }


def build_one_pager(case_name: str, ctx: CaseContext) -> Dict[str, Any]:
    """向上汇报一页纸摘要：结论 + 二维评分 + 三条核心理由 + 行动建议"""
    scores = ctx.scores or {}
    rec = ctx.recommendation or {}
    reasons = []

    # 理由 1：法律可行性
    legal = scores.get("legal_feasibility")
    if legal is not None:
        reasons.append(f"法律可行性 {_fmt(legal)} 分：权利基础与侵权认定整体"
                       + ("稳固" if legal >= 70 else "存在短板"))
    # 理由 2：业务预期
    business = scores.get("business_expectation")
    if ctx.goal_type == "要钱" and business is not None:
        reasons.append(f"业务预期 {_fmt(business)} 分（判赔规模 × 回款能力"
                       + (f" {_fmt(ctx.recovery_ability)}" if ctx.recovery_ability is not None else "")
                       + "）")
    elif business is not None:
        reasons.append(f"业务预期 {_fmt(business)} 分（判例价值维度）")
    # 理由 3：证据缺口
    gap_count = len(ctx.gap_list or [])
    reasons.append(f"证据完整度 {_fmt(ctx.evidence_completeness)}%，"
                   + (f"存在 {gap_count} 项缺口（置信度 {_fmt(ctx.confidence)}）" if gap_count else "无重大缺口"))

    return {
        "conclusion": rec.get("recommendation", "评估未完成"),
        "level": rec.get("level", ""),
        "scores": {
            "legal_feasibility": legal,
            "business_expectation": business,
            "final": scores.get("final"),
            "confidence": ctx.confidence,
        },
        "quadrant": quadrant(legal, business),
        "reasons": reasons,
        "actions": rec.get("actions", []) or rec.get("missing_dimensions", []),
    }


# ============================================================
# Markdown 渲染
# ============================================================

def render_memo_markdown(data: Dict[str, Any]) -> str:
    """决策备忘录 → Markdown（Word 导出的内容源，P4 接 html-to-docx）"""
    s = data["scores"]
    rec = data["recommendation"]
    lines = []
    lines.append(f"# 主诉评估决策备忘录：{data['case_name']}")
    lines.append("")
    lines.append(f"> 案由：{data['cause_type']} ｜ 业务目标：{data['goal_type']} ｜ "
                 f"评估模型：v4 二维主诉决策模型")
    lines.append("")
    lines.append("## 一、核心结论")
    lines.append("")
    lines.append(f"- **主诉决策分**：{_fmt(s.get('final'))}（法律可行性 {_fmt(s.get('legal_feasibility'))} "
                 f"与 业务预期 {_fmt(s.get('business_expectation'))} 的均衡水平）")
    lines.append(f"- **决策象限**：{data['quadrant']}")
    lines.append(f"- **评估置信度**：{_fmt(data['confidence'])}（独立输出，基于证据完整度）")
    if s.get("correction_coeff") and s["correction_coeff"] != 1.0:
        lines.append(f"- **模拟法庭修正系数**：{s['correction_coeff']}")
    lines.append(f"- **建议**：{rec.get('recommendation', '—')}")
    lines.append("")

    # 硬门禁
    if data["red_flags"]:
        lines.append("### 硬门禁检查")
        lines.append("")
        for f in data["red_flags"]:
            mark = {"block": "⛔", "warning": "⚠️", "pass": "✅"}.get(f.get("severity"), "·")
            lines.append(f"- {mark} {f.get('rule_name', '')}：{f.get('reason', '')}")
        lines.append("")

    # 维度明细
    lines.append("## 二、维度明细")
    lines.append("")
    dim = data["dimensions"]
    if dim["rights"]:
        lines.append(f"### 权利基础（{_fmt(dim['rights'].get('score'))} 分）")
        lines.append(dim["rights"].get("analysis", ""))
        lines.append("")
    if dim["infringement"]:
        lines.append(f"### 侵权认定（{_fmt(dim['infringement'].get('score'))} 分）")
        if dim["infringement"].get("elements"):
            for el in dim["infringement"]["elements"]:
                lines.append(f"- **{el.get('name')}**（{el.get('status')}）：{el.get('analysis', '')}")
        lines.append("")
    if dim["procedure"]:
        lines.append(f"### 诉讼程序（{_fmt(dim['procedure'].get('score'))} 分）")
        for r in dim["procedure"].get("risks", []):
            lines.append(f"- {r.get('item')}（{r.get('level')}）：{r.get('detail', '')}")
        lines.append("")
    if dim["damages"]:
        d = dim["damages"]
        lines.append(f"### 判赔规模（{_fmt(d.get('score'))} 分）")
        lines.append(f"类案判赔区间：P10 {_fmt(d.get('p10'), ' 万')} / P50 {_fmt(d.get('p50'), ' 万')} / "
                     f"P90 {_fmt(d.get('p90'), ' 万')}；回报倍数 {_fmt(d.get('return_multiple'))}")
        lines.append("")
    if dim["recovery"]:
        lines.append(f"### 回款能力（{_fmt(dim['recovery'].get('recovery_ability'))} 分）")
        lines.append("")

    # 证据缺口
    ev = data["evidence"]
    lines.append("## 三、证据盘点与缺口清单")
    lines.append("")
    lines.append(f"证据完整度 {_fmt(ev['completeness'], '%')}。缺口清单：")
    lines.append("")
    for g in ev["gap_list"]:
        lines.append(f"- [ ] **{g.get('item', '')}**（{g.get('category', '')}，支撑要件：{g.get('element', '')}）"
                     f"—— {g.get('suggestion', g.get('reason', ''))}")
    lines.append("")

    # 模拟法庭
    if data["moot"]["rounds"]:
        lines.append("## 四、模拟法庭压力测试")
        lines.append("")
        lines.append(f"修正系数：{data['moot']['correction_coeff']}（庭审记录另行导出）")
        lines.append("")

    # 一页纸摘要
    op = data["one_pager"]
    lines.append("## 五、向上汇报一页纸摘要")
    lines.append("")
    lines.append(f"**结论**：{op['conclusion']}（{op['quadrant']}）")
    lines.append("")
    for r in op["reasons"]:
        lines.append(f"- {r}")
    if op["actions"]:
        lines.append("")
        lines.append("**行动建议**：")
        for a in op["actions"]:
            lines.append(f"- {a}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("> 本备忘录由 Soft IP 主诉评估系统生成，AI 辅助评估结果仅供内部决策参考，不构成正式法律意见。")
    return "\n".join(lines)


def generate_memo(case_name: str, ctx: CaseContext) -> Dict[str, Any]:
    """生成完整备忘录：结构化数据 + markdown"""
    data = build_memo_data(case_name, ctx)
    data["markdown"] = render_memo_markdown(data)
    return data
