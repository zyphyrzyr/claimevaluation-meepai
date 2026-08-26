"""
Mock 数据（v4 二维模型，商标案由示例）
函数签名与真实模块一致，USE_MOCK=True 时全链路离线可跑
"""

from typing import Any, Dict, List

from ..config import CAUSE_TRADEMARK


def mock_evidence_review(cause_type: str = CAUSE_TRADEMARK) -> Dict[str, Any]:
    matrix = [
        {"id": "权利基础证据-1", "category": "权利基础证据", "item": "商标注册证/续展证明",
         "basis": "《商标法》第3条", "element": "权利有效性", "status": "sufficient",
         "reason": "已提供注册证，注册号/有效期/注册人信息完整"},
        {"id": "权利基础证据-2", "category": "权利基础证据", "item": "近三年商标使用证据（防撤三）",
         "basis": "《商标法》第49条", "element": "防撤三", "status": "partial",
         "reason": "有销售记录但缺少连续三年的完整使用链"},
        {"id": "权利基础证据-3", "category": "权利基础证据", "item": "商标知名度证据（可主张驰名跨类保护）",
         "basis": "《商标法》第13、14条", "element": "保护范围", "status": "missing",
         "reason": "未提供广告宣传、市场排名等知名度材料"},
        {"id": "侵权认定证据-1", "category": "侵权认定证据", "item": "侵权商品/服务截图、页面快照",
         "basis": "《商标法》第57条", "element": "商标性使用", "status": "sufficient",
         "reason": "已提供侵权店铺页面截图，标识使用清晰"},
        {"id": "侵权认定证据-2", "category": "侵权认定证据", "item": "购买取证（侵权实物+交易记录）",
         "basis": "《商标法》第57条", "element": "混淆可能性", "status": "missing",
         "reason": "未进行购买取证"},
        {"id": "侵权认定证据-3", "category": "侵权认定证据", "item": "公证文书（侵权事实固定）",
         "basis": "《民事诉讼法》第72条", "element": "证据效力", "status": "missing",
         "reason": "截图未公证，证据效力存疑"},
        {"id": "损害赔偿证据-1", "category": "损害赔偿证据", "item": "被告获利/原告损失/许可费依据",
         "basis": "《商标法》第63条", "element": "判赔计算", "status": "partial",
         "reason": "有许可合同但费率与本案品类不完全对应"},
        {"id": "损害赔偿证据-2", "category": "损害赔偿证据", "item": "侵权规模证据（销量/评价数/店铺数）",
         "basis": "《商标法》第63条", "element": "判赔计算", "status": "sufficient",
         "reason": "页面显示月销 3000+，评价数 1.2 万"},
        {"id": "取证技术规范-1", "category": "取证技术规范", "item": "可信时间戳/区块链存证/公证取证",
         "basis": "《电子签名法》第8条", "element": "证据三性", "status": "partial",
         "reason": "部分截图做了时间戳存证，覆盖不全"},
    ]
    gap_list = [
        {**row, "suggestion": f"补充{row['item']}（支撑要件：{row['element']}）"}
        for row in matrix if row["status"] != "sufficient"
    ]
    return {
        "matrix": matrix,
        "gap_list": gap_list,
        "extra_evidence": [
            {"name": "被告同类侵权历史", "value": "案情提及被告曾被投诉，可用于证明主观恶意、争取惩罚性赔偿"}
        ],
        "completeness": 55.6,
        "note": "",
        "error": None,
    }


def mock_rights() -> Dict[str, Any]:
    return {
        "score": 82,
        "analysis": "商标合法注册且在有效期内，连续三年使用证据基本充分，核定商品覆盖侵权商品类别，权利基础较为稳固。",
        "strengths": ["注册证齐全，权利状态稳定", "有持续使用记录，撤三风险低", "核定范围覆盖侵权商品"],
        "risks": ["知名度证据不足，难以主张驰名跨类保护"],
        "red_light": False,
    }


def mock_infringement() -> Dict[str, Any]:
    return {
        "score": 76,
        "elements": [
            {"name": "商标性使用", "status": "满足", "analysis": "被告在商品标题和详情页突出使用涉案标识"},
            {"name": "商品/服务相同或类似", "status": "满足", "analysis": "与核定使用商品属同类商品"},
            {"name": "商标相同或近似", "status": "满足", "analysis": "标识在字形、读音上高度近似"},
            {"name": "混淆可能性", "status": "存疑", "analysis": "价格差异较大，需结合购买取证进一步固定"},
            {"name": "是否正当使用", "status": "存疑", "analysis": "被告可能主张描述性使用，需庭审检验"},
        ],
        "analysis": "侵权构成要件总体成立，混淆可能性与正当使用抗辩是主要争点，建议通过购买取证和公证加固。",
    }


def mock_procedure() -> Dict[str, Any]:
    return {
        "score": 85,
        "risks": [
            {"item": "诉讼时效", "level": "low", "detail": "侵权行为持续中且在 3 年时效内"},
            {"item": "管辖与仲裁", "level": "none", "detail": "无仲裁协议，可在被告住所地或侵权地法院起诉"},
            {"item": "主体适格", "level": "low", "detail": "原告为注册商标专用权人，被告主体明确"},
            {"item": "前置程序", "level": "none", "detail": "商标侵权无行政前置要求"},
        ],
        "analysis": "程序层面无明显障碍，建议优先选择侵权结果发生地法院以获得管辖便利。",
    }


def mock_damages() -> Dict[str, Any]:
    return {
        "score": 68,
        "p10": 8, "p50": 25, "p90": 60,
        "return_multiple": 2.1,
        "scale_support": "medium",
        "analysis": "参考同类案件判赔区间，P50 约 25 万元，覆盖预估成本约 2 倍；侵权规模证据可支撑中等偏上判赔，若补齐被告获利证据可进一步上探。",
    }


def mock_precedent() -> Dict[str, Any]:
    return {
        "score": 45,
        "first_case_index": 30,
        "influence_level": "个案级",
        "analysis": "同类商标侵权判例较多，首案潜力有限；案情事实常见，规则明晰价值一般，主要价值在于个案维权本身。",
    }


def mock_defendant_profile() -> Dict[str, Any]:
    """企查查 8 阶段画像 Mock：正常经营企业，回款能力中等偏上"""
    return {
        "locked_name": "示例被告科技有限公司",
        "metrics": {
            "recovery_probability": 72.0,
            "damages_adjustment": "中等规模 → 基准",
            "time_extra_months": 0,
            "red_flags": [],
            "green_flags": ["有公开财务数据"],
            "details": {},
        },
        "stages_skipped": False,
        "error": None,
    }


def mock_rule_hits() -> List[Dict[str, Any]]:
    return [
        {"rule_code": "R1", "rule_name": "诉讼时效检查", "severity": "pass",
         "result": "pass", "reason": "侵权行为发现于 1 年内，处于 3 年时效期内"},
        {"rule_code": "R2", "rule_name": "主体资格检查", "severity": "pass",
         "result": "pass", "reason": "原告为注册商标专用权人，主体适格"},
        {"rule_code": "R3", "rule_name": "仲裁条款检查", "severity": "pass",
         "result": "pass", "reason": "未发现有效仲裁协议"},
        {"rule_code": "R4", "rule_name": "关键权利证明检查", "severity": "pass",
         "result": "pass", "reason": "已提供商标注册证"},
        {"rule_code": "R5", "rule_name": "关键侵权固定证据检查", "severity": "warning",
         "result": "warning", "reason": "侵权截图未公证，建议补充公证或时间戳存证"},
        {"rule_code": "R6", "rule_name": "损害赔偿主张基础检查", "severity": "warning",
         "result": "warning", "reason": "被告获利证据不足，判赔计算依赖侵权规模推定"},
    ]


# ============================================================
# 模拟法庭 Mock（P2）：7 轮发言 + 法官结构化归纳
# ============================================================

def mock_moot_rounds() -> List[Dict[str, Any]]:
    """7 轮庭审发言（五步流程：陈述→答辩→举证→质证→辩论×2→归纳）"""
    return [
        {"step": 1, "step_name": "开庭陈述", "role": "plaintiff", "role_name": "原告代理律师",
         "content": "审判长、审判员：原告系第25类服装商品上第XXXXXXX号注册商标的专用权人，商标合法注册且在有效期内。被告未经许可，在其经营的电商平台店铺中，于商品标题、详情页显著位置使用与原告注册商标高度近似的标识，销售同类服装商品，月销量达3000余件。被告的行为构成商标性使用，易导致相关公众混淆，侵犯了原告的注册商标专用权。原告前期评估显示权利基础稳固、侵权要件基本成立，请求法庭判令被告停止侵权并赔偿损失。"},
        {"step": 2, "step_name": "被告答辩", "role": "defendant", "role_name": "被告代理律师",
         "content": "审判长：被告对原告所述事实部分不予认可。第一，被告使用的标识与原告商标在字形、读音上存在明显差异，且商品售价仅原告正品的三分之一，消费群体并不重合，不会产生混淆；第二，被告销售的商品有合法进货渠道，供货商具备品牌授权材料，被告主观上无侵权故意；第三，原告主张的赔偿数额缺乏依据，其未提供实际损失或被告获利的任何证据。恳请法庭驳回原告诉请。"},
        {"step": 3, "step_name": "举证质证-原告举证", "role": "plaintiff", "role_name": "原告代理律师",
         "content": "原告提交三组证据：证据一，商标注册证及续展证明，证明原告系注册商标专用权人，权利状态稳定；证据二，被告店铺页面截图及时间戳存证，证明被告在商品标题、详情页突出使用被诉标识，且页面显示月销3000+、评价1.2万条，侵权规模较大；证据三，原告正品与被诉商品的标识对比图，证明二者在整体观感上构成近似。需要说明的是，本案尚未进行公证购买取证，原告将在庭后补充。"},
        {"step": 3, "step_name": "举证质证-被告质证", "role": "defendant", "role_name": "被告代理律师",
         "content": "对证据一的真实性无异议，但关联性有异议：原告商标知名度有限，不应获得过宽保护。对证据二的证据效力有异议：截图未经公证，且部分未做时间戳，真实性无法确认，无法证明侵权行为的持续状态；月销数据存在刷单可能，不能作为侵权规模依据。对证据三的证明目的有异议：隔离比对下两标识差异明显，且价格、渠道的差异已足以区分商品来源。"},
        {"step": 4, "step_name": "法庭辩论-原告", "role": "plaintiff", "role_name": "原告代理律师",
         "content": "针对被告质证意见，原告认为：第一，被告未提交任何授权材料或进货凭证，合法来源抗辩不能成立；第二，商标近似应以整体观感和主要部分比对为准，被诉标识与原告商标在整体视觉上高度近似，足以造成混淆；第三，被告以低价销售恰恰会挤占原告的市场，并拉低原告品牌商誉，价格差异不能阻却混淆的认定。被告店铺销量巨大，即使按法定赔偿，也应从高认定赔偿数额。"},
        {"step": 4, "step_name": "法庭辩论-被告", "role": "defendant", "role_name": "被告代理律师",
         "content": "原告始终未能证明实际混淆的发生，混淆可能性仅是其单方推测。电商平台同类标识众多，相关公众施以一般注意力即可区分。关于赔偿，原告既无损失证据，也无被告获利证据，即使适用法定赔偿，也应考虑被告经营规模小、主观无恶意、商品单价低等因素酌定。被告愿在合理范围内协商和解。"},
        {"step": 5, "step_name": "法官归纳", "role": "judge", "role_name": "审判法官",
         "content": "本案争议焦点有三：一是被诉标识与原告商标是否构成近似、是否足以造成混淆；二是被告的合法来源抗辩能否成立；三是赔偿数额如何确定。关于焦点一，原告权利商标核定使用于同类商品，被诉标识使用于商品显著位置，整体比对构成近似，但原告未提交购买取证，混淆可能性的证明存在瑕疵。关于焦点二，被告仅口头主张有授权材料，未提交任何证据，抗辩不能成立。关于焦点三，原告举证以页面数据为主，判赔可参照侵权规模酌定。综合双方表现，本案原告胜诉基础存在但证据链条需补强，尤其缺少公证购买与被告获利证据。"},
    ]


def mock_judge_result() -> Dict[str, Any]:
    """法官结构化归纳（Mock）：修正系数 0.85，抗辩强度 62"""
    return {
        "summary": "原告权利基础稳固，侵权认定方向正确，但证据固定存在明显瑕疵：未公证、未购买取证，被告合法来源抗辩虽不能成立，但其对证据效力的质疑有相当说服力。整体而言原告胜诉预期存在，然证据补强前判赔数额恐难足额支持。",
        "summary_structured": {
            "争议焦点": "标识近似与混淆可能性、合法来源抗辩、赔偿数额确定",
            "双方表现": "原告主张清晰但证据链不完整；被告抗辩虽未获证据支持，但对证据效力的攻击有效",
            "核心判断": "原告胜诉基础存在，证据补强后可获得更佳结果",
        },
        "correction_coefficient": 0.85,
        "defense_strength": 62,
        "weak_points": [
            "侵权事实未公证，证据效力受到有效攻击",
            "未进行购买取证，混淆可能性证明存在瑕疵",
            "无被告获利或原告损失证据，判赔数额支撑不足",
        ],
        "focus_points": [
            "庭前完成公证购买取证，固定侵权事实",
            "补充被告获利线索（销量×单价×行业利润率）以支撑判赔",
        ],
        "plaintiff_scores": {"legal_basis": 78, "evidence": 55, "argument": 72},
        "defendant_scores": {"defense": 62, "evidence_challenge": 68, "argument": 60},
        "plaintiff_scores_detail": {"legal_basis": "权利基础引用准确", "evidence": "证据链不完整", "argument": "辩论逻辑清晰"},
        "defendant_scores_detail": {"defense": "合法来源抗辩无证据支持", "evidence_challenge": "对证据效力攻击有效", "argument": "酌减赔偿论证合理"},
        "coefficient_reasoning": "原告证据固定存在瑕疵且关键取证缺失，被告对证据效力的抗辩具有实质影响，修正系数取 0.85。",
    }
