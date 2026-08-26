"""
评分引擎 - v4 二维乘法模型（纯规则，零 LLM）
主诉决策分 = 法律可行性 × 业务预期
  法律可行性 = 权利基础 × 侵权认定 × 诉讼程序 × 对抗检验修正系数(0.7-1.3)
  业务预期   = 要钱 → 判赔规模 × 回款能力 / 要名 → 判例价值
证据完整度 → 独立置信度（不参与乘法）
红线命中 → 硬门禁拦截（不看分数）
"""

from typing import Any, Dict, Iterable, List, Optional

from .config import (
    ADVERSARIAL_COEFF_MIN, ADVERSARIAL_COEFF_MAX,
    SCORE_THRESHOLD_GO, SCORE_THRESHOLD_PATCH,
)


def normalize(score, scale=100):
    return max(0, min(score, scale)) / scale


def clip_coefficient(c: float) -> float:
    return max(ADVERSARIAL_COEFF_MIN, min(c, ADVERSARIAL_COEFF_MAX))


def calculate_legal_feasibility(
    rights_score: float,
    infringement_score: float,
    procedure_score: float,
    correction_coefficient: float = 1.0,
) -> float:
    """法律可行性：等权相乘（一票否决逻辑）× 模拟法庭修正系数"""
    r = normalize(rights_score)
    i = normalize(infringement_score)
    p = normalize(procedure_score)
    c = clip_coefficient(correction_coefficient)
    return round(r * i * p * c * 100, 1)


def calculate_business_expectation(
    goal_type: str,
    damages_scale: Optional[float] = None,
    recovery_ability: Optional[float] = None,
    precedent_value: Optional[float] = None,
) -> float:
    """
    业务预期分流：
      要钱 → 判赔规模 × 回款能力（财务导向）
      要名 → 判例价值（名誉导向，财务仅作参考）
    """
    if goal_type == "要钱":
        d = normalize(damages_scale or 0)
        r = normalize(recovery_ability or 0)
        return round(d * r * 100, 1)
    return round(normalize(precedent_value or 0) * 100, 1)


def calculate_overall_score(legal_feasibility: float, business_expectation: float) -> float:
    """主诉决策分 = 法律可行性 × 业务预期（只有这两项相乘）"""
    return round(normalize(legal_feasibility) * normalize(business_expectation) * 100, 1)


def calculate_confidence(evidence_completeness: float, retrieval_complete: bool = True) -> float:
    """
    置信度 = f(证据完整度)，独立输出，不参与乘法
    证据完整度来自证据盘点矩阵（充足=1/不足=0.5/缺失=0 的加权占比）
    外部检索未完成时适当下调
    """
    score = normalize(evidence_completeness) * 100
    if not retrieval_complete:
        score *= 0.85
    return round(max(0, min(score, 100)), 1)


def has_block_red_flag(red_flags: List[Dict[str, Any]]) -> bool:
    return any(r.get("severity") == "block" or r.get("status") == "block" for r in red_flags)


def weakest_dimension(dimension_scores: Dict[str, float]) -> Optional[str]:
    """找出最低分子维度（60-74 档建议时指出具体补哪里）"""
    if not dimension_scores:
        return None
    return min(dimension_scores, key=dimension_scores.get)


def generate_recommendation(
    final_score: Optional[float],
    red_flags: List[Dict[str, Any]],
    is_complete: bool = True,
    missing_dimensions: Optional[Iterable[str]] = None,
    dimension_scores: Optional[Dict[str, float]] = None,
    confidence: Optional[float] = None,
) -> Dict[str, Any]:
    """
    决策建议：
      红线 block → 暂不建议起诉（硬门禁，不看分数）
      数据不完整 → 评估未完成（不输出误导性结论）
      ≥75 建议优先启动 / 60-74 补充短板后启动（指出最低分子维度） / <60 暂缓
    """
    missing_dimensions = list(missing_dimensions or [])

    if has_block_red_flag(red_flags):
        return {
            "recommendation": "暂不建议起诉",
            "reason": "存在程序性红线问题（时效/主体资格等），需优先解决，本结论不受评分影响",
            "level": "block",
        }

    if not is_complete or final_score is None:
        dims = "、".join(missing_dimensions) if missing_dimensions else "关键维度"
        return {
            "recommendation": "评估未完成",
            "reason": f"{dims}尚未完成，当前不输出综合起诉建议。",
            "level": "yellow",
        }

    confidence_note = ""
    if confidence is not None and confidence < 50:
        confidence_note = f"；注意证据完整度偏低（置信度 {confidence}%），建议先补证再决策"

    if final_score >= SCORE_THRESHOLD_GO:
        return {
            "recommendation": "建议优先启动诉讼",
            "reason": f"主诉决策分 {final_score} 分，法律风险可控、业务预期可观{confidence_note}",
            "level": "green",
        }
    if final_score >= SCORE_THRESHOLD_PATCH:
        weakest = weakest_dimension(dimension_scores or {})
        weakest_note = f"，建议优先补强「{weakest}」" if weakest else ""
        return {
            "recommendation": "补充短板后启动",
            "reason": f"主诉决策分 {final_score} 分{weakest_note}{confidence_note}",
            "level": "yellow",
        }
    return {
        "recommendation": "建议暂缓，重新评估",
        "reason": f"主诉决策分 {final_score} 分，当前胜诉收益比不足{confidence_note}",
        "level": "red",
    }
