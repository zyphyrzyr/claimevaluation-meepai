"""
模拟法庭路由（P2 双模式）
- POST /api/moot/{case_id}/run   内嵌模式：评估完成后压力测试，系数回写 + 决策合成重算
- POST /api/moot/{case_id}/stop  中止进行中的庭审（内嵌 / 挂案独立演练）
- POST /api/moot/standalone      独立模式：手动组料纯演练，不回写评分
- GET  /api/moot/{case_id}       查询已保存的庭审记录
SSE 事件流：round（逐轮发言）→ moot_finished（法官归纳 + 系数）
          中止时以 moot_stopped 结束，不回写系数、不落库
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


# ============================================================
# 庭审中止标志
# ============================================================
# 按「案件」登记停止请求：内嵌模式一定有 case_id；独立演练传了 case_id 时也挂上去。
# 纯独立演练（不带 case_id）没有可登记的键，因此不支持中止——那种模式下
# 前端不显示中止按钮。
_MOOT_STOP: dict = {}
_MOOT_STOP_LOCK = threading.Lock()


def _register_stop(key: str) -> threading.Event:
    """开跑前登记一个「本次运行的停止按钮」。同案重复开跑会顶掉上一个标志。"""
    with _MOOT_STOP_LOCK:
        ev = threading.Event()
        _MOOT_STOP[key] = ev
        return ev


def _release_stop(key: Optional[str]) -> None:
    if not key:
        return
    with _MOOT_STOP_LOCK:
        _MOOT_STOP.pop(key, None)


def _pump(gen, stop_ev: Optional[threading.Event], on_event, counter: dict) -> bool:
    """逐轮取事件推给 on_event；stop_ev 置位时以 moot_stopped 收尾。

    返回 True 表示「被中止」。抽成独立函数是因为这段逻辑最难测：真实模式下
    next(gen) 是一次十秒级的 LLM 调用，而 httpx 的 ASGI 传输**不会增量返回**
    SSE（整个响应体读完后才交给调用方），所以 TestClient 里根本抓不到
    「跑到一半」这一刻——只能把循环单独拎出来，喂一个假生成器做确定性断言。
    """
    while True:
        if stop_ev is not None and stop_ev.is_set():
            on_event({"event": "moot_stopped", "rounds": counter["rounds"]})
            return True
        try:
            event = next(gen)
        except StopIteration:
            return False
        if event.get("event") == "round":
            counter["rounds"] += 1
        on_event(event)


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

    # 聚合三方共享的「可引用依据」：法定基准（始终有）+ 复用评估流程北大法宝结果
    # （load_results，不重复调用）+ 经验库 RAG 召回。
    legal_ctx = {"block": "", "refs": []}
    try:
        from core.moot_court.context_sources import gather_legal_context
        recall_query = (ctx.case_description or "")[:300] + " " + " ".join(
            g.get("item", "") for g in (ctx.gap_list or [])[:3])
        legal_ctx = gather_legal_context(
            case_id, ctx.cause_type, recall_query,
            db=db, user_id=case.user_id, top_k=3,
        )
    except Exception:
        pass

    events: queue.Queue = queue.Queue()
    result_holder: dict = {}
    stop_key = case.id
    stop_ev = _register_stop(stop_key)
    # 用容器而不是闭包变量计数：work() 里要改这个值
    counter = {"rounds": 0}

    def work():
        try:
            before = dict(ctx.scores)
            if legal_ctx["refs"]:
                events.put({"event": "recall", "refs": legal_ctx["refs"]})
            gen = moot_service.run_embedded(ctx, shared_legal_context=legal_ctx["block"])
            # 中止检查放在「取下一轮」之前：真实模式下 next(gen) 就是一次
            # LLM 调用（十秒级），调用中途打断不了。能承诺的只有
            # 「当前这轮说完就停」——前端文案必须照这个口径，别许诺立即停止。
            if _pump(gen, stop_ev, events.put, counter):
                return

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
            _release_stop(stop_key)
            events.put(None)

    threading.Thread(target=work, daemon=True).start()

    def stream():
        while True:
            item = events.get()
            if item is None:
                break
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/{case_id}/stop")
def stop_moot(case_id: str, case: Case = Depends(case_owned)):
    """中止进行中的庭审。

    这里只登记一个「停止请求」，真正生效点在下一次取轮次之前（见 run_embedded）：
    真实模式下一轮就是一次 LLM 调用，中途打断不了，生效时机会落在当前这轮说完之后。

    对没在跑的庭审调用是**无害的空操作**（返回 stopped=false），因为前端很可能因为
    网络延迟、点了刚刚已经跑完的庭审。
    """
    with _MOOT_STOP_LOCK:
        ev = _MOOT_STOP.get(case_id)
    if ev is None:
        return {"stopped": False, "detail": "当前没有进行中的庭审"}
    ev.set()
    return {"stopped": True}


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

    # 聚合三方共享依据：法定基准 + 经验库召回；纯独立演练（cid=None）不触发 on-demand 北大法宝
    legal_ctx = {"block": "", "refs": []}
    try:
        from core.moot_court.context_sources import gather_legal_context
        legal_ctx = gather_legal_context(
            cid, payload.cause_type, payload.case_description[:300],
            db=db, user_id=owner_id, top_k=3,
        )
    except Exception:
        pass

    # 纯独立演练（无 case_id）没有可登记的键，不支持中止；挂在案件下的演练按案件登记
    stop_ev = _register_stop(cid) if cid else None
    counter = {"rounds": 0}
    final_holder: dict = {}

    def work():
        try:
            if legal_ctx["refs"]:
                events.put({"event": "recall", "refs": legal_ctx["refs"]})
            gen = moot_service.run_standalone(
                payload.case_description,
                cause_type=payload.cause_type,
                viewpoints=payload.viewpoints,
                plaintiff_points=payload.plaintiff_points,
                shared_legal_context=legal_ctx["block"],
            )

            def emit(event):
                # moot_finished 里带着完整 transcript，落库要用，所以边推边留一份
                if event.get("event") == "moot_finished":
                    final_holder["event"] = event
                events.put(event)

            if _pump(gen, stop_ev, emit, counter):
                return  # 被中止：不落库、不回写

            # 庭审记录持久化（case_id 为空 = 纯独立演练，不挂任何案件）
            final = final_holder.get("event")
            if final:
                _save_rounds(db, cid, final.get("rounds", []), "standalone")
        except Exception as e:
            events.put({"event": "moot_error", "error": str(e)})
        finally:
            _release_stop(cid)
            events.put(None)

    threading.Thread(target=work, daemon=True).start()

    def stream():
        while True:
            item = events.get()
            if item is None:
                break
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")
