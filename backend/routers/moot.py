"""
模拟法庭路由（P2 双模式）
- POST /api/moot/{case_id}/run   内嵌模式：评估完成后压力测试，系数回写 + 决策合成重算
- POST /api/moot/standalone      独立模式：手动组料纯演练，不回写评分
- GET  /api/moot/{case_id}       查询已保存的庭审记录
SSE 事件流：round（逐轮发言）→ moot_finished（法官归纳 + 系数）
"""

import json
import queue
import threading
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core import moot_service
from core.case_context import CaseContext
from core.database import Case, MootRound, get_db
from core.orchestrator import Orchestrator

router = APIRouter()


def _load_ctx(case: Case) -> CaseContext:
    ctx = CaseContext.from_dict(case.context_json or {})
    ctx.case_id = case.id
    return ctx


def _save_rounds(db: Session, case_id: Optional[str], rounds: list, mode: str) -> None:
    for i, r in enumerate(rounds):
        db.add(MootRound(
            case_id=case_id, mode=mode,
            round_type=f"{r.get('step', '')}-{r.get('step_name', '')}",
            speaker_role=r.get("role", ""),
            content=r.get("content", ""),
            source_refs={"step_name": r.get("step_name", ""), "role_name": r.get("role_name", "")},
        ))
    db.commit()


# ============================================================
# 内嵌模式
# ============================================================

@router.post("/{case_id}/run")
def run_embedded_moot(case_id: str, db: Session = Depends(get_db)):
    """内嵌模拟法庭：SSE 直播逐轮发言；结束后系数回写 + 决策合成重算"""
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(404, "案件不存在")
    ctx = _load_ctx(case)

    if ctx.scores.get("final") is None:
        raise HTTPException(400, "案件尚未完成评估，请先完成主诉评估再启动模拟法庭")

    events: queue.Queue = queue.Queue()
    result_holder: dict = {}

    def work():
        try:
            before = dict(ctx.scores)
            gen = moot_service.run_embedded(ctx)
            while True:
                try:
                    event = next(gen)
                except StopIteration:
                    break
                events.put(event)

            # 生成器内部已回写 correction_coeff 与 moot_transcript；此处重算合成（纯规则瞬时）
            if ctx.correction_coeff != 1.0 or ctx.moot_transcript:
                orch = Orchestrator(ctx)
                orch.run_node("synthesize")
            _save_rounds(db, case.id, ctx.moot_transcript, "embedded")
            case.context_json = ctx.to_dict()
            for ev in ctx.audit_trail:
                from core.database import AuditEvent
                db.add(AuditEvent(case_id=case.id, event_type=ev["event_type"],
                                  node=ev.get("node"), content=ev.get("content", ""),
                                  effect=ev.get("effect", "")))
            ctx.audit_trail.clear()
            db.commit()
            result_holder["scores"] = ctx.scores
            result_holder["before"] = before
            events.put({
                "event": "scores_updated",
                "before": before,
                "after": ctx.scores,
                "correction_coeff": ctx.correction_coeff,
            })
            events.put({"event": "moot_done"})
        except Exception as e:
            events.put({"event": "moot_error", "error": str(e)})
        finally:
            events.put(None)

    threading.Thread(target=work, daemon=True).start()

    def stream():
        while True:
            item = events.get()
            if item is None:
                break
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.get("/{case_id}")
def get_moot(case_id: str, db: Session = Depends(get_db)):
    """查询已保存的庭审记录与修正系数"""
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(404, "案件不存在")
    ctx = _load_ctx(case)
    return {
        "case_id": case.id,
        "transcript": ctx.moot_transcript,
        "correction_coeff": ctx.correction_coeff,
    }


# ============================================================
# 独立模式
# ============================================================

class StandaloneMootRequest(BaseModel):
    case_description: str
    cause_type: str = "商标侵权"
    viewpoints: List[str] = []
    plaintiff_points: str = ""    # 我方主张要点（可选，替代权利基础评估结论）


@router.post("/standalone")
def run_standalone_moot(payload: StandaloneMootRequest, db: Session = Depends(get_db)):
    """独立演练：不建案、不评估、不回写评分，输出演练报告"""
    if not payload.case_description.strip():
        raise HTTPException(400, "案情描述不能为空")

    events: queue.Queue = queue.Queue()

    def work():
        try:
            gen = moot_service.run_standalone(
                payload.case_description,
                cause_type=payload.cause_type,
                viewpoints=payload.viewpoints,
                plaintiff_points=payload.plaintiff_points,
            )
            final = None
            while True:
                try:
                    event = next(gen)
                except StopIteration:
                    break
                if event.get("event") == "moot_finished":
                    final = event
                events.put(event)
            # 庭审记录持久化（case_id 为空 = 独立演练，不挂在任何案件下）
            if final:
                _save_rounds(db, None, final.get("rounds", []), "standalone")
        except Exception as e:
            events.put({"event": "moot_error", "error": str(e)})
        finally:
            events.put(None)

    threading.Thread(target=work, daemon=True).start()

    def stream():
        while True:
            item = events.get()
            if item is None:
                break
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")
