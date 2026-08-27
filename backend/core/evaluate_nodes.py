"""
评估节点群（LLM 单点节点，§6.2）
权利基础 / 侵权认定 / 诉讼程序 / 判赔规模 / 判例价值
- 每个节点：单次 LLM 调用 + 强制 JSON 输出，无自主决策
- 输入统一含：案情 + 证据矩阵/缺口 + 用户观点（§5.5 双通道分层指令）
- P1 为商标案由完整 prompt；著作权/不正当竞争按案由切换框架描述，完整三案由 prompt 套件在 P3 补齐
"""

from typing import Any, Dict

from .case_context import CaseContext
from .config import CAUSE_TRADEMARK, RIGHTS_RED_LINE
from .llm_gateway import call_json

_SYSTEM = (
    "你是资深知识产权律师，站在原告视角做诉前评估。请严格按 JSON 格式返回。"
    "【权威法律依据】为事实基准；【用户经验与观点】为视角参考。"
    "二者矛盾时，事实以权威来源/证据材料为准并显式说明，判断可采纳用户观点但须标注。"
)


def _evidence_brief(ctx: CaseContext) -> str:
    if not ctx.evidence_matrix:
        return "（未进行证据盘点）"
    lines = [f"证据完整度：{ctx.evidence_completeness}%"]
    for gap in ctx.gap_list[:6]:
        lines.append(f"- 缺口：[{gap['category']}] {gap['item']}（{gap['status']}：{gap['reason']}）")
    return "\n".join(lines)


def _base(ctx: CaseContext) -> str:
    injected = ""
    if ctx.injected_knowledge:
        from .knowledge import build_injection_text
        injected = f"""
## 知识库参考材料（用户勾选注入）
{build_injection_text(ctx.injected_knowledge)}
"""
    return f"""## 案情描述
{ctx.case_description[:3000]}

## 案由
{ctx.cause_type}

## 证据盘点情况
{_evidence_brief(ctx)}

## 用户经验与观点
{ctx.viewpoints_text() or "（无）"}
{injected}"""


def evaluate_rights(ctx: CaseContext, use_mock: bool = False) -> Dict[str, Any]:
    """子维度 1.1 权利基础（<60 触发红灯）"""
    if use_mock:
        from .mock import mock_rights
        return mock_rights()

    focus = {
        "商标侵权": "商标是否有效注册、是否连续三年使用（防撤三）、核定范围是否覆盖侵权、是否驰名、无效/撤销风险",
        "著作权侵权": "独创性程度、权利归属链条（职务作品/委托创作/转让）、是否登记、创作留痕",
        "不正当竞争": "是否构成'有一定影响'的商品名称/包装装潢/企业名称、知名度证据充分性",
    }.get(ctx.cause_type, "")
    prompt = f"""{_base(ctx)}

## 评估任务
评估原告权利基础的稳固程度，重点关注：{focus}

## 返回 JSON
{{
  "score": 0-100 的整数,
  "analysis": "综合分析（200字内）",
  "strengths": ["权利基础优势1", "优势2"],
  "risks": ["风险点1", "风险点2"]
}}"""
    result = call_json(_SYSTEM, prompt, node="rights")
    if "error" not in result:
        result["red_light"] = (result.get("score") or 0) < RIGHTS_RED_LINE
    return result


def evaluate_infringement(ctx: CaseContext, use_mock: bool = False) -> Dict[str, Any]:
    """子维度 1.2 侵权认定（构成要件逐一认定）"""
    if use_mock:
        from .mock import mock_infringement
        return mock_infringement()

    elements = {
        "商标侵权": ["商标性使用", "商品/服务相同或类似", "商标相同或近似", "混淆可能性", "是否正当使用"],
        "著作权侵权": ["接触", "实质性相似", "是否落入合理使用"],
        "不正当竞争": ["混淆行为构成", "商业诋毁构成（如适用）", "互联网专条构成（如适用）"],
    }.get(ctx.cause_type, [])
    rights_analysis = (ctx.dimension_results.get("rights", {}).get("result") or {}).get("analysis", "")
    prompt = f"""{_base(ctx)}

## 权利基础评估结论（前序节点）
{rights_analysis or "（未提供）"}

## 评估任务
逐一认定以下构成要件（满足/存疑/不满足），并给出综合评分：{"、".join(elements)}

## 返回 JSON
{{
  "score": 0-100 的整数,
  "elements": [{{"name": "要件名", "status": "满足|存疑|不满足", "analysis": "一句话"}}],
  "analysis": "综合分析（200字内）"
}}"""
    return call_json(_SYSTEM, prompt, node="infringement")


def evaluate_procedure(ctx: CaseContext, use_mock: bool = False) -> Dict[str, Any]:
    """子维度 1.3 诉讼程序"""
    if use_mock:
        from .mock import mock_procedure
        return mock_procedure()

    prompt = f"""{_base(ctx)}

## 评估任务
评估诉讼程序可行性：① 诉讼时效（3年，是否临近届满/有无中断中止事由）② 管辖与仲裁（是否存在有效仲裁协议、哪个法院对原告最有利）③ 主体适格（原告是否适格权利人、被告是否明确、是否需追加共同被告）④ 前置程序（行政前置、通知-删除要求）

## 返回 JSON
{{
  "score": 0-100 的整数,
  "risks": [{{"item": "评估项", "level": "high|medium|low|none", "detail": "一句话"}}],
  "analysis": "综合分析（200字内）"
}}"""
    return call_json(_SYSTEM, prompt, node="procedure")


def evaluate_damages(ctx: CaseContext, use_mock: bool = False) -> Dict[str, Any]:
    """判赔规模（要钱目标）：P10/P50/P90 + 回报倍数 + 侵权规模支撑度"""
    if use_mock:
        from .mock import mock_damages
        return mock_damages()

    prompt = f"""{_base(ctx)}

## 评估任务
估算本案判赔规模（0-100 相对评分），综合三方面：
① 判赔金额量级——估算判赔金额概率分布（P10/P50/P90，单位：万元），结合法定赔偿区间与类案判赔水平
② 回报倍数——P50 判赔额 ÷ 预估总成本（律师费+诉讼费+公证费等，按 8-15 万估）
③ 侵权规模支撑度——案情中的销量/店铺规模等能否支撑高判赔

## 返回 JSON
{{
  "score": 0-100 的整数,
  "p10": 数值（万元）, "p50": 数值（万元）, "p90": 数值（万元）,
  "return_multiple": 数值（回报倍数）,
  "scale_support": "high|medium|low",
  "analysis": "分析（200字内）"
}}"""
    return call_json(_SYSTEM, prompt, node="damages")


def evaluate_precedent(ctx: CaseContext, use_mock: bool = False) -> Dict[str, Any]:
    """判例价值（要名目标）"""
    if use_mock:
        from .mock import mock_precedent
        return mock_precedent()

    prompt = f"""{_base(ctx)}

## 评估任务
评估本案的判例价值（0-100），综合四方面：
① 首案潜力——是否无同类在先判例
② 指导性案例潜力——比对最高法指导性案例/典型案例遴选标准
③ 行业震慑效应——胜诉后对行业其他侵权者的威慑力
④ 规则明晰价值——能否推动模糊法律规则的明确化

## 返回 JSON
{{
  "score": 0-100 的整数,
  "first_case_index": 0-100 的首案指数,
  "influence_level": "行业级|区域级|个案级",
  "analysis": "分析（200字内）"
}}"""
    return call_json(_SYSTEM, prompt, node="precedent")
