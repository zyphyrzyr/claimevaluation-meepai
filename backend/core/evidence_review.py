"""
证据盘点引擎（Evidence Review）- v4 前置步骤
先摸家底，再谈输赢。

- 对照带法律出处的"标准取证清单"逐项核验（4 类 × 3 案由）
- 产出证据矩阵（充足/不足/缺失 + 一句理由）+ 缺口清单 + 清单外发现
- 证据完整度 → 决定最终结论置信度（不评分、不参与乘法）
"""

from typing import Any, Dict, List, Optional

from .config import (CAUSE_TRADEMARK, CAUSE_COPYRIGHT,
                     CAUSE_UNFAIR_COMPETITION, SUPPORTED_CAUSE_TYPES)
from .llm_gateway import call_json

# ============================================================
# 标准取证清单（4 类 × 3 案由，带法律出处）
# ============================================================

_CATEGORIES = ["权利基础证据", "侵权认定证据", "损害赔偿证据", "取证技术规范"]

CHECKLISTS: Dict[str, Dict[str, List[Dict[str, str]]]] = {
    CAUSE_TRADEMARK: {
        "权利基础证据": [
            {"item": "商标注册证/续展证明", "basis": "《商标法》第3条", "element": "权利有效性"},
            {"item": "近三年商标使用证据（防撤三）", "basis": "《商标法》第49条", "element": "防撤三"},
            {"item": "商标知名度证据（可主张驰名跨类保护）", "basis": "《商标法》第13、14条", "element": "保护范围"},
        ],
        "侵权认定证据": [
            {"item": "侵权商品/服务截图、页面快照", "basis": "《商标法》第57条", "element": "商标性使用"},
            {"item": "购买取证（侵权实物+交易记录）", "basis": "《商标法》第57条", "element": "混淆可能性"},
            {"item": "公证文书（侵权事实固定）", "basis": "《民事诉讼法》第72条", "element": "证据效力"},
        ],
        "损害赔偿证据": [
            {"item": "被告获利/原告损失/许可费依据", "basis": "《商标法》第63条", "element": "判赔计算"},
            {"item": "侵权规模证据（销量/评价数/店铺数）", "basis": "《商标法》第63条", "element": "判赔计算"},
        ],
        "取证技术规范": [
            {"item": "可信时间戳/区块链存证/公证取证", "basis": "《电子签名法》第8条", "element": "证据三性"},
        ],
    },
    CAUSE_COPYRIGHT: {
        "权利基础证据": [
            {"item": "作品底稿/源文件/创作留痕", "basis": "《著作权法》第12条", "element": "权属推定"},
            {"item": "著作权登记证书", "basis": "《作品自愿登记试行办法》", "element": "权属证明"},
            {"item": "权利归属链条文件（职务作品/委托创作/转让合同）", "basis": "《著作权法》第18、19条", "element": "权利归属"},
        ],
        "侵权认定证据": [
            {"item": "被诉侵权作品与权利作品比对材料", "basis": "接触+实质性相似规则", "element": "实质性相似"},
            {"item": "被告接触权利作品的可能性证据", "basis": "接触+实质性相似规则", "element": "接触"},
            {"item": "侵权页面/载体截图及固定", "basis": "《著作权法》第53条", "element": "侵权行为"},
        ],
        "损害赔偿证据": [
            {"item": "原告实际损失/被告违法所得依据", "basis": "《著作权法》第54条", "element": "判赔计算"},
            {"item": "侵权规模证据（下载量/销量/传播范围）", "basis": "《著作权法》第54条", "element": "判赔计算"},
        ],
        "取证技术规范": [
            {"item": "可信时间戳/区块链存证/公证取证", "basis": "《电子签名法》第8条", "element": "证据三性"},
        ],
    },
    CAUSE_UNFAIR_COMPETITION: {
        "权利基础证据": [
            {"item": "商品名称/包装装潢/企业名称使用证据", "basis": "《反不正当竞争法》第6条", "element": "有一定影响"},
            {"item": "知名度证据（销售规模/宣传投入/市场排名）", "basis": "《反不正当竞争法》第6条司法解释", "element": "有一定影响"},
        ],
        "侵权认定证据": [
            {"item": "被诉混淆行为证据（仿冒页面/实物比对）", "basis": "《反不正当竞争法》第6条", "element": "混淆行为"},
            {"item": "商业诋毁内容固定（如适用）", "basis": "《反不正当竞争法》第11条", "element": "商业诋毁"},
            {"item": "互联网不正当竞争行为证据（如适用）", "basis": "《反不正当竞争法》第12条", "element": "互联网专条"},
        ],
        "损害赔偿证据": [
            {"item": "原告损失/被告获利依据", "basis": "《反不正当竞争法》第17条", "element": "判赔计算"},
            {"item": "侵权规模证据", "basis": "《反不正当竞争法》第17条", "element": "判赔计算"},
        ],
        "取证技术规范": [
            {"item": "可信时间戳/区块链存证/公证取证", "basis": "《电子签名法》第8条", "element": "证据三性"},
        ],
    },
}

_SYSTEM = (
    "你是知识产权证据审查专家。请严格按 JSON 格式返回。"
    "【用户经验与观点】为视角参考；【证据材料文本】为事实基准。"
    "二者矛盾时，事实以证据材料为准并显式说明，判断可参考用户观点但须标注。"
)


def require_supported_cause(cause_type: str) -> str:
    """
    校验案由并原样返回。未知案由抛 ValueError，不静默回落商标。

    mock 数据（core/mock）与模拟法庭画像（core/moot_court/prompts）已各自做了
    同样的收敛，这里是真实 Prompt 链路上的同一道闸。
    """
    if cause_type not in CHECKLISTS:
        raise ValueError(
            f"不支持的案由：{cause_type}；当前支持 {SUPPORTED_CAUSE_TYPES}")
    return cause_type


def _build_prompt(cause_type: str, case_description: str,
                  evidence_texts: str, user_viewpoints: str) -> str:
    require_supported_cause(cause_type)
    checklist = CHECKLISTS[cause_type]
    lines = []
    for cat in _CATEGORIES:
        for idx, entry in enumerate(checklist.get(cat, []), 1):
            lines.append(f"- [{cat}-{idx}] {entry['item']}（依据：{entry['basis']}；要件：{entry['element']}）")
    checklist_text = "\n".join(lines)

    return f"""请对照标准取证清单，逐项核验本案证据准备情况。

## 案情描述
{case_description[:3000]}

## 案由
{cause_type}

## 已上传证据材料文本
{evidence_texts[:6000] if evidence_texts else "（未上传证据文件，仅凭案情描述判断）"}

## 用户经验与观点
{user_viewpoints[:1000] if user_viewpoints else "（无）"}

## 标准取证清单（逐项核验）
{checklist_text}

## 要求
1. 逐项判断：该项证据是否已提供、内容能否支撑对应要件（如商标注册证上的注册号/有效期/注册人）
2. 每项给出状态：sufficient（充足）/ partial（不足）/ missing（缺失）+ 一句理由
3. 如发现清单之外但有价值的证据，列入 extra_evidence
4. 不受用户观点影响而拔高证据状态；用户观点与证据矛盾时在 note 中显式说明

## 返回 JSON
{{
  "matrix": [{{"id": "权利基础证据-1", "status": "sufficient|partial|missing", "reason": "一句话"}}],
  "extra_evidence": [{{"name": "证据名", "value": "价值说明"}}],
  "note": "需要显式说明的矛盾或提示（无则空字符串）"
}}"""


def _completeness(matrix: List[Dict[str, Any]]) -> float:
    """完整度：充足=1 / 不足=0.5 / 缺失=0 的占比（%）"""
    if not matrix:
        return 0.0
    weight = {"sufficient": 1.0, "partial": 0.5, "missing": 0.0}
    total = sum(weight.get(item.get("status", "missing"), 0.0) for item in matrix)
    return round(total / len(matrix) * 100, 1)


def review_evidence(
    case_description: str,
    cause_type: str = CAUSE_TRADEMARK,
    evidence_texts: str = "",
    user_viewpoints: str = "",
    use_mock: bool = False,
) -> Dict[str, Any]:
    """
    证据盘点主入口。
    返回：{
      matrix: 证据矩阵（含类别/项目/法律依据/状态/理由）,
      gap_list: 缺口清单（partial+missing，附补证路径建议）,
      extra_evidence: 清单外发现,
      completeness: 证据完整度（%，供置信度）,
      note, error
    }
    """
    if use_mock:
        from .mock import mock_evidence_review
        return mock_evidence_review(cause_type)

    # 未知案由显式报错，不静默回落到商标清单：
    # 回落会产出一份看起来正常、实际按商标要件核验的证据矩阵（评测报告 P2）
    require_supported_cause(cause_type)
    checklist = CHECKLISTS[cause_type]
    prompt = _build_prompt(cause_type, case_description, evidence_texts, user_viewpoints)
    result = call_json(_SYSTEM, prompt, node="evidence_review")

    if "error" in result:
        return {"matrix": [], "gap_list": [], "extra_evidence": [],
                "completeness": 0.0, "note": "", "error": result["error"]}

    # 把 LLM 判断合并回带法律出处的清单骨架
    status_map = {item.get("id"): item for item in result.get("matrix", [])}
    matrix, gap_list = [], []
    for cat in _CATEGORIES:
        for idx, entry in enumerate(checklist.get(cat, []), 1):
            item_id = f"{cat}-{idx}"
            judged = status_map.get(item_id, {})
            status = judged.get("status", "missing")
            row = {
                "id": item_id,
                "category": cat,
                "item": entry["item"],
                "basis": entry["basis"],
                "element": entry["element"],
                "status": status if status in ("sufficient", "partial", "missing") else "missing",
                "reason": judged.get("reason", "未提供"),
            }
            matrix.append(row)
            if row["status"] != "sufficient":
                gap_list.append({
                    **row,
                    "suggestion": f"补充{entry['item']}（支撑要件：{entry['element']}）",
                })

    return {
        "matrix": matrix,
        "gap_list": gap_list,
        "extra_evidence": result.get("extra_evidence", []),
        "completeness": _completeness(matrix),
        "note": result.get("note", ""),
        "error": None,
    }
