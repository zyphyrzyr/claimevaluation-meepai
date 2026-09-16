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
from core.config import (SCORE_THRESHOLD_GO, SCORE_THRESHOLD_PATCH,
                         QUADRANT_AXIS_MID, POWER_MEAN_P)
from core.database import Case, ScoreSnapshot, AuditEvent, get_db
from core.orchestrator import (Orchestrator, NODE_ORDER, NODE_LABELS,
                               BUSINESS_DIMENSIONS, RERUNNABLE_NODES)

router = APIRouter()


def _load_ctx(case: Case) -> CaseContext:
    ctx = CaseContext.from_dict(case.context_json or {})
    ctx.case_id = case.id
    # 把已上传证据附件的元数据带进上下文，供红线门禁区分「未上传」与「已上传但系统未能读取」
    ctx.evidence_files_meta = [
        {"file_name": f.file_name, "file_type": f.file_type, "parse_status": f.parse_status}
        for f in (case.evidence_files or [])
    ]
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
    return {"order": NODE_ORDER, "labels": NODE_LABELS,
            "business_dimensions": BUSINESS_DIMENSIONS,
            "rerunnable": RERUNNABLE_NODES}


def _auto_recall(db: Session, case: Case, ctx: CaseContext) -> int:
    """后台静默召回（方案 B）：确定性查询词 → 双库检索 → 写入注入快照 + 审计留痕。

    - 查询词 = 案由 + 业务目标 + 案情描述截断，纯规则拼接，不用 LLM（确定性可复现）
    - 检索走 search_knowledge 双库（防跨案污染规则内建）；异常降级为空集，不阻塞评估
    - 覆盖 ctx.injected_knowledge（原手动注入字段），下游 evaluate_nodes 注入路径零改动
    """
    from core.knowledge import search_knowledge
    from core.knowledge import embeddings as emb
    from core.config import AUTO_RECALL_TOP_K, auto_recall_min_score

    parts = [
        case.cause_type or "",
        ctx.goal_type or "",
        (case.case_description or "")[:500],
    ]
    query = " ".join(p.strip() for p in parts if p.strip())
    if not query:
        ctx.injected_knowledge = []
        ctx.log_event("knowledge_auto_recall", content="(案情为空，无可用查询词)",
                      effect="未执行自动召回，注入集为空")
        return 0
    try:
        hits = search_knowledge(db, query, case_id=case.id, scope=None,
                                top_k=AUTO_RECALL_TOP_K)
    except Exception as e:
        ctx.injected_knowledge = []
        ctx.log_event("knowledge_auto_recall", content=f"召回异常: {e}",
                      effect="自动召回异常降级为空集，不阻塞评估")
        return 0

    # 合格线随后端自适应：哈希兜底向量分数系统性偏低，共用 0.3 会让经验库短条目全落空
    hash_backend = emb.use_mock_embedding()
    threshold = auto_recall_min_score(hash_backend)
    selected = [h for h in hits if float(h.get("score", 0)) >= threshold]
    ctx.injected_knowledge = [
        {"id": h["id"], "title": h["title"], "scope": h["scope"],
         "source_type": h.get("source_type", ""),
         "snippet": (h.get("content") or h.get("matched_chunk") or "")[:200]}
        for h in selected
    ]
    titles = "；".join(h["title"] for h in selected)
    ctx.log_event("knowledge_auto_recall",
                  content=f"查询词: {query[:150]}",
                  effect=(f"自动召回 {len(selected)}/{len(hits)} 条（top_k={AUTO_RECALL_TOP_K}, "
                          f"向量={'哈希兜底' if hash_backend else 'bge-m3'}, "
                          f"阈值={threshold}）：{titles[:200]}"))
    return len(selected)


@router.post("/{case_id}/run")
def run_evaluation(case_id: str, db: Session = Depends(get_db)):
    """启动完整评估，SSE 推送节点进度。

    2026-09-03 方案 B：材料注入后台化——启动时先执行自动召回
    （确定性查询词 + 双库语义检索），命中写入 ctx.injected_knowledge
    并留审计，替代原「评估准备页手动勾选」。用户无感，过程可查。
    """
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(404, "案件不存在")

    ctx = _load_ctx(case)
    events: queue.Queue = queue.Queue()

    def work():
        try:
            case.status = "evaluating"
            db.commit()
            recalled = _auto_recall(db, case, ctx)
            events.put({"event": "recall_done", "node": "",
                        "label": f"已自动召回 {recalled} 条参考材料注入评估节点",
                        "status": "ok"})
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
        "cause_type": case.cause_type,
        "goal_type": case.goal_type,
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
        # 档位与四象限中线由后端下发，避免前端再硬编码一份（两处硬编码必然漂移）
        "thresholds": {
            "go": SCORE_THRESHOLD_GO,
            "patch": SCORE_THRESHOLD_PATCH,
            "quadrant_mid": QUADRANT_AXIS_MID,
            "power_mean_p": POWER_MEAN_P,
        },
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
    # 必须在 _save_ctx 之前取：_save_ctx 落库后会清空 audit_trail
    stale = [{"node": n, "label": NODE_LABELS.get(n, n),
              "reason": d.get("stale_reason", "")}
             for n, d in ctx.dimension_results.items()
             if d.get("status") == "stale"]
    last_event = ctx.audit_trail[-1] if ctx.audit_trail else None
    effect = (last_event or {}).get("effect", "")

    _save_ctx(db, case, ctx)
    return {"ok": True,
            "dimension_results": ctx.dimension_results,
            "scores": ctx.scores,
            "recommendation": ctx.recommendation,
            "stale_nodes": stale,          # 前端据此展示「待确认重跑」
            "effect": effect}
