"""
伴随式追问顾问（§6.4，系统内唯一对话 Agent）
- 悬浮于全流程任意页面：评估前/中/后都可提问
- 每次提问自动召回：本案材料库 + 全局经验库（自动召回，无需手动勾选）
- 案件状态（评分/缺口/证据矩阵）注入 system prompt，回答对齐当前评估进展
- 历史落 CaseContext.advisor_messages（context_json 持久化）

事件流：{"event": "recall", "refs": [...]} → {"event": "delta", "text": "..."}* → {"event": "done"}
"""

import time
from typing import Any, Dict, Generator

from sqlalchemy.orm import Session

from .case_context import CaseContext
from .config import get_runtime_settings
from .knowledge import recall_for_context


def _case_brief(ctx: CaseContext) -> str:
    lines = [
        f"案由：{ctx.cause_type}；业务目标：{ctx.goal_type}",
        f"案情摘要：{(ctx.case_description or '')[:600]}",
    ]
    if ctx.scores:
        s = ctx.scores
        lines.append(
            f"当前评估结果：法律可行性 {s.get('legal_feasibility')} / "
            f"业务预期 {s.get('business_expectation')} / 决策分 {s.get('final')} / "
            f"置信度 {ctx.confidence}")
    if ctx.gap_list:
        gaps = "；".join(g.get("item", "") for g in ctx.gap_list[:5])
        lines.append(f"证据缺口：{gaps}")
    if ctx.correction_coeff != 1.0:
        lines.append(f"模拟法庭修正系数：{ctx.correction_coeff}")
    if ctx.user_viewpoints:
        lines.append(f"用户既有观点：{'；'.join(ctx.user_viewpoints[:3])}")
    return "\n".join(lines)


def _history_text(ctx: CaseContext, max_turns: int = 6) -> str:
    msgs = ctx.advisor_messages[-max_turns * 2:]
    if not msgs:
        return "（无）"
    return "\n".join(f"{'用户' if m['role'] == 'user' else '顾问'}：{m['content'][:400]}"
                     for m in msgs)


_SYSTEM = (
    "你是知识产权主诉评估系统的伴随式追问顾问（资深知识产权律师视角）。"
    "用户可在评估的任意阶段向你提问。回答要求："
    "① 紧扣本案当前评估状态（评分、证据缺口、模拟法庭结论）；"
    "② 优先引用【知识库召回材料】中与问题相关的内容，注明出处条目名；"
    "③ 回答简洁、可执行，直接给判断与下一步动作，避免泛泛而谈；"
    "④ 你只做解释和建议，不直接修改评分（评分修改须走节点重跑）。"
)


def _mock_answer(question: str, recall: Dict[str, Any], ctx: CaseContext) -> str:
    refs = recall.get("refs") or []
    ref_part = ""
    if refs:
        ref_part = "\n\n参考依据（知识库自动召回）：\n" + "\n".join(
            f"- 《{r['title']}》（相似度 {r['score']}）" for r in refs)
    scores = ctx.scores or {}
    gap_count = len(ctx.gap_list or [])
    return (
        f"关于「{question[:60]}」，结合本案当前状态给你三点判断：\n\n"
        f"1. 当前评估进展：法律可行性 {scores.get('legal_feasibility', '尚未评估')}、"
        f"业务预期 {scores.get('business_expectation', '尚未评估')}、"
        f"决策分 {scores.get('final', '尚未评估')}，证据缺口 {gap_count} 项；"
        f"{"整体处于可推进区间，短板集中在证据侧。" if gap_count else "证据链较为完整。"}\n"
        f"2. 针对你的问题，建议优先核对该问题对应的证据是否已在缺口清单中——"
        f"若在，先补证再启动；若不在，可将本条讨论作为观点注入后重跑相关节点（评估页支持节点级重跑）。\n"
        f"3. 如需让本讨论影响评分结论，请把关键结论提炼为一句话观点注入（建案页或节点重跑引导均可）。"
        f"{ref_part}"
    )


def chat(db: Session, ctx: CaseContext, question: str) -> Generator[Dict[str, Any], None, Dict[str, Any]]:
    """
    一次顾问对话。yield 事件流；返回值为 {"answer": 完整回答, "recall": refs}。
    调用方负责把回答追加进 ctx.advisor_messages 并落库。
    """
    question = (question or "").strip()
    if not question:
        return {"answer": "", "recall": []}

    # ① 自动召回（案件材料库 + 全局经验库）
    recall = recall_for_context(db, ctx.case_id, question, top_k=3)
    if recall["refs"]:
        yield {"event": "recall", "refs": recall["refs"]}

    # ② 生成回答（流式）
    answer_parts: list = []

    if get_runtime_settings()["use_mock"]:
        text = _mock_answer(question, recall, ctx)
        for i in range(0, len(text), 24):
            chunk = text[i:i + 24]
            answer_parts.append(chunk)
            yield {"event": "delta", "text": chunk}
            time.sleep(0.05)
    else:
        from .llm_gateway import stream_text
        user_prompt = f"""## 本案当前状态
{_case_brief(ctx)}

## 知识库召回材料
{recall["context"] or "（无命中）"}

## 最近对话
{_history_text(ctx)}

## 用户提问
{question}"""
        for chunk in stream_text(_SYSTEM, user_prompt, node="advisor"):
            answer_parts.append(chunk)
            yield {"event": "delta", "text": chunk}

    return {"answer": "".join(answer_parts), "recall": recall["refs"]}
