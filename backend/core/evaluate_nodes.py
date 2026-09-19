"""
评估节点群（LLM 单点节点，§6.2）
权利基础 / 侵权认定 / 诉讼程序 / 判赔规模 / 判例价值
- 每个节点：单次 LLM 调用 + 强制 JSON 输出，无自主决策
- 输入统一含：案情 + 证据矩阵/缺口 + 用户观点（§5.5 双通道分层指令）
- 三案由在「赔偿 / 时效 / 管辖 / 前置程序 / 抗辩 / 判例价值」上的实质差异由
  legal_basis 模块提供事实基准，随案由注入对应节点的 prompt。
- 节点 prompt 里的特征词被 tests/test_l2_real_path.py 用来分派假响应，
  改措辞前先看那里的 PROMPT_MARKERS。
"""

from typing import Any, Dict

from .case_context import CaseContext
from .config import CAUSE_TRADEMARK, RIGHTS_RED_LINE
from .legal_basis import (damages_clause, precedent_clause,
                          procedure_clause)
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


def _format_pkulaw_block(pkulaw: Dict[str, Any]) -> str:
    """把北大法宝检索结果压成 prompt 可用的文本块；无数据返回空串。

    仅在确有法条/类案时注入，避免把「检索失败」的空壳塞进 prompt。
    失败原因由调用方在 trace 与结果里单独显式露出。
    """
    if not pkulaw:
        return ""
    laws = pkulaw.get("laws") or []
    cases = pkulaw.get("cases") or []
    if not laws and not cases:
        return ""
    lines = ["## 北大法宝检索依据（外部法律数据库，作为评判的外部参照，不替代权威来源）"]
    for l in laws[:5]:
        if not isinstance(l, dict):
            continue
        title = l.get("title", "")
        content = (l.get("content") or "")[:220]
        lines.append(f"- 法条：{title} —— {content}")
    for c in cases[:4]:
        if not isinstance(c, dict):
            continue
        title = c.get("title", "")
        court = c.get("court", "")
        ahao = c.get("ahao", "")
        summary = (c.get("summary") or "")[:200]
        # 顺位标记告诉模型这个案例的权威等级（最高法指导性案例 vs 本院判例），
        # 模型据此决定该给多大权重；没有它，四顺位检索的成果在 prompt 里就退化成一串平铺的名称
        tier = c.get("tier_label", "")
        tag = f"［{tier}］" if tier else ""
        lines.append(f"- 类案{tag}：{title}（{court} {ahao}） —— {summary}")
    return "\n".join(lines)


def _pkulaw_payload(pkulaw: Dict[str, Any]) -> Dict[str, Any]:
    """结构化保存北大法宝检索结果，供前端「外部依据」板块渲染。"""
    return {
        "status": pkulaw.get("status", "ok"),
        "error": pkulaw.get("error"),
        "summary": pkulaw.get("_summary", ""),
        "laws": pkulaw.get("laws") or [],
        "cases": pkulaw.get("cases") or [],
    }


def _qcc_facts(ctx: CaseContext) -> Dict[str, Any]:
    """取被告画像里的结构化工商事实；没有就返回 {}。

    只认 metrics.facts：那是 qcc_api 从 8 阶段明细提炼出来、字段经过核对的结构。
    不在提示词层重新解析 stages——企查查返回的原始中文字段会随版本变动，
    统一在那道提炼层隔离掉，改动才只有一个地方要跟。
    """
    profile = getattr(ctx, "defendant_profile", None)
    if not isinstance(profile, dict):
        return {}
    return (profile.get("metrics") or {}).get("facts") or {}


def _format_qcc_block(ctx: CaseContext) -> str:
    """把企查查工商事实压成 prompt 文本块；无数据返回空串。

    与 _format_pkulaw_block 同一条规矩：确有数据才注入。把「没查到」的空壳塞进
    prompt，LLM 会把「无信息」误读成「被告是家空壳公司」，比不注入更糟。
    """
    facts = _qcc_facts(ctx)
    if not facts:
        return ""

    e, r, s = facts.get("entity") or {}, facts.get("risk") or {}, facts.get("scale") or {}

    def join_kv(pairs) -> str:
        return "、".join(f"{k} {v}" for k, v in pairs if v not in (None, "", []))

    lines = ["## 被告工商登记事实（企查查实查，用于校正案情中对侵权规模的自述）"]

    name_line = f"- 主体：{e.get('name') or '（未取到）'}"
    if e.get("credit_code"):
        name_line += f"（统一社会信用代码 {e['credit_code']}）"
    if e.get("queried_name") and not e.get("name_matches_query", True):
        # 查错人会让后面所有事实全部张冠李戴，且不会有任何报错——必须显式说破
        name_line += f"；⚠️ 注意：实际锁定主体与所查名称「{e['queried_name']}」不一致，请谨慎采信"
    lines.append(name_line)

    status_line = join_kv([
        ("登记状态", e.get("reg_status")),
        ("成立", e.get("established")),
        ("注册资本", e.get("registered_capital")),
        ("参保人数", f"{e['insured_count']} 人" if e.get("insured_count") is not None else None),
        ("人员规模标注", e.get("staff_scale")),
        ("行业", e.get("industry")),
        ("所在地", e.get("region")),
    ])
    if status_line:
        lines.append(f"- 工商登记：{status_line}")
    if e.get("legal_rep"):
        lines.append(f"- 法定代表人：{e['legal_rep']}")

    risk_line = join_kv([
        ("注销", r.get("deregistered")), ("清算", r.get("liquidation")),
        ("破产重整", r.get("bankruptcy")), ("被执行", r.get("executed")),
        ("失信", r.get("dishonest")), ("终本", r.get("terminated")),
        ("限高", r.get("restricted")), ("严重违法", r.get("serious_violation")),
        ("经营异常", r.get("abnormal")), ("法院立案", r.get("court_filed")),
    ])
    risk_line += "（各单位均为记录条数，0 表示未查到记录）"
    if r.get("hit_dimensions"):
        risk_line += f"；风险分诊命中 {r['hit_dimensions']} 个维度"
    lines.append(f"- 涉诉与风险：{risk_line}")

    scale_line = join_kv([
        ("商标", s.get("trademark_count")), ("线上店铺", s.get("online_shops")),
        ("APP", s.get("app")), ("小程序", s.get("miniprogram")),
        ("微信公众号", s.get("wechat_mp")), ("抖音", s.get("douyin")),
        ("融资记录", s.get("financing")), ("荣誉", s.get("honors")),
        ("被诉历史", f"{s.get('litigation_history')} 件" if s.get("litigation_history") is not None else None),
    ])
    if scale_line:
        lines.append(f"- 经营与资产：{scale_line}")

    tier = facts.get("scale_tier")
    if tier:
        lines.append(f"- 规模档位：{tier}（{facts.get('scale_tier_basis') or '无依据'}）"
                     f"——仅作规模参考，不得据此直接加减判赔金额")

    lines.append("- 上述为工商登记的客观数据；与案情陈述不一致时，以工商数据为准。")
    return "\n".join(lines)


def evaluate_rights(ctx: CaseContext, use_mock: bool = False,
                    pkulaw: Dict[str, Any] = None) -> Dict[str, Any]:
    """子维度 1.1 权利基础（<60 触发红灯）"""
    if use_mock:
        from .mock import mock_rights
        return mock_rights(ctx.cause_type)

    focus = {
        "商标侵权": "商标是否有效注册、是否连续三年使用（防撤三）、核定范围是否覆盖侵权、是否驰名、无效/撤销风险",
        "著作权侵权": "独创性程度、权利归属链条（职务作品/委托创作/转让）、是否登记、创作留痕",
        "不正当竞争": "是否构成'有一定影响'的商品名称/包装装潢/企业名称、知名度证据充分性",
    }.get(ctx.cause_type, "")
    prompt = f"""{_base(ctx)}

{_format_pkulaw_block(pkulaw)}

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
    if pkulaw is not None:
        result["pkulaw"] = _pkulaw_payload(pkulaw)
    return result


def evaluate_infringement(ctx: CaseContext, use_mock: bool = False,
                          pkulaw: Dict[str, Any] = None) -> Dict[str, Any]:
    """子维度 1.2 侵权认定（构成要件逐一认定）"""
    if use_mock:
        from .mock import mock_infringement
        return mock_infringement(ctx.cause_type)

    elements = {
        "商标侵权": ["商标性使用", "商品/服务相同或类似", "商标相同或近似", "混淆可能性", "是否正当使用"],
        "著作权侵权": ["接触", "实质性相似", "是否落入合理使用"],
        "不正当竞争": ["混淆行为构成", "商业诋毁构成（如适用）", "互联网专条构成（如适用）"],
    }.get(ctx.cause_type, [])
    rights_analysis = (ctx.dimension_results.get("rights", {}).get("result") or {}).get("analysis", "")
    prompt = f"""{_base(ctx)}

{_format_pkulaw_block(pkulaw)}

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
    result = call_json(_SYSTEM, prompt, node="infringement")
    if pkulaw is not None:
        result["pkulaw"] = _pkulaw_payload(pkulaw)
    return result


def evaluate_procedure(ctx: CaseContext, use_mock: bool = False,
                       pkulaw: Dict[str, Any] = None) -> Dict[str, Any]:
    """子维度 1.3 诉讼程序"""
    if use_mock:
        from .mock import mock_procedure
        return mock_procedure(ctx.cause_type)

    prompt = f"""{_base(ctx)}

{_format_pkulaw_block(pkulaw)}

{procedure_clause(ctx.cause_type, ctx.case_description)}

## 评估任务
评估诉讼程序可行性：① 诉讼时效（是否临近届满、有无中断中止事由、持续侵权下赔偿期间如何起算）② 管辖与仲裁（是否存在有效仲裁协议、结合上述级别管辖判断哪个法院对原告最有利）③ 主体适格（原告是否适格权利人、被告是否明确、是否需追加共同被告）④ 前置程序（行政前置、通知-删除要求）⑤ 上述常见抗辩在本案的成立可能性

## 返回 JSON
{{
  "score": 0-100 的整数,
  "risks": [{{"item": "评估项", "level": "high|medium|low|none", "detail": "一句话"}}],
  "analysis": "综合分析（200字内）"
}}"""
    result = call_json(_SYSTEM, prompt, node="procedure")
    if pkulaw is not None:
        result["pkulaw"] = _pkulaw_payload(pkulaw)
    return result


def evaluate_damages(ctx: CaseContext, use_mock: bool = False) -> Dict[str, Any]:
    """判赔规模（要钱目标）：P10/P50/P90 + 回报倍数 + 侵权规模支撑度"""
    if use_mock:
        from .mock import mock_damages
        return mock_damages(ctx.cause_type)

    qcc_block = _format_qcc_block(ctx)
    cross_check = """
④ 规模交叉核对（有工商事实才做）——案情自述的侵权规模（门店数、销量、覆盖范围）
   必须与上述工商客观数据（参保人数、注册资本、涉诉记录、渠道保有量）对得上：
   - 明显不符时（例如自述两千余家门店，工商实查参保百余人、线上渠道个位数），
     以工商数据为准下调 scale_support，并把矛盾写进 external_conflict；
   - 无矛盾时 external_conflict 填空字符串。

【重要】判赔金额完全由你按法定赔偿区间与类案水平判断。上述工商事实只用于校正
案情里主张的侵权规模，不要因为外部数据而脱离法定框架随意加价或减价：既不得有
乘数，也不得把工商数据当成突破法定赔偿上限的理由。""" if qcc_block else ""

    # external_conflict 字段也只在有工商事实时要求：没有事实就谈不上「交叉核对」，
    # 让模型对着不存在的数据编一句矛盾描述，比不给这个字段更糟。
    conflict_field = ('  "external_conflict": "案情主张与外部工商数据矛盾的具体描述，'
                      '无矛盾则填空字符串",\n') if qcc_block else ""

    prompt = f"""{_base(ctx)}

{damages_clause(ctx.cause_type)}
{qcc_block}

## 评估任务
估算本案判赔规模（0-100 相对评分），综合三方面：
① 判赔金额量级——估算判赔金额概率分布（P10/P50/P90，单位：万元），严格以本案由的
   法定赔偿区间为边界、以类案判赔水平为锚，并说明走的是哪个计算顺位
② 回报倍数——P50 判赔额 ÷ 预估总成本（律师费+诉讼费+公证费等，按 8-15 万估）
③ 侵权规模支撑度——案情中的销量/店铺规模等能否支撑高判赔{cross_check}

## 返回 JSON
{{
  "score": 0-100 的整数,
  "p10": 数值（万元）, "p50": 数值（万元）, "p90": 数值（万元）,
  "return_multiple": 数值（回报倍数）,
  "scale_support": "high|medium|low",
{conflict_field}  "analysis": "分析（200字内）"
}}"""
    result = call_json(_SYSTEM, prompt, node="damages")
    # 无工商事实时不该出现 external_conflict：那是「查过但没查出问题」与
    # 「根本没查」的区别，留空比填 '' 更不容易被误读。
    if not qcc_block:
        result.pop("external_conflict", None)
    return result


def evaluate_precedent(ctx: CaseContext, use_mock: bool = False) -> Dict[str, Any]:
    """判例价值（要名目标）"""
    if use_mock:
        from .mock import mock_precedent
        return mock_precedent(ctx.cause_type)

    prompt = f"""{_base(ctx)}

{precedent_clause(ctx.cause_type)}

## 评估任务
评估本案的判例价值（0-100），综合四方面：
① 首案潜力——是否无同类在先判例
② 指导性案例潜力——比对最高法指导性案例/典型案例遴选标准
③ 行业震慑效应——胜诉后对行业其他侵权者的威慑力
④ 规则明晰价值——能否推动模糊法律规则的明确化（结合上述本案由的判断重点）

## 返回 JSON
{{
  "score": 0-100 的整数,
  "first_case_index": 0-100 的首案指数,
  "influence_level": "行业级|区域级|个案级",
  "analysis": "分析（200字内）"
}}"""
    return call_json(_SYSTEM, prompt, node="precedent")
