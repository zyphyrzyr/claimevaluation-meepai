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
from core.auth import case_owned, case_readable, current_user_optional
from core.case_context import CaseContext
from core.config import CAUSE_TRADEMARK, SUPPORTED_CAUSE_TYPES
from core.database import Case, MootRound, User, get_db
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
def run_embedded_moot(case_id: str, db: Session = Depends(get_db),
                      case: Case = Depends(case_owned)):
    """内嵌模拟法庭：SSE 直播逐轮发言；结束后系数回写 + 决策合成重算"""
    ctx = _load_ctx(case)

    if ctx.scores.get("final") is None:
        raise HTTPException(400, "案件尚未完成评估，请先完成主诉评估再启动模拟法庭")

    # RAG 自动召回（§7）：案件材料库 + 全局经验库，注入庭审材料
    recall = {"context": "", "refs": []}
    try:
        from core.knowledge import recall_for_context
        recall_query = (ctx.case_description or "")[:200] + " " + " ".join(
            g.get("item", "") for g in (ctx.gap_list or [])[:3])
        # 案件的归属人 = 经验库的可见范围（案件已过 case_owned 校验）
        recall = recall_for_context(db, case_id, recall_query, top_k=3,
                                    user_id=case.user_id)
    except Exception:
        pass

    events: queue.Queue = queue.Queue()
    result_holder: dict = {}

    def work():
        try:
            before = dict(ctx.scores)
            if recall["refs"]:
                events.put({"event": "recall", "refs": recall["refs"]})
            gen = moot_service.run_embedded(ctx, recall_context=recall["context"])
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
def get_moot(case_id: str, db: Session = Depends(get_db),
             case: Case = Depends(case_readable)):
    """查询已保存的庭审记录与修正系数"""
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
    cause_type: str = CAUSE_TRADEMARK
    viewpoints: List[str] = []
    plaintiff_points: str = ""    # 我方主张要点（可选，替代权利基础评估结论）
    case_id: Optional[str] = None  # 传了则庭审记录挂到本案并从本案材料库召回；不传为纯独立演练


@router.post("/standalone")
def run_standalone_moot(payload: StandaloneMootRequest, db: Session = Depends(get_db),
                        user: Optional[User] = Depends(current_user_optional)):
    """独立演练：不评估、不回写评分，输出演练报告。传 case_id 时记录挂到本案并从本案材料库召回。

    case_id 不在路径里（在请求体），所以拿不到路径依赖项，这里显式校验一次：
    带了 case_id 的独立演练会读该案材料库、并把庭审记录写回该案，实质是读写那个案件。
    不带 case_id 的纯演练不碰任何案件，允许匿名。
    """
    if not payload.case_description.strip():
        raise HTTPException(400, "案情描述不能为空")
    if payload.cause_type not in SUPPORTED_CAUSE_TYPES:
        raise HTTPException(400, f"不支持的案由: {payload.cause_type}")
    owner_id = user.id if user else None
    if payload.case_id:
        if user is None:
            raise HTTPException(401, "请先登录")
        owner_id = case_owned(case_id=payload.case_id, user=user, db=db).user_id

    events: queue.Queue = queue.Queue()
    cid = payload.case_id

    # RAG 自动召回：传了 case_id 则含本案材料库；否则仅全局经验库
    recall = {"context": "", "refs": []}
    try:
        from core.knowledge import recall_for_context
        recall = recall_for_context(db, cid, payload.case_description[:300], top_k=3,
                                    user_id=owner_id)
    except Exception:
        pass

    def work():
        try:
            if recall["refs"]:
                events.put({"event": "recall", "refs": recall["refs"]})
            gen = moot_service.run_standalone(
                payload.case_description,
                cause_type=payload.cause_type,
                viewpoints=payload.viewpoints,
                plaintiff_points=payload.plaintiff_points,
                recall_context=recall["context"],
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
            # 庭审记录持久化（case_id 为空 = 纯独立演练，不挂任何案件）
            if final:
                _save_rounds(db, cid, final.get("rounds", []), "standalone")
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
