"""
判赔措辞：把 p10/p50/p90 这套概率分位数翻译成使用人看得懂的话

为什么单独成模块：
    p10/p50/p90 是概率分位数（悲观值 / 中位值 / 乐观值），字段名只该出现在
    数据契约与模型交互里。此前它们被原样印在报告正文与流程时间线上
    （「类案判赔区间：P10 8 万 / P50 25 万 / P90 60 万」），非专业使用人既读
    不懂，也容易把三个数误当成三份并列的报价。

    措辞若散落在 orchestrator 与 report_generator 两处，改一处漏一处，界面上
    就会出现「偏保守」与「保守估计」两个说法并存。这里是后端的唯一事实源：
    orchestrator（流程时间线）与 report_generator（报告正文，docx / pdf 同源）
    都从这里取词。前端另有一份 TS 常量 frontend/src/lib/damagesWording.ts，
    措辞必须与本文件保持一致——改这里时同步改那份。

只做展示，不参与任何评分与聚合。
"""

from typing import Any, Dict, Optional

# ── 三档说法 ──────────────────────────────────────────────
# label = 面向使用人的叫法；explain = 首次出现时随附的一句解释
# （之后只说 label，不再重复解释，否则每处都拖一句会显得啰嗦）
P10_LABEL = "保守估计"
P50_LABEL = "最可能"
P90_LABEL = "争取上限"

P10_EXPLAIN = "十件同类案件里最差的一件也能拿到的水平"
P50_EXPLAIN = "一半的同类案件判得比它多、一半比它少"
P90_EXPLAIN = "十件里只有一件能超过，需要证据和庭审都顺利"

_TRIPLE = (
    ("p10", P10_LABEL, P10_EXPLAIN),
    ("p50", P50_LABEL, P50_EXPLAIN),
    ("p90", P90_LABEL, P90_EXPLAIN),
)

# ── 规模支撑度 ────────────────────────────────────────────
# 模型返回的是 high/medium/low 英文枚举，原样印进报告同样属于术语裸露。
SCALE_SUPPORT_LABEL: Dict[str, str] = {"high": "强", "medium": "中", "low": "弱"}

# ── 维权成本口径 ──────────────────────────────────────────
# 与 evaluate_nodes 判赔 prompt 里「按 8-15 万估」保持一致，改一处要改两处。
COST_RANGE_TEXT = "约 8–15 万元"
COST_ITEMS_TEXT = "律师费、诉讼费、公证取证费等"


def _fmt_num(v: Any) -> Optional[str]:
    """整数不带小数尾巴（78.0 → 78），与 orchestrator._fmt_score 同一口径。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return str(int(f)) if f == int(f) else f"{f:.1f}"


def _fmt_amount(v: Any) -> Optional[str]:
    """金额文案；单位统一写「万元」，不再用「万」这种半截说法。"""
    s = _fmt_num(v)
    return None if s is None else f"{s} 万元"


def damages_triple(damages: Dict[str, Any], with_explain: bool = False) -> str:
    """
    「保守估计 8 万元、最可能 25 万元、争取上限 60 万元」。

    缺失的档位自动省略——模型偶尔只给 p50，硬凑三档会印出「— 万元」。
    """
    parts = []
    for key, label, explain in _TRIPLE:
        amount = _fmt_amount(damages.get(key) if isinstance(damages, dict) else None)
        if amount is None:
            continue
        text = f"{label} {amount}"
        if with_explain:
            text += f"（{explain}）"
        parts.append(text)
    return "、".join(parts)


def return_multiple_sentence(damages: Dict[str, Any]) -> str:
    """回报倍数：说清分母是什么，否则「2.1 倍」没有参照系。"""
    if not isinstance(damages, dict):
        return ""
    s = _fmt_num(damages.get("return_multiple"))
    if s is None:
        return ""
    return (f"相对预估的维权投入（{COST_ITEMS_TEXT}，{COST_RANGE_TEXT}），"
            f"大致能收回 {s} 倍。")


def scale_support_sentence(damages: Dict[str, Any]) -> str:
    """规模支撑度：英文枚举翻成中文，weak 档额外提示判赔可能贴下限。"""
    if not isinstance(damages, dict):
        return ""
    raw = damages.get("scale_support")
    label = SCALE_SUPPORT_LABEL.get(raw or "")
    if not label:
        return ""
    tail = "——规模证据偏弱，判赔可能贴着下限走。" if raw == "low" else "。"
    return f"案情里交代的侵权规模对高判赔的支撑度为「{label}」{tail}"


def damages_report_line(damages: Dict[str, Any]) -> str:
    """
    报告正文（一整句，首次出现带解释）。

    分位数与倍数都拿不到时返回空串，由调用方决定要不要渲染这一行——
    印一个「——」占位反而让人以为系统没算出来。
    """
    if not isinstance(damages, dict):
        return ""
    triple = damages_triple(damages, with_explain=True)
    multiple = return_multiple_sentence(damages)
    scale = scale_support_sentence(damages)

    if triple:
        line = f"参考同类案件的判赔水平，给出三档估算：{triple}。"
        if multiple:
            line += multiple
    else:
        line = multiple
    if scale:
        line += scale
    return line


def damages_step_text(damages: Dict[str, Any]) -> str:
    """
    流程时间线（短句，不带解释）。

    时间线一行只能放一句话，解释留给报告正文；这里只要让人看一眼知道
    「算完了、大概多少钱」就够了。
    """
    if not isinstance(damages, dict):
        return "判赔规模已完成"
    score = _fmt_num(damages.get("score"))
    head = f"判赔规模完成：{score} 分" if score is not None else "判赔规模已完成"
    triple = damages_triple(damages)
    return head + (f"，{triple}" if triple else "")
