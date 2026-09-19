"""
评估结果文档生成器（P2）

- generate_memo：从 CaseContext 生成结构化的评估结果文档（markdown + 一页纸摘要）
  这个 markdown 是结果页「下载评估结果」的内容源，Word 与 PDF 都由它转换而来。

注：函数名沿用 generate_memo 与「备忘录」时期的命名，但对外文案已统一为「评估结果」。
页面层的「决策备忘录」及其定稿快照机制（v1/v2 版本对比）已整体下架，
本生成器与 Report 表被保留：前者供导出复用，后者保留历史数据不再写入。
"""

from typing import Any, Dict, List

from .case_context import CaseContext
from .config import CAUSE_TRADEMARK, QUADRANT_AXIS_MID
from . import damages_wording


def _fmt(v, suffix: str = "") -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.1f}{suffix}"
    return f"{v}{suffix}"


_CN_NUM = "一二三四五六七八九十"


def _cn(n: int) -> str:
    return _CN_NUM[n - 1] if 1 <= n <= len(_CN_NUM) else str(n)


class _Sections:
    """
    章节号按**实际渲染顺序**递增。

    模拟法庭、判例价值、法宝检索三节都是条件渲染，硬编码编号会出现
    「三、证据盘点」直接跳到「五、一页纸摘要」的断号，看着像漏了一章。
    """

    def __init__(self) -> None:
        self.n = 0

    def add(self, lines, title: str) -> None:
        self.n += 1
        lines.append(f"## {_cn(self.n)}、{title}")
        lines.append("")

    def heading(self, title: str) -> str:
        """给不在主流程里拼装的章节用（如法宝增强节）；不渲染就别调，否则会跳号"""
        self.n += 1
        return f"## {_cn(self.n)}、{title}"


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
        # 幂平均（短板主导）而非相乘：写成「×」会让读者以为 78×60 这种算式成立，
        # 实际两维是短板效应合成，量纲也不同（一个是相对分、一个是回款把握度）。
        reasons.append(f"业务预期 {_fmt(business)} 分（判赔规模与回款能力按短板效应合成"
                       + (f"，回款能力 {_fmt(ctx.recovery_ability)}" if ctx.recovery_ability is not None else "")
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
    """评估结果文档 → Markdown（Word / PDF 导出的共同内容源，见 core/docx_export、core/pdf_export）"""
    s = data["scores"]
    rec = data["recommendation"]
    sec = _Sections()
    lines = []
    lines.append(f"# 主诉评估结果：{data['case_name']}")
    lines.append("")
    lines.append(f"> 案由：{data['cause_type']} ｜ 业务目标：{data['goal_type']} ｜ "
                 f"评估模型：v4 二维主诉决策模型")
    lines.append("")
    sec.add(lines, "核心结论")
    lines.append(f"- **主诉决策分**：{_fmt(s.get('final'))}（法律可行性 {_fmt(s.get('legal_feasibility'))} "
                 f"与 业务预期 {_fmt(s.get('business_expectation'))} 的均衡水平）")
    lines.append(f"- **决策象限**：{data['quadrant']}")
    lines.append(f"- **评估置信度**：{_fmt(data['confidence'])}（独立输出，基于证据完整度）")
    if s.get("correction_coeff") and s["correction_coeff"] != 1.0:
        lines.append(f"- **模拟法庭修正系数**：{s['correction_coeff']}")
    lines.append(f"- **建议**：{rec.get('recommendation', '—')}")
    lines.append("")

    # 红线检查
    if data["red_flags"]:
        lines.append("### 红线检查")
        lines.append("")
        for f in data["red_flags"]:
            mark = {"block": "⛔", "warning": "⚠️", "pass": "✅"}.get(f.get("severity"), "·")
            lines.append(f"- {mark} {f.get('rule_name', '')}：{f.get('reason', '')}")
        lines.append("")

    # 维度明细
    sec.add(lines, "维度明细")
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
        if dim["infringement"].get("analysis"):
            lines.append("")
            lines.append(dim["infringement"]["analysis"])
        lines.append("")
    if dim["procedure"]:
        lines.append(f"### 诉讼程序（{_fmt(dim['procedure'].get('score'))} 分）")
        for r in dim["procedure"].get("risks", []):
            lines.append(f"- {r.get('item')}（{r.get('level')}）：{r.get('detail', '')}")
        if dim["procedure"].get("analysis"):
            lines.append("")
            lines.append(dim["procedure"]["analysis"])
        lines.append("")
    if dim["damages"]:
        d = dim["damages"]
        lines.append(f"### 判赔规模（{_fmt(d.get('score'))} 分）")
        # 判赔文案统一走 damages_wording：不出现 P10/P50/P90 这类分位数术语，
        # 英文枚举 scale_support 也在这里翻成中文。该行会随 markdown 进 docx / pdf。
        damages_line = damages_wording.damages_report_line(d)
        if damages_line:
            lines.append(damages_line)
        if d.get("analysis"):
            lines.append("")
            lines.append(d["analysis"])
        lines.append("")
    if dim["precedent"]:
        p = dim["precedent"]
        lines.append(f"### 判例价值（{_fmt(p.get('score'))} 分）")
        lines.append(f"首案指数 {_fmt(p.get('first_case_index'))}；"
                     f"影响力层级：{p.get('influence_level') or '—'}")
        if p.get("analysis"):
            lines.append("")
            lines.append(p["analysis"])
        lines.append("")
    if dim["recovery"]:
        lines.append(f"### 回款能力（{_fmt(dim['recovery'].get('recovery_ability'))} 分）")
        for f in dim["recovery"].get("red_flags", []):
            lines.append(f"- ⚠️ {f}")
        for f in dim["recovery"].get("green_flags", []):
            lines.append(f"- ✅ {f}")
        lines.append("")

    # 证据缺口
    ev = data["evidence"]
    sec.add(lines, "证据盘点与缺口清单")
    lines.append(f"证据完整度 {_fmt(ev['completeness'], '%')}。缺口清单：")
    lines.append("")
    for g in ev["gap_list"]:
        lines.append(f"- [ ] **{g.get('item', '')}**（{g.get('category', '')}，支撑要件：{g.get('element', '')}）"
                     f"—— {g.get('suggestion', g.get('reason', ''))}")
    lines.append("")

    # 模拟法庭
    if data["moot"]["rounds"]:
        sec.add(lines, "模拟法庭压力测试")
        lines.append(f"修正系数：{data['moot']['correction_coeff']}（庭审记录另行导出）")
        lines.append("")

    # 一页纸摘要
    op = data["one_pager"]
    sec.add(lines, "向上汇报一页纸摘要")
    lines.append(f"**结论**：{op['conclusion']}（{op['quadrant']}）")
    lines.append("")
    for r in op["reasons"]:
        lines.append(f"- {r}")
    if op["actions"]:
        lines.append("")
        lines.append("**行动建议**：")
        for a in op["actions"]:
            lines.append(f"- {a}")

    # 北大法宝增强章节（未配置或失败时返回空，不渲染，也不占章节号）
    lines.extend(render_pkulaw_section(data.get("pkulaw") or {}, numbering=sec))

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("> 本评估结果由 Soft IP 主诉评估系统生成，AI 辅助评估结果仅供内部决策参考，不构成正式法律意见。")
    return "\n".join(lines)


def enrich_with_pkulaw(data: Dict[str, Any], case_id: str,
                       cause_type: str = CAUSE_TRADEMARK,
                       goal_type: str = "要钱") -> Dict[str, Any]:
    """
    用北大法宝给备忘录做增强：法条/类案参考 + 引用真实性核验（防幻觉）。

    设计约束（三条都不能破）：
    1. 不进评分链路——只影响报告文本，绝不参与分数计算。v4 的卖点是确定性可复现，
       评分链路一旦依赖外部 API，同一份输入就可能跑出不同分。
    2. 不阻塞——任何异常都吞掉并降级成 warning，报告照常产出。检索是加分项，
       不是必要条件；让它有能力搞挂报告得不偿失。
    3. 无 token 时整段跳过，不留空章节——没配 PKULAW_API_TOKEN 时 pkulaw_api 各函数
       返回 status=skipped 且不发起网络请求，此时 data 里不放任何键，渲染层自然省略。
    """
    from .pkulaw import pkulaw_api, pkulaw_integration

    data["pkulaw"] = {"status": "skipped"}
    try:
        plan = pkulaw_integration.generate_all_queries(
            case_id, cause_type, goal_type, data.get("case_description", ""))
        rights = pkulaw_api.search_for_rights_foundation(cause_type)
        infr = pkulaw_api.search_for_infringement(cause_type)

        if rights.get("status") == "skipped" and infr.get("status") == "skipped":
            return data  # 未配置 token，保持 skipped，调用方据此省略章节

        reference = {
            "laws": (rights.get("laws") or []) + (infr.get("laws") or []),
            "cases": infr.get("cases") or [],
            # 类案是怎么检索出来的也要留下痕迹：哪一档命中几个、哪些顺位被跳过、
            # 为什么跳过。读者据此判断「没有本省类案」到底是查不到还是压根没查。
            "case_search": infr.get("case_search") or {},
            "query_plan": plan,
        }
        verification = pkulaw_api.run_verification_phase(data.get("markdown", ""))

        # 关键修复：检索阶段若返回 error（不抛异常）而 verification 仍回 ok，
        # 旧逻辑会直接拿 verification.status 当最终状态，导致「检索全空却显示成功」
        # 的静默失败。这里把检索错误也透传出来。
        search_errors = [e for e in (rights.get("error"), infr.get("error")) if e]
        vstatus = verification.get("status")
        if vstatus in ("auth_error", "error") or search_errors:
            final_status = vstatus if vstatus in ("auth_error", "error") else "error"
        else:
            final_status = "ok"

        data["pkulaw"] = {
            "status": final_status,
            "error": "; ".join(search_errors) or verification.get("error"),
            "reference": reference,
            "verification": verification,
            "summary": reference and verification.get("_summary") or "",
        }
        pkulaw_integration.save_results(case_id, {
            "reference": reference, "verification": verification})
    except Exception as e:  # 检索失败只降级，不让报告挂掉
        data["pkulaw"] = {"status": "error", "error": str(e)}
        data.setdefault("warnings", []).append(f"北大法宝增强失败（已跳过）：{e}")
    return data


def _ladder_note(search: Dict[str, Any]) -> str:
    """类案检索过程说明：顺位分布 / 相关性 / 哪些顺位没跑及原因 / 数量是否不足。

    必须写出来的理由：「命中 3 个类案」这个数字本身说明不了任何问题——
    可能是没有更权威的类案，也可能是因为不知道被告在哪个省所以没检索本省高院。
    """
    if not search:
        return ""
    parts = []
    counts = search.get("tier_counts") or {}
    if counts:
        parts.append("检索顺位分布：" + "、".join(f"{k} {v} 个" for k, v in counts.items()))
    dropped = int(search.get("dropped_irrelevant") or 0)
    if dropped:
        parts.append(f"其中 {dropped} 个案由相关性较弱，未计入达标数")
    if search.get("insufficient_note"):
        parts.append(search["insufficient_note"])
    for s in search.get("skipped") or []:
        parts.append(f"顺位{s.get('code')}未执行：{s.get('reason')}")
    if search.get("budget_exhausted"):
        parts.append("已达单次检索预算上限，后续顺位未继续")
    if search.get("time_exhausted"):
        parts.append("已达单次检索时限，后续顺位未继续")
    if not parts:
        return ""
    return "类案检索说明：" + "；".join(parts) + "。"


def render_pkulaw_section(pk: Dict[str, Any], numbering=None) -> List[str]:
    """
    法宝检索与核验结果 → markdown 行。返回空 list 表示这一节不渲染。

    numbering 传 _Sections 时章节号顺延主流程；不传则不带编号
    （调用方只拿它判断有无内容时也用这种）。
    """
    status = pk.get("status")
    if status in (None, "skipped"):
        return []  # 未配置 token：不留空章节（设计约束3）

    if status in ("error", "auth_error"):
        # 检索/核验失败也要让用户看见，而不是静默消失（问题3：北大法宝调用失败）。
        err = pk.get("error") or "北大法宝增强未成功，但未记录具体原因"
        title = "法律检索与引用核验（北大法宝）"
        lines = [numbering.heading(title) if numbering is not None else f"## {title}", ""]
        lines.append(f"> ⚠️ **北大法宝增强未成功**：{err}")
        lines.append("")
        lines.append("_（本报告未包含法宝法条/类案参考，不影响已完成的评估结论；"
                     "可检查 PKULAW_API_TOKEN 配置后重新生成报告。）_")
        lines.append("")
        return lines

    title = "法律检索与引用核验（北大法宝）"
    lines = [numbering.heading(title) if numbering is not None else f"## {title}", ""]
    ref = pk.get("reference") or {}

    laws = ref.get("laws") or []
    if laws:
        lines += ["### 相关法条", ""]
        for law in laws[:8]:
            title = law.get("title", "")
            content = (law.get("content") or "").strip()
            lines.append(f"- **{title}**" + (f"：{content[:200]}" if content else ""))
        lines.append("")

    cases = ref.get("cases") or []
    if cases:
        lines += ["### 类案参考", ""]
        for i, c in enumerate(cases[:5], 1):
            tier = f"［{c['tier_label']}］" if c.get("tier_label") else ""
            lines.append(f"{i}. {tier}**{c.get('title', '')}**"
                         + (f"（{c.get('court', '')}）" if c.get("court") else ""))
            if c.get("summary"):
                lines.append(f"   - {c['summary'][:160]}")
        note = _ladder_note(ref.get("case_search") or {})
        if note:
            lines += ["", f"_{note}_"]
        lines.append("")

    ver = pk.get("verification") or {}
    if ver.get("_summary"):
        lines += ["### 引用真实性核验", "", ver["_summary"], ""]

    return lines


def generate_memo(case_name: str, ctx: CaseContext,
                  pkulaw: bool = False) -> Dict[str, Any]:
    """生成完整备忘录：结构化数据 + markdown（pkulaw=True 时附带法宝增强）"""
    data = build_memo_data(case_name, ctx)
    if not pkulaw:
        data["markdown"] = render_memo_markdown(data)
        return data

    # 核验的输入是报告正文，而正文又要把核验结果渲染进去——循环依赖。
    # 解法是渲染两遍：第一遍产出不含法宝章节的正文供核验，第二遍再带上结果。
    # 第一遍只是纯文本拼装，成本可忽略。
    data["markdown"] = render_memo_markdown(data)
    data = enrich_with_pkulaw(data, ctx.case_id, ctx.cause_type, ctx.goal_type)
    if render_pkulaw_section(data.get("pkulaw") or {}):
        data["markdown"] = render_memo_markdown(data)
    return data
