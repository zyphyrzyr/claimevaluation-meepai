"""
模拟法庭外部依据聚合层（moot_court/context_sources.py）

为三方（原告/被告/法官）统一产出「可引用的公开依据」：
  - statutory_basis : legal_basis.moot_clause 按案由法定基准（零成本，始终可用）
  - pkulaw_laws     : 评估流程已落盘的北大法宝法条（load_results 复用，仅内嵌/带 case_id）
  - pkulaw_cases    : 评估流程已落盘的北大法宝类案
  - experience      : 个人经验库 RAG 召回（案件材料库 + 全局经验库）

设计约束（用户拍板）：
  - 内嵌 / 带 case_id：优先复用评估流程的北大法宝结果，不重复调用。
  - 独立演练（无 case_id）或北大法宝结果缺失：不触发 on-demand 调用，
    仅用 legal_basis 兜底 + 经验库召回；类案缺失时显式告知模型不得编造。
"""

from typing import Any, Dict, List, Optional

from ..config import SUPPORTED_CAUSE_TYPES
from ..legal_basis import moot_clause
from ..pkulaw import pkulaw_integration


def gather_legal_context(
    case_id: Optional[str],
    cause_type: str,
    case_description: str = "",
    db=None,
    user_id: Optional[str] = None,
    top_k: int = 3,
) -> Dict[str, Any]:
    """聚合模拟法庭三方共享的公开依据。

    返回结构化 dict：
    {
      "statutory_basis": str,
      "pkulaw_laws":   List[Dict],   # {title, content}
      "pkulaw_cases":  List[Dict],   # {title, court, date, summary}
      "experience":    str,          # RAG 拼接文本
      "refs":          List[Dict],   # 供前端 recall 事件展示
      "block":         str,          # 拼好的 prompt 片段（注入 system prompt）
    }
    """
    if cause_type not in SUPPORTED_CAUSE_TYPES:
        # 与全栈一致：未知案由显式报错，不静默回落商标
        raise ValueError(
            f"不支持的案由：{cause_type}；当前支持 {SUPPORTED_CAUSE_TYPES}")

    # —— 法定基准：始终有，零成本兜底 ——
    statutory = moot_clause(cause_type)

    # —— 北大法宝：优先复用评估流程落盘结果，绝不 on-demand ——
    pkulaw_laws: List[Dict] = []
    pkulaw_cases: List[Dict] = []
    if case_id:
        results = pkulaw_integration.load_results(case_id)
        if results:
            for law in (results.get("law_results") or []):
                title = law.get("title") or law.get("name") or ""
                content = law.get("content") or law.get("text") or ""
                if title or content:
                    pkulaw_laws.append({"title": title, "content": content})
            for c in (results.get("case_results") or []):
                pkulaw_cases.append({
                    "title": c.get("title") or c.get("name") or "",
                    "court": c.get("court") or c.get("courthouse_name") or "",
                    "date": c.get("date") or c.get("decision_date") or "",
                    "summary": c.get("summary") or c.get("abstract") or "",
                })

    # —— 经验库：RAG 召回（案件材料库 + 全局经验库）——
    experience = ""
    refs: List[Dict] = []
    if db is not None:
        try:
            from ..knowledge import recall_for_context
            query = (case_description or "")[:300]
            recall = recall_for_context(db, case_id, query, top_k=top_k, user_id=user_id)
            experience = recall.get("context", "")
            refs = recall.get("refs", [])
        except Exception:
            # 召回失败不应阻断庭审；依据缺一块比整体崩掉好
            pass

    block = render_legal_context_block(
        statutory=statutory,
        pkulaw_laws=pkulaw_laws,
        pkulaw_cases=pkulaw_cases,
        experience=experience,
    )
    return {
        "statutory_basis": statutory,
        "pkulaw_laws": pkulaw_laws,
        "pkulaw_cases": pkulaw_cases,
        "experience": experience,
        "refs": refs,
        "block": block,
    }


def render_legal_context_block(
    statutory: str,
    pkulaw_laws: Optional[List[Dict]] = None,
    pkulaw_cases: Optional[List[Dict]] = None,
    experience: str = "",
) -> str:
    """把聚合结果渲染成可注入 system prompt 的「可引用依据」片段。

    关键纪律：任何依据缺失都显式标注「无」，并禁止模型虚构 —— 避免
    「仅模拟法庭」场景下模型为了显得专业而编造法条/案号。
    """
    pkulaw_laws = pkulaw_laws or []
    pkulaw_cases = pkulaw_cases or []
    parts: List[str] = []

    parts.append(
        "## 可引用依据（仅可引用以下内容，严禁编造条号、案号或不存在的条文）")

    # 一、法定基准（始终有）
    parts.append("\n### 一、本案由法定基准（事实性依据，可直接引用）\n" + statutory)

    # 二、法律法规
    if pkulaw_laws:
        law_lines = []
        for i, law in enumerate(pkulaw_laws[:8], 1):
            head = f"【法条 {i}】{law['title']}" if law["title"] else f"【法条 {i}】"
            body = law["content"] if law["content"] else ""
            law_lines.append((head + ("\n" + body if body else "")).strip())
        parts.append("\n### 二、法律法规（北大法宝检索，可引用）\n"
                     + "\n\n".join(law_lines))
    else:
        parts.append(
            "\n### 二、法律法规（北大法宝）\n"
            "（本案无外部法条检索依据，请基于上方「法定基准」论证，不得编造条文）")

    # 三、参考类案
    if pkulaw_cases:
        case_lines = []
        for i, c in enumerate(pkulaw_cases[:5], 1):
            head = f"【类案 {i}】{c['title']}"
            meta = []
            if c["court"]:
                meta.append(c["court"])
            if c["date"]:
                meta.append(c["date"])
            if meta:
                head += "（" + "，".join(meta) + "）"
            body = c["summary"][:300] if c["summary"] else ""
            case_lines.append((head + ("\n" + body if body else "")).strip())
        parts.append("\n### 三、参考类案（北大法宝检索，可引用并标注案号）\n"
                     + "\n\n".join(case_lines))
    else:
        parts.append(
            "\n### 三、参考类案（北大法宝）\n"
            "（本案无外部类案检索依据，请基于法条与经验论证，不得虚构案号或类案）")

    # 四、个人经验库
    if experience:
        parts.append("\n### 四、个人经验库（RAG 召回，可结合办案思路）\n" + experience)

    # 防幻觉收尾
    parts.append(
        "\n⚠️ 引用纪律：上述依据之外的法条、案号、司法解释一律不得引用；"
        "某类依据标注为「无」时，不得虚构该类内容，应基于可引用依据论证，"
        "或明确说明依据不足。")
    return "\n".join(parts)
