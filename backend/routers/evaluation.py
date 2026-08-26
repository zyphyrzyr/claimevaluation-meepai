"""
评估路由：启动评估（SSE 进度推送）、查询结果、节点级重跑
"""

import json
import queue
import threading
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.case_context import CaseContext
from core.database import Case, ScoreSnapshot, AuditEvent, get_db
from core.orchestrator import Orchestrator, NODE_ORDER, NODE_LABELS

router = APIRouter()


def _load_ctx(case: Case) -> CaseContext:
    ctx = CaseContext.from_dict(case.context_json or {})
    ctx.case_id = case.id
    return ctx


def _save_ctx(db: Session, case: Case, ctx: CaseContext) -> None:
    case.context_json = ctx.to_dict()
    for event in ctx.audit_trail:
        db.add(AuditEvent(case_id=case.id, event_type=event["event_type"],
                          node=event.get("node"), content=event.get("content", ""),
                          effect=event.get("effect", "")))
    ctx.audit_trail.clear()   # 已落库，避免重复写入
    syn = ctx.dimension_results.get("synthesize", {})
    if syn.get("status") in ("ok", "partial", "blocked"):
        latest = (db.query(ScoreSnapshot)
                  .filter(ScoreSnapshot.case_id == case.id)
                  .order_by(ScoreSnapshot.version.desc()).first())
        db.add(ScoreSnapshot(
            case_id=case.id,
            legal_score=ctx.scores.get("legal_feasibility"),
            business_score=ctx.scores.get("business_expectation"),
            evidence_score=ctx.evidence_completeness,
            confidence_score=ctx.confidence,
            final_score=ctx.scores.get("final"),
            recommendation=ctx.recommendation.get("recommendation", ""),
            dimension_json=ctx.dimension_results,
            version=(latest.version + 1) if latest else 1,
        ))
    db.commit()


@router.get("/nodes")
def nodes_meta():
    return {"order": NODE_ORDER, "labels": NODE_LABELS}


@router.post("/{case_id}/run")
def run_evaluation(case_id: str, db: Session = Depends(get_db)):
    """启动完整评估，SSE 推送节点进度"""
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(404, "案件不存在")

    ctx = _load_ctx(case)
    events: queue.Queue = queue.Queue()

    def work():
        try:
            case.status = "evaluating"
            db.commit()
            orch = Orchestrator(ctx, on_event=events.put)
            orch.run_all()
            case.status = "completed" if ctx.scores.get("final") is not None else "partial"
            if ctx.recommendation.get("level") == "block":
                case.status = "blocked"
            _save_ctx(db, case, ctx)
            events.put({"event": "flow_finished", "node": "",
                        "label": "", "status": case.status})
        except Exception as e:
            events.put({"event": "flow_error", "node": "", "label": "", "error": str(e)})
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


@router.get("/{case_id}/result")
def evaluation_result(case_id: str, db: Session = Depends(get_db)):
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(404, "案件不存在")
    ctx = _load_ctx(case)
    return {
        "case_id": case.id,
        "status": case.status,
        "scores": ctx.scores,
        "confidence": ctx.confidence,
        "recommendation": ctx.recommendation,
        "evidence": {
            "completeness": ctx.evidence_completeness,
            "matrix": ctx.evidence_matrix,
            "gap_list": ctx.gap_list,
            "extra_evidence": ctx.extra_evidence,
            "note": ctx.evidence_note,
        },
        "red_flags": ctx.red_flags,
        "dimension_results": ctx.dimension_results,
        "defendant_profile": {
            "recovery_ability": ctx.recovery_ability,
            "metrics": (ctx.defendant_profile or {}).get("metrics", {}),
        },
        "correction_coeff": ctx.correction_coeff,
    }


class RerunRequest(BaseModel):
    node: str
    guidance: str = ""


@router.post("/{case_id}/rerun")
def rerun_node(case_id: str, payload: RerunRequest, db: Session = Depends(get_db)):
    """节点级重跑（§6.4）：引导注入 + 重跑 + 下游失效传播 + 规则环节瞬时重算"""
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(404, "案件不存在")
    ctx = _load_ctx(case)
    orch = Orchestrator(ctx)
    try:
        orch.rerun_node(payload.node, guidance=payload.guidance)
    except ValueError as e:
        raise HTTPException(400, str(e))
    _save_ctx(db, case, ctx)
    return {"ok": True, "dimension_results": ctx.dimension_results,
            "scores": ctx.scores, "recommendation": ctx.recommendation}
