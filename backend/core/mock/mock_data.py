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
