"""
伴随式追问顾问路由（§6.4 唯一对话 Agent）
- POST /{case_id}/chat：SSE 流式对话（recall → delta* → done）
- GET /{case_id}/history：历史对话
"""

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core import advisor
from core.database import Case, get_db
from routers.evaluation import _load_ctx, _save_ctx

router = APIRouter()


class ChatRequest(BaseModel):
    question: str


@router.post("/{case_id}/chat")
def chat(case_id: str, payload: ChatRequest, db: Session = Depends(get_db)):
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(404, "案件不存在")
    if not payload.question.strip():
        raise HTTPException(400, "问题不能为空")

    ctx = _load_ctx(case)
    ctx.case_id = case_id

    def stream():
        result = {"answer": "", "recall": []}
        try:
            gen = advisor.chat(db, ctx, payload.question)
            while True:
                try:
                    event = next(gen)
                except StopIteration as stop:
                    result = stop.value or result
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'event': 'error', 'error': str(e)}, ensure_ascii=False)}\n\n"
            return

        # 历史落 CaseContext（含召回出处，随 context_json 持久化）
        ctx.advisor_messages.append({
            "role": "user", "content": payload.question.strip(),
        })
        ctx.advisor_messages.append({
            "role": "assistant", "content": result["answer"],
            "recall": result["recall"],
        })
        ctx.log_event("advisor_query", content=payload.question.strip(),
                      effect=(f"顾问已回答（召回 {len(result['recall'])} 条知识库材料）"
                              if result["recall"] else "顾问已回答（无知识库命中）"))
        try:
            _save_ctx(db, case, ctx)
        except Exception:
            pass  # 历史保存失败不阻断对话流
        yield f"data: {json.dumps({'event': 'done'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.get("/{case_id}/history")
def history(case_id: str, db: Session = Depends(get_db)):
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(404, "案件不存在")
    ctx = _load_ctx(case)
    return {"messages": ctx.advisor_messages}
