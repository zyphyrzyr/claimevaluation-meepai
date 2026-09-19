"""
《诉算类案检索规则 v1.1》四顺位级联检索。

规则要点（来自规则文档，落地时不得擅自改动）：
    1. 效力层级优先：① 最高法指导性案例 → ② 最高法典型案例/生效裁判 →
       ③ 本省高院典型案例/生效裁判 → ④ 上一级法院/本院生效裁判
    2. 时间优先：除顺位①与顺位③-A 外，其余一律限定近五年；同一档内按审结日期从新到旧
    3. 数量控制：去重后累计 ≥ 5 即停止后续顺位；单个顺位凑够 10 个也停
    4. 不足 5 个时按实际命中返回，并显式标注「类案数量不足（n 个）」

三条工程约束（2026-09-19 与用户确认）：
    - 单次级联硬上限 6 次 RPC、单次调用超时 10s、整体 25s
    - 任何一个顺位拿不到所需参数（例如没有省份、没有管辖法院）就**跳过并留痕**，
      不伪造参数、不退化为「不过滤」（空字符串进接口等于不筛选，是最危险的静默失真）
    - 顺位标记随结果返回，让报告能说出「这个案例来自哪一档」
"""

import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..config import (CAUSE_COPYRIGHT, CAUSE_TRADEMARK,
                      CAUSE_UNFAIR_COMPETITION, SUPPORTED_CAUSE_TYPES)
from . import pkulaw_api
from .court_resolver import derive_upper_court, resolve_court_targets

MAX_CALLS_PER_RUN = 6
PER_CALL_TIMEOUT = 10
TOTAL_TIMEOUT_SECONDS = 25
TARGET_MIN = 5
TARGET_MAX = 10

# base 参数：三案由均为民事、裁判说理以判决书为主（规则文档第二节）
BASE_ARGS = {"case_type": "民事案件", "doc_type": "判决书"}

# 案由相关性关键词：比对该案例的「案由」与「标题」
#
# 为什么要这道门：2026-09-19 实跑发现，顺位①（`case_grade=指导性案例`）会把
# **语义沾边但案由无关**的指导案例一并返回——检索「商标侵权 混淆可能性」，
# 10 条结果里混进了环境污染、植物新品种的指导案例。若照规则原文「凑够 5 个即停」，
# 我们会在顺位①就凑满并停止，反而拿不到顺位②③里大量真正对口的裁判。
#
# 于是这里把**停止规则的计数口径改成「与本案由相关的条数」**：
# 不相关的案例不会被丢弃（仍随结果返回，报告可追溯），但不计入达标数，
# 级联得以继续下行到第 ②、③ 档去找真正相关的类案。
CAUSE_RELEVANCE_KEYWORDS = {
    CAUSE_TRADEMARK: ("商标",),
    CAUSE_COPYRIGHT: ("著作权", "作品", "计算机软件", "信息网络传播"),
    CAUSE_UNFAIR_COMPETITION: ("不正当竞争", "商业秘密", "仿冒", "垄断"),
}


def relevance_score(case: Dict[str, Any], cause_type: str) -> int:
    """案由相关性打分：0 表示看着像、实则与本案由无关。"""
    keywords = CAUSE_RELEVANCE_KEYWORDS.get(cause_type or "")
    if not keywords:
        return 1  # 未指定案由时不筛——宁可不筛，也不用别的案由的词去筛
    text = f"{case.get('cause') or ''} {case.get('title') or ''}"
    return sum(1 for k in keywords if k in text)


@dataclass
class TierCall:
    code: str            # ②-A
    label: str           # 最高法典型案例
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Tier:
    code: str            # ②
    label: str
    calls: List[TierCall]


@dataclass
class LadderSession:
    """一次评估内的检索预算账本。

    为什么要有会话：评估链路里有多个节点要类案（侵权认定、模拟法庭抗辩……）。
    若各节点各自跑满级联，一次评估就是二十几次 RPC，串行起来足以让演示超时。
    会话把所有节点的调用记在同一本账上，超预算后节点得到的是显式的
    「已达预算上限」而不是一次无声的降级检索。

    默认 12 次 = 单次级联上限（6）× 2：一次评估里通常只有「侵权认定」与
    「模拟法庭」两个节点要类案，各自的实际停止点又多在 2–3 次，留一倍余量。
    实测（2026-09-19）单次级联 1.8s / 2 次调用；真跑到上限会把 budget_exhausted
    显式写进报告而不是悄悄少检索。
    """
    max_calls: int = MAX_CALLS_PER_RUN * 2
    started_at: float = field(default_factory=time.time)
    calls_used: int = 0

    def remaining(self) -> int:
        return max(0, self.max_calls - self.calls_used)

    def elapsed(self) -> float:
        return time.time() - self.started_at

    def can_call(self) -> bool:
        return self.remaining() > 0 and self.elapsed() < TOTAL_TIMEOUT_SECONDS


def build_tier_plan(province: Optional[str] = None,
                    court: Optional[str] = None,
                    since: Optional[str] = None) -> Tuple[List[Tier], List[Dict[str, str]]]:
    """按可用输入生成四顺位调用计划。

    返回 (可执行的顺位列表, 被跳过的顺位及原因)。参数不齐的顺位不进计划——
    宁可少检索一档，也不让一个可能错的法院名把结果带偏。
    """
    targets = resolve_court_targets(province, court)
    skipped: List[Dict[str, str]] = []
    tiers: List[Tier] = []

    tiers.append(Tier("①", "最高法指导性案例", [
        TierCall("①", "最高法指导性案例", {"case_grade": "指导性案例"}),
    ]))

    tiers.append(Tier("②", "最高法典型案例 / 生效裁判", [
        TierCall("②-A", "最高法典型案例",
                 {"case_grade": "典型案例", "decision_date_start": since}),
        TierCall("②-B", "最高法生效裁判",
                 {"courthouse_name": "最高人民法院", "decision_date_start": since}),
    ]))

    if targets["high_court"]:
        calls = [
            TierCall("③-A", "本省高院典型案例 / 参考案例",
                     {"case_grade": "典型案例", "courthouse_province": province}),
            TierCall("③-A2", "本省高院参考案例",
                     {"case_grade": "参考案例", "courthouse_province": province}),
            TierCall("③-B", "本省高院生效裁判",
                     {"courthouse_name": targets["high_court"],
                      "decision_date_start": since}),
        ]
    else:
        calls = []
        skipped.append({"code": "③", "reason": "未识别到本省，无法换算本省高级人民法院全称"})
    tiers.append(Tier("③", "本省高院典型案例 / 生效裁判", calls))

    if court and (targets["upper_court"] or targets["self_court"]):
        calls = []
        if targets["upper_court"]:
            calls.append(TierCall("④-A", "上一级法院生效裁判",
                                  {"courthouse_name": targets["upper_court"],
                                   "decision_date_start": since}))
        if targets["self_court"]:
            calls.append(TierCall("④-B", "本院生效裁判",
                                  {"courthouse_name": targets["self_court"],
                                   "decision_date_start": since}))
    else:
        calls = []
        skipped.append({"code": "④",
                        "reason": derive_upper_court(court)[1] if court
                                  else "未提供管辖法院，无法推导上一级法院 / 本院"})
    tiers.append(Tier("④", "上一级法院 / 本院生效裁判", calls))

    return tiers, skipped


def _case_key(c: Dict[str, Any]) -> Tuple:
    """三级回退主键：案号 → 法院+标题 → 标题。

    为什么不能只用案号：实跑发现 `case_grade=典型案例` 返回的多是法院发布的
    「XX 法院发布 10 起典型案例之八」式汇编条目，**大多没有案号**。按案号去重会
    把整个最高权威顺位判成重复丢掉——规则文档第五节说「按案号去重」，落地必须补这层回退。
    """
    ahao = (c.get("ahao") or "").strip()
    if ahao:
        return ("ahao", ahao)
    court = (c.get("court") or "").strip()
    title = (c.get("title") or "").strip()
    if court and title:
        return ("court_title", court, title[:30])
    if title:
        return ("title", title[:60])
    return ("raw", repr(c)[:160])


def dedup_cases(cases: List[Dict[str, Any]],
                seen: Optional[set] = None) -> Tuple[List[Dict[str, Any]], set]:
    """跨顺位去重；返回 (去重后列表, 累计主键集合)。"""
    seen = set() if seen is None else seen
    out: List[Dict[str, Any]] = []
    for c in cases:
        key = _case_key(c)
        if key in seen:
            continue
        seen.add(key)
        c = dict(c)
        c["dedup_key"] = "案号" if key[0] == "ahao" else (
            "法院+标题" if key[0] == "court_title" else "标题")
        c["has_ahao"] = bool((c.get("ahao") or "").strip())
        out.append(c)
    return out, seen


def sort_by_date_desc(cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """同一顺位内按审结日期从新到旧。

    服务端不排序（实跑结果顺序为 2009/2014/2013/2008/2019），这一步只能在客户端做。
    没有日期的条目沉到最后，而不是被丢弃。
    """
    dated = [c for c in cases if _date_of(c)]
    undated = [c for c in cases if not _date_of(c)]
    dated.sort(key=_date_of, reverse=True)
    return dated + undated


def _date_of(c: Dict[str, Any]) -> str:
    d = (c.get("date") or "").strip()
    return d if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) else ""


def retrieve_similar_cases(text: str,
                           cause_type: str = "",
                           session: Optional[LadderSession] = None,
                           province: Optional[str] = None,
                           court: Optional[str] = None,
                           size: int = 10,
                           target: int = TARGET_MIN,
                           since: Optional[str] = None) -> Dict[str, Any]:
    """按四顺位逐档检索，凑够 target 个即停。

    返回结构里 `cases` 每条都带 `tier_code` / `tier_label`，`skipped` 显式说明哪些顺位
    没跑及原因——「不知道被告在哪个省」和「该省高院没有类案」在报告里必须是两件事。
    """
    session = session or LadderSession(max_calls=MAX_CALLS_PER_RUN)
    since = since or pkulaw_api.five_years_ago()
    if cause_type and cause_type not in SUPPORTED_CAUSE_TYPES:
        # 未知案由显式报错，不静默回落——用别的案由的关键词去筛，等于把噪声当信号
        raise ValueError(
            f"不支持的案由：{cause_type}；当前支持 {SUPPORTED_CAUSE_TYPES}")
    tiers, skipped = build_tier_plan(province, court, since)

    result: Dict[str, Any] = {
        "status": "ok",
        "cases": [],
        "tier_counts": {},
        "executed_calls": 0,
        "skipped": skipped,
        "budget_exhausted": False,
        "time_exhausted": False,
        "errors": [],
        "target": target,
    }

    seen: set = set()
    done = False
    for tier in tiers:
        if not tier.calls or done:
            continue
        tier_hits: List[Dict[str, Any]] = []
        aborted = False
        for call in tier.calls:
            if not session.can_call():
                if session.elapsed() >= TOTAL_TIMEOUT_SECONDS:
                    result["time_exhausted"] = True
                else:
                    result["budget_exhausted"] = True
                aborted = True
                break
            args = dict(BASE_ARGS)
            args["text"] = text
            args["size"] = max(1, min(size, TARGET_MAX))
            args.update({k: v for k, v in call.extra.items() if v not in (None, "")})
            rpc = pkulaw_api._rpc_call("search_case", args, timeout=PER_CALL_TIMEOUT)
            session.calls_used += 1
            result["executed_calls"] += 1

            fail = pkulaw_api._rpc_failed(rpc)
            if fail:
                # 参数校验失败是**我们自己的 bug**（键名拼错会被接口报 pydantic error）。
                # 继续用同样的参数重试只会重复报错——就地中止，把原因显式抛给管理层。
                result["errors"].append(f"{call.code} {fail}")
                result["status"] = "error"
                aborted = True
                break
            batch, seen = dedup_cases(pkulaw_api._extract_items(rpc), seen)
            for c in batch:
                c["tier_code"] = call.code
                c["tier_label"] = call.label
                c["relevant"] = relevance_score(c, cause_type) > 0
            tier_hits.extend(sort_by_date_desc(batch))

            # 达标口径是「与本案由相关的条数」——按原始条数达标会让级联在顺位①就停住，
            # 而那一档的高权威案例恰恰最容易混进案由无关的指导案例。
            # 每次调用后就检查（而不是等整档跑完）：2-A 已经够用时不必再打 2-B，省一次 RPC。
            if (_relevant_count(result["cases"])
                    + _relevant_count(tier_hits)) >= target:
                done = True
                break

        _merge_tier(result, tier, tier_hits)
        if aborted or done:
            break

    result["total"] = len(result["cases"])
    _prioritize_relevant(result)
    result["relevant_total"] = _relevant_count(result["cases"])
    result["dropped_irrelevant"] = result["total"] - result["relevant_total"]
    if result["relevant_total"] < TARGET_MIN:
        result["insufficient"] = True
        result["insufficient_note"] = f"类案数量不足（{result['total']} 个）"
    else:
        result["insufficient"] = False
        result["insufficient_note"] = ""

    if result["executed_calls"] == 0 and result["status"] == "ok":
        result["status"] = "skipped"
    result["_summary"] = _summarize(result)
    return result


def _relevant_count(cases: List[Dict[str, Any]]) -> int:
    return sum(1 for c in cases if c.get("relevant"))


def _prioritize_relevant(result: Dict[str, Any]) -> None:
    """相关案例排前、其余排后；组内仍按顺位与日期的原顺序。

    为什么会有一个「排序」而不是「过滤」：不相关的案例不是错误，它们是真实的
    命中，报告里应当可查（读者能看出检索过哪些档）。但注入模型 prompt 时只会取前几条，
    排序保证了那几条一定是对口的。丢弃则可能让读者误以为「本省高院没有类案」。
    """
    cases = result.get("cases") or []
    relevant = [c for c in cases if c.get("relevant")]
    others = [c for c in cases if not c.get("relevant")]
    result["cases"] = relevant + others


def _merge_tier(result: Dict[str, Any], tier: Tier, hits: List[Dict[str, Any]]) -> None:
    if hits:
        result["cases"].extend(hits)
        result["tier_counts"][tier.code] = (
            result["tier_counts"].get(tier.code, 0) + len(hits))


def _summarize(result: Dict[str, Any]) -> str:
    parts = [f"类案命中 {result['total']} 个"
             f"（其中与本案由相关 {result.get('relevant_total', 0)} 个）"]
    if result["tier_counts"]:
        parts.append("顺位分布：" + "、".join(
            f"{k} {v} 个" for k, v in result["tier_counts"].items()))
    if result["skipped"]:
        parts.append("已跳过：" + "；".join(
            f"{s['code']}（{s['reason']}）" for s in result["skipped"]))
    if result["insufficient_note"]:
        parts.append(result["insufficient_note"])
    if result["budget_exhausted"]:
        parts.append("已达检索预算上限，未继续后续顺位")
    if result["time_exhausted"]:
        parts.append("已达检索时限，未继续后续顺位")
    for e in result["errors"]:
        parts.append(f"⚠️ {e}")
    return "；".join(parts)
