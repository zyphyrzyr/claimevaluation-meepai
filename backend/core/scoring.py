"""
评分引擎 - v4 二维决策模型（纯规则，零 LLM）

聚合方式（v2 校准方案）：分层幂平均，替代原先的全连乘
  M_p(x₁..xₙ) = ((1/n)·Σ xᵢᵖ)^(1/p)      p = POWER_MEAN_P（默认 -0.5）

  法律可行性 = M_p(权利基础, 侵权认定, 诉讼程序) × 修正系数
  业务预期   = 要钱 → M_p(判赔规模, 回款能力) / 要名 → 判例价值
  主诉决策分 = M_p(法律可行性, 业务预期)

为什么换成幂平均（原先连乘的问题）：
  连乘的压缩率随维度数变化，五维各打 80 分最终只有 32.8 分，分数被压出常识区间。
  幂平均保留三件事：
    ① 自洽性——各维度同分时总分等于该分数（五维 80 = 80）
    ② 一票否决——p <= 0 时任一维度归零，总分严格归零
    ③ 短板惩罚——p 越小越接近最小值，维度越不均衡分数越低于算术平均

证据完整度 → 独立置信度（不参与聚合）
红线命中 → 硬门禁拦截（不看分数）
"""

import math
from typing import Any, Dict, Iterable, List, Optional

from .config import (
    ADVERSARIAL_COEFF_MIN, ADVERSARIAL_COEFF_MAX,
    SCORE_THRESHOLD_GO, SCORE_THRESHOLD_PATCH, POWER_MEAN_P,
)

# 判定 p 是否为 0（几何平均）的浮点容差
_P_EPS = 1e-12


def normalize(score, scale=100):
    return max(0, min(score, scale)) / scale


def clip_coefficient(c: float) -> float:
    return max(ADVERSARIAL_COEFF_MIN, min(c, ADVERSARIAL_COEFF_MAX))


def power_mean(values: Iterable[float], p: Optional[float] = None) -> float:
    """
    幂平均（广义平均）M_p = ((1/n)·Σ xᵢᵖ)^(1/p)

    - p = 1   算术平均，不惩罚短板
    - p = 0   几何平均，温和惩罚
    - p < 0   惩罚短板，越负越严厉；p → −∞ 时趋近取最小值
    - 一票否决：p <= 0 且任一维度 <= 0 时直接返回 0.0
      （必须短路——p < 0 时 0^p 发散，会得到 inf/nan）
    - 入参应为 0–100 的分值；调用方需先过 normalize() 夹取，
      否则越界值（如 120）会被放大而非截断。
    """
    if p is None:
        p = POWER_MEAN_P
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return 0.0
    if any(v <= 0 for v in vals):
        return 0.0
    if abs(p) < _P_EPS:
        return math.exp(sum(math.log(v) for v in vals) / len(vals))
    return (sum(v ** p for v in vals) / len(vals)) ** (1.0 / p)


def calculate_legal_feasibility(
    rights_score: float,
    infringement_score: float,
    procedure_score: float,
    correction_coefficient: float = 1.0,
) -> float:
    """法律可行性：三维幂平均（任一维度归零则归零）× 模拟法庭修正系数

    修正系数保持乘法语义：它是模拟法庭压力测试给出的主动调整，
    与「连乘导致的被动尺度压扁」性质不同。
    修正系数须 ≠ 0，否则会误触发一票否决语义（clip_coefficient 已保证 >= 0.7）。
    """
    r = normalize(rights_score) * 100
    i = normalize(infringement_score) * 100
    p = normalize(procedure_score) * 100
    c = clip_coefficient(correction_coefficient)
    return round(max(0.0, min(power_mean([r, i, p]) * c, 100.0)), 1)


def calculate_business_expectation(
    goal_type: str,
    damages_scale: Optional[float] = None,
    recovery_ability: Optional[float] = None,
    precedent_value: Optional[float] = None,
) -> float:
    """
    业务预期分流：
      要钱 → M_p(判赔规模, 回款能力)（财务导向）
      要名 → 判例价值（名誉导向，财务仅作参考，单值不聚合）
    """
    if goal_type == "要钱":
        d = normalize(damages_scale or 0) * 100
        r = normalize(recovery_ability or 0) * 100
        return round(max(0.0, min(power_mean([d, r]), 100.0)), 1)
    return round(normalize(precedent_value or 0) * 100, 1)


def calculate_overall_score(legal_feasibility: float, business_expectation: float) -> float:
    """主诉决策分 = M_p(法律可行性, 业务预期)（v2：连乘改为幂平均）"""
    lg = normalize(legal_feasibility) * 100
    bz = normalize(business_expectation) * 100
    return round(max(0.0, min(power_mean([lg, bz]), 100.0)), 1)


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
    stale_dimensions: Optional[Iterable[str]] = None,
    dimension_scores: Optional[Dict[str, float]] = None,
    confidence: Optional[float] = None,
) -> Dict[str, Any]:
    """
    决策建议：
      红线 block → 暂不建议起诉（硬门禁，不看分数）
      含过期维度（仍算得出分）→ 结论仅供参考（标「参考」，不隐藏、不误导）
      数据不完整（failed/缺失，算不出分）→ 评估未完成（不输出误导性结论）
      ≥ SCORE_THRESHOLD_GO 建议优先启动
      ≥ SCORE_THRESHOLD_PATCH 补充短板后启动（指出最低分子维度）
      < SCORE_THRESHOLD_PATCH 暂缓
    """
    missing_dimensions = list(missing_dimensions or [])
    stale_dimensions = list(stale_dimensions or [])

    if has_block_red_flag(red_flags):
        return {
            "recommendation": "暂不建议起诉",
            "reason": "存在程序性红线问题（时效/主体资格等），需优先解决，本结论不受评分影响",
            "level": "block",
        }

    if stale_dimensions and final_score is not None and is_complete:
        # 过期维度旧分仍参与聚合（所以 final_score 算得出、is_complete 为真），
        # 但结论须明确标注「参考」而非正常结论——既不让陈旧分静默复用误导，
        # 也不再像旧逻辑那样因 stale 一票否决导致整案结论消失。
        dims = "、".join(stale_dimensions)
        return {
            "recommendation": "结论仅供参考（含过期维度）",
            "reason": (f"「{dims}」因上游被重跑、尚未重新评估，当前结论基于其上次结果；"
                       f"建议对其重跑后再采信。"),
            "level": "stale",
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
