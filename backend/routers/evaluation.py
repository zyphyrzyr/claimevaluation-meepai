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


class RunController:
    """单次评估运行的可控句柄（模块级 RUNS 按 case_id 持有）。

    - events：后端向 SSE 推送的事件队列（暂停/恢复/终止都经它通知前端）
    - pause / abort：前端端点置位的 threading.Event
    - status：running / paused / aborted / completed / partial / blocked
    - stream_open：当前是否有活跃的 SSE 消费者（用于 /resume 决定是否重开流）
    """

    def __init__(self):
        self.events: "queue.Queue" = queue.Queue()
        self.pause = threading.Event()
        self.abort = threading.Event()
        self.status = "running"
        self.stream_open = False


# case_id -> 运行中的评估句柄。同一时刻每案至多一个运行实例。
RUNS: dict[str, RunController] = {}


def derive_status_from_ctx(ctx: CaseContext) -> str:
    """按 ctx 里真实存在的产出推导评估终态。

    判定口径与 work() 收尾处一致（final / recommendation.level），只多一条：
    完全没有产出时退回 pending，而不是 partial——否则一个从没跑出东西的案件
    会被标成「部分完成」，比留在 evaluating 更误导。

    用途：终止、异常、进程重启都可能让流程没走到收尾，此时 case.status 会停在
    'evaluating' 变成僵尸。修它不能靠猜，只能看 ctx 里到底有没有东西。
    """
    has_output = any(
        (d or {}).get("status") in ("ok", "partial", "failed", "blocked")
        for d in (ctx.dimension_results or {}).values()
    )
    if not has_output:
        return "pending"
    if ctx.recommendation.get("level") == "block":
        return "blocked"
    return "completed" if ctx.scores.get("final") is not None else "partial"


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


def heal_stuck_evaluations(db: Session) -> list[dict]:
    """启动自愈：把进程外遗留的 'evaluating' 僵尸状态按实际产出修正。

    为什么可以在启动时断定它们全是僵尸：评估跑在工作线程里，进程一重启线程就没了，
    而 case.status 是持久化的镜像、不会自己回滚。所以「刚启动的进程里仍有
    case.status == 'evaluating'」本身是自相矛盾的——不是修不修的问题，它一定是错的。

    不这么做的话，案件会永远显示「评估中」徽标，且前端 run-state 会据此
    谎报 running、挂出暂停/终止控制条，一点就是 404。
    """
    fixed: list[dict] = []
    stuck = db.query(Case).filter(Case.status == "evaluating").all()
    for case in stuck:
        try:
            new_status = derive_status_from_ctx(_load_ctx(case))
        except Exception:
            continue          # 上下文都读不出来就别乱改，留给人工判断
        fixed.append({"case_id": case.id, "name": case.name,
                      "from": "evaluating", "to": new_status})
        case.status = new_status
    if fixed:
        db.commit()
    return fixed


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
    """启动完整评估，SSE 推送节点进度（支持暂停/恢复/终止）。

    2026-09-03 方案 B：材料注入后台化——启动时先执行自动召回
    （确定性查询词 + 双库语义检索），命中写入 ctx.injected_knowledge
    并留审计，替代原「评估准备页手动勾选」。用户无感，过程可查。
    """
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(404, "案件不存在")
    existing = RUNS.get(case_id)
    if existing and existing.status in ("running", "paused"):
        raise HTTPException(409, "评估正在进行中，请先暂停/终止或等待完成")

    ctx = _load_ctx(case)
    ctrl = RunController()
    RUNS[case_id] = ctrl

    def work():
        try:
            case.status = "evaluating"
            db.commit()
            recalled = _auto_recall(db, case, ctx)
            ctrl.events.put({"event": "recall_done", "node": "",
                             "label": f"已自动召回 {recalled} 条参考材料注入评估节点",
                             "status": "ok"})
            orch = Orchestrator(ctx, on_event=ctrl.events.put,
                               pause_event=ctrl.pause, abort_event=ctrl.abort)
            orch.run_all()
            # run_all 正常结束：非终止路径
            final = ctx.scores.get("final")
            case.status = "completed" if final is not None else "partial"
            if ctx.recommendation.get("level") == "block":
                case.status = "blocked"
            ctrl.status = case.status
            ctrl.events.put({"event": "flow_finished", "node": "",
                             "label": "", "status": case.status})
        except StopRun:
            # 终止信号：清空本次已产出结果（全部作废），状态置 aborted
            ctx.reset_evaluation()
            case.status = "aborted"
            ctrl.status = "aborted"
            ctrl.events.put({"event": "flow_aborted", "node": "",
                             "label": "评估已终止并作废", "status": "aborted"})
        except Exception as e:
            # 关键：不能把 case.status 留在 evaluating。这一轮已经死了，进程内
            # 再没有任何人会去改它，前端就会永远看到「评估进行中」＋点暂停报 404。
            # 按 ctx 里已有的产出落一个诚实的终态。
            ctrl.status = "failed"
            try:
                case.status = derive_status_from_ctx(ctx)
                db.commit()
            except Exception:
                pass
            ctrl.events.put({"event": "flow_error", "node": "", "label": "", "error": str(e)})
        finally:
            try:
                _save_ctx(db, case, ctx)
            except Exception:
                pass
            RUNS.pop(case_id, None)
            ctrl.events.put(None)

    threading.Thread(target=work, daemon=True).start()

    def stream():
        ctrl.stream_open = True
        try:
            while True:
                item = ctrl.events.get()
                if item is None:
                    break
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
        finally:
            ctrl.stream_open = False

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/{case_id}/pause")
def pause_evaluation(case_id: str):
    """暂停：置 pause 信号，编排器在下一个检查点挂起（线程保持、几乎不耗算力）。"""
    ctrl = RUNS.get(case_id)
    if not ctrl:
        raise HTTPException(404, "当前没有进行中的评估")
    ctrl.pause.set()
    if ctrl.status == "running":
        ctrl.status = "paused"
    return {"ok": True, "paused": ctrl.pause.is_set()}


@router.post("/{case_id}/resume")
def resume_evaluation(case_id: str):
    """恢复：清除 pause 信号并（按需）重开 SSE 流。

    - 若已有活跃流（同一标签页未刷新）：仅发信号，不返回新流，避免事件被两个消费者瓜分。
    - 若无活跃流（刷新页面后）：返回新的 SSE 流，承接后续事件。
    """
    ctrl = RUNS.get(case_id)
    if not ctrl:
        raise HTTPException(409, "没有可恢复的评估")
    ctrl.pause.clear()
    if ctrl.status == "paused":
        ctrl.status = "running"

    if ctrl.stream_open:
        return {"ok": True, "reconnected": False}

    def stream():
        ctrl.stream_open = True
        try:
            while True:
                item = ctrl.events.get()
                if item is None:
                    break
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
        finally:
            ctrl.stream_open = False

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/{case_id}/stop")
def stop_evaluation(case_id: str):
    """终止：置 abort 信号（若在暂停中同时解除阻塞），编排器在下一个检查点抛出 StopRun 收尾。"""
    ctrl = RUNS.get(case_id)
    if not ctrl:
        return {"ok": True, "stopped": False}  # 幂等：无可终止的运行
    ctrl.abort.set()
    ctrl.pause.clear()
    return {"ok": True, "stopped": True}


@router.get("/{case_id}/run-state")
def evaluation_run_state(case_id: str, db: Session = Depends(get_db)):
    """查询当前评估运行状态，供前端刷新页面后同步。

    注意 run 的取值口径：**RUNS 是唯一「可实际操作」的真相**——pause / resume /
    stop 都只看它。所以这里绝不能拿持久化的 case.status 冒充 running：那份镜像会
    因为进程重启或异常而停在 evaluating，一旦据此谎报 running，前端就会显示
    「评估进行中」并挂出控制条，用户一点暂停就撞 404（本 bug 的原始形态）。

    DB 是 evaluating 而 RUNS 里没有句柄 = 上一轮的进程已经没了 → 如实报 interrupted。
    """
    ctrl = RUNS.get(case_id)
    if ctrl:
        return {"run": ctrl.status}
    case = db.query(Case).filter(Case.id == case_id).first()
    s = case.status if case else None
    if s == "evaluating":
        return {"run": "interrupted"}
    if s in ("completed", "partial", "blocked"):
        return {"run": "done"}
    if s == "aborted":
        return {"run": "aborted"}
    return {"run": "none"}


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
            # 8 阶段明细与那句摘要：界面上「外部数据依据」要展示「查到什么、据此推断了什么」
            "stages": (ctx.defendant_profile or {}).get("stages", {}),
            "summary": (ctx.defendant_profile or {}).get("_summary", ""),
        },
        # 自动召回的材料：界面上要能回答「这个判断参考了哪些材料」
        "recalled_materials": ctx.injected_knowledge or [],
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


@router.get("/{case_id}/audit")
def case_audit(case_id: str, limit: int = 200, db: Session = Depends(get_db)):
    """
    案件审计轨迹。

    此前审计只写库、无出口：界面上写着「明细见审计轨迹」，用户却无处可查
    （前端 grep 不到任何 audit 接口）。这个方法把 audit_events 暴露出来，
    让「谁在什么时候做了什么、影响了什么」真的可见。
    """
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(404, "案件不存在")
    rows = (db.query(AuditEvent)
            .filter(AuditEvent.case_id == case_id)
            .order_by(AuditEvent.created_at.desc())
            .limit(max(1, min(limit, 500)))
            .all())
    return [{
        "id": r.id,
        "event_type": r.event_type,
        "node": r.node,
        "node_label": NODE_LABELS.get(r.node or "", r.node or ""),
        "content": r.content,
        "effect": r.effect,
        "created_at": r.created_at.isoformat(timespec="seconds") if r.created_at else "",
    } for r in rows]


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
