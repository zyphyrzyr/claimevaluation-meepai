"""
对抗修正系数的推导（模拟法庭第五步产出）

为什么单独成模块：
    此前系数由法官 LLM 在同一个 JSON 里「顺便」给一个 0.70–1.30 的数，而它同时
    输出的 10 项子分（原被告各 5 项）只用于展示、不参与计算。后果有三：
      ① 单次采样，同一案件重复跑系数会漂；
      ② 系数与法官自己的评分可能自相矛盾（子分显示被告抗辩很强，系数却是 1.00）；
      ③ 「为什么 0.85」拿不出算式，只有一句 coefficient_reasoning。

    现在改为：10 项子分 → 两侧加权强度 → 比值 → 裁剪。取值可复现、可解释，
    且方差从「一个数」摊薄到 10 个数上。

    ⚠️ 法官仍会输出一个自评系数（correction_coefficient），但**不参与取值**，
    只作为对照记录在 detail 里——用于将来标定 GAMMA / 权重时比对「推导值 vs 直觉」。

取值链（三级降级，逐级记录来源）：
    1. derived  —— 10 项子分齐全，按公式推导（正常路径）
    2. model    —— 子分缺失，退回法官自评值（裁剪后）
    3. fallback —— 两者都没有，取 1.00（不修正），绝不静默编造

只产出系数与推导明细，不参与评分聚合本身（乘法在 scoring.calculate_legal_feasibility）。
"""

from typing import Any, Dict, Optional, Tuple

# ── 子分项（与 prompts.JUDGE_SUMMARY_USER 的 JSON schema 严格一致）──────────
# 改 prompt 里的键名必须同步改这里，否则推导会因取不到值而静默降级。
PLAINTIFF_KEYS = (
    "rights",                 # 权利基础论证
    "infringement",           # 侵权认定论证
    "evidence",               # 证据体系
    "legal_application",      # 法律适用
    "claim_reasonableness",   # 诉讼请求合理性
)

DEFENDANT_KEYS = (
    "fact_defense",            # 事实抗辩
    "legal_defense",           # 法律抗辩
    "evidence_challenge",      # 证据质疑
    "alternative_explanation", # 替代性解释
    "procedural_defense",      # 程序性抗辩
)

# 权重：先等权。跑够真实案子后可在此标定——比如给「证据质疑」更高权重，
# 因为它在实务里最能左右判决。改这里等于改系数的灵敏度，要同步看测试。
PLAINTIFF_WEIGHTS: Dict[str, float] = {k: 1.0 for k in PLAINTIFF_KEYS}
DEFENDANT_WEIGHTS: Dict[str, float] = {k: 1.0 for k in DEFENDANT_KEYS}

# ── 公式参数 ──────────────────────────────────────────────
# coeff = clamp( ((P + EPS) / (D + EPS)) ** GAMMA, MIN, MAX )
#
# 用比值而非差值的理由：系数表达的是「被告抗辩对原告论证的削弱程度」，
# 天然是相对量——原告 30 分被告 20 分（双方都很烂）与原告 90 分被告 60 分，
# 削弱程度不该用同一个绝对差衡量。
#
# EPS 的作用有二：① D → 0 时防止除零爆炸；② 对双方都很低的情形提供阻尼，
# 避免「原告 10 分、被告 5 分」被算成 2.0 而撞上上限。
EPS = 10.0
GAMMA = 1.0        # 灵敏度：> 1 放大差距，< 1 收敛差距

# 与 config.ADVERSARIAL_COEFF_MIN / MAX 对齐（0.70 / 1.30）
COEFF_MIN = 0.70
COEFF_MAX = 1.30


def _as_float(v: Any) -> Optional[float]:
    if isinstance(v, bool) or v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _weighted_mean(scores: Any, weights: Dict[str, float],
                   keys: Tuple[str, ...]) -> Optional[float]:
    """
    按权重求均值；越界值夹取而非丢弃。

    模型偶尔给 105 或 -3 这类越界分。丢弃会让「有 4 项有效分」和「有 5 项」
    被同等对待，夹取则保留了该维度的相对位置。
    """
    if not isinstance(scores, dict):
        return None
    num = 0.0
    den = 0.0
    for k in keys:
        v = _as_float(scores.get(k))
        if v is None:
            continue
        v = max(0.0, min(100.0, v))
        w = weights.get(k, 1.0)
        num += v * w
        den += w
    return None if den == 0 else num / den


def _clamp(raw: float) -> Tuple[float, bool]:
    """裁剪到合法区间，并回报是否被裁剪过（裁剪必须显式露出，不能静默）"""
    if raw < COEFF_MIN:
        return COEFF_MIN, True
    if raw > COEFF_MAX:
        return COEFF_MAX, True
    return raw, False


def derive_coefficient(judge: Dict[str, Any]) -> Dict[str, Any]:
    """
    从法官归纳的结构化评分推导修正系数。

    返回 dict：
        coefficient  最终采用值（0.70–1.30，两位小数）
        source       derived / model / fallback（取值链走到哪一级）
        plaintiff   / defendant   两侧加权强度（None 表示没取到）
        model_value  法官自评值（可能为 None），仅供对照
        clamped      是否被裁剪到边界
        formula      人话版的推导说明，供界面与审计展示
    """
    if not isinstance(judge, dict):
        judge = {}

    p = _weighted_mean(judge.get("plaintiff_scores"), PLAINTIFF_WEIGHTS, PLAINTIFF_KEYS)
    d = _weighted_mean(judge.get("defendant_scores"), DEFENDANT_WEIGHTS, DEFENDANT_KEYS)
    model_value = _as_float(judge.get("correction_coefficient"))

    detail: Dict[str, Any] = {
        "coefficient": 1.0,
        "source": "fallback",
        "plaintiff": p,
        "defendant": d,
        "model_value": model_value,
        "clamped": False,
        "formula": "",
    }

    if p is None or d is None:
        # ── 降级：子分不齐，退回法官自评 ──
        if model_value is not None:
            value, clamped = _clamp(model_value)
            detail.update({
                "coefficient": round(value, 2),
                "source": "model",
                "clamped": clamped,
                "formula": ("子项评分不齐，未能推导；采用法官自评系数"
                            + ("（已裁剪到合理区间）" if clamped else "")),
            })
        else:
            detail["formula"] = "既无子项评分也无自评系数，按「不修正」计入 1.00"
        return detail

    raw = ((p + EPS) / max(d + EPS, 1e-6)) ** GAMMA
    value, clamped = _clamp(raw)
    detail.update({
        "coefficient": round(value, 2),
        "source": "derived",
        "clamped": clamped,
        "formula": (f"原告论证强度 {p:.0f} ÷ 被告抗辩强度 {d:.0f} = {raw:.2f}"
                    + ("，已裁剪到合理区间" if clamped else "")),
    })
    return detail
