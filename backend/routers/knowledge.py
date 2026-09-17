"""
知识库路由（§7 RAG）
- 全局经验库 / 案件材料库 CRUD + 语义检索
- 案件材料自动入库（来源 A）、评分链路手动勾选注入、观点沉淀（来源 C）
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from typing import Optional

from core.auth import (
    case_owned, case_readable, current_user_optional, entry_owned, require_user,
)
from core.case_context import CaseContext
from core.database import Case, KnowledgeEntry, User, get_db
from core.knowledge import (
    add_knowledge, delete_knowledge, list_entries, search_knowledge,
    ingest_case_materials, info,
)
from routers.evaluation import _load_ctx, _save_ctx

router = APIRouter()


@router.get("/info")
def kb_info(db: Session = Depends(get_db)):
    # 带上 db：顺带返回孤儿向量块数量，让「DB 行与向量库不一致」这个
    # 本来完全不可见的故障状态变成可观测的（前端取不到时自己会忽略）。
    return info(db)


# ---------------------------------------------------------- 条目 CRUD

class EntryCreate(BaseModel):
    scope: str = "global"                 # global / case
    case_id: Optional[str] = None
    source_type: str = "B"                # A/B/C/D
    title: str
    content: str


@router.get("/entries")
def get_entries(scope: Optional[str] = None, case_id: Optional[str] = None,
                db: Session = Depends(get_db),
                user: Optional[User] = Depends(current_user_optional)):
    return list_entries(db, scope=scope, case_id=case_id,
                        user_id=user.id if user else None)


@router.post("/entries")
def create_entry(payload: EntryCreate, db: Session = Depends(get_db),
                 user: User = Depends(require_user)):
    if payload.scope == "case":
        if not payload.case_id:
            raise HTTPException(400, "案件材料（scope=case）必须提供 case_id")
        # 案件材料挂到别人的案子上等于替别人加料，按案件归属校验
        case_owned(case_id=payload.case_id, user=user, db=db)
    try:
        entry = add_knowledge(db, scope=payload.scope, source_type=payload.source_type,
                              title=payload.title, content=payload.content,
                              case_id=payload.case_id, user_id=user.id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "id": entry.id}


@router.delete("/entries/{entry_id}")
def remove_entry(entry: KnowledgeEntry = Depends(entry_owned),
                 db: Session = Depends(get_db)):
    if not delete_knowledge(db, entry.id):
        raise HTTPException(404, "条目不存在")
    return {"ok": True}


# ---------------------------------------------------------- 检索

class SearchRequest(BaseModel):
    query: str
    case_id: Optional[str] = None
    scope: Optional[str] = None           # case / global / None=双库
    top_k: int = 5


@router.post("/search")
def search(payload: SearchRequest, db: Session = Depends(get_db),
           user: Optional[User] = Depends(current_user_optional)):
    return search_knowledge(db, payload.query, case_id=payload.case_id,
                            scope=payload.scope, top_k=payload.top_k,
                            user_id=user.id if user else None)


# ---------------------------------------------------------- 案件级操作

@router.get("/cases/{case_id}/entries")
def case_entries(case_id: str, db: Session = Depends(get_db),
                 case: Case = Depends(case_readable)):
    return list_entries(db, scope="case", case_id=case_id)


class IngestRequest(BaseModel):
    evidence_texts: str


@router.post("/cases/{case_id}/ingest")
def ingest(case_id: str, payload: IngestRequest, db: Session = Depends(get_db),
           case: Case = Depends(case_owned)):
    """来源 A：证据文本自动入库（案件材料库，case_id 隔离）"""
    count = ingest_case_materials(db, case_id, payload.evidence_texts)
    return {"ok": True, "ingested": count}


# 手动勾选注入接口（POST /cases/{case_id}/inject）已于 2026-09-03 移除。
# 方案 B 把材料注入完全后台化：评估启动时由 evaluation._auto_recall 自动召回并
# 覆盖 ctx.injected_knowledge。保留手动接口会误导——用户勾选的条目会在下一次
# 评估启动时被自动召回整体覆盖，界面上看不出为什么没生效。注入明细改由审计轨迹
# 的 knowledge_auto_recall 事件承载，可查且可复现。


class DepositRequest(BaseModel):
    title: str
    content: str


@router.post("/cases/{case_id}/deposit")
def deposit(case_id: str, payload: DepositRequest, db: Session = Depends(get_db),
            case: Case = Depends(case_owned), user: User = Depends(require_user)):
    """来源 C：本案讨论/评估结论沉淀到全局经验库（跨案复用）"""
    content = payload.content.strip()
    if not content:
        raise HTTPException(400, "沉淀内容不能为空")
    # 沉淀出来的全局经验记在自己名下：它是跨案复用的私有经验，
    # 不该变成所有人都看得到、却谁都删不掉的公共数据。
    entry = add_knowledge(db, scope="global", source_type="C",
                          title=payload.title, content=content, user_id=user.id)
    return {"ok": True, "id": entry.id, "title": entry.title}
