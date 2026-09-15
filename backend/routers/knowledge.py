"""
知识库路由（§7 RAG）
- 全局经验库 / 案件材料库 CRUD + 语义检索
- 案件材料自动入库（来源 A）、评分链路手动勾选注入、观点沉淀（来源 C）
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.case_context import CaseContext
from core.database import Case, KnowledgeEntry, get_db
from core.knowledge import (
    add_knowledge, delete_knowledge, list_entries, search_knowledge,
    ingest_case_materials, info,
)
from routers.evaluation import _load_ctx, _save_ctx

router = APIRouter()


@router.get("/info")
def kb_info():
    return info()


# ---------------------------------------------------------- 条目 CRUD

class EntryCreate(BaseModel):
    scope: str = "global"                 # global / case
    case_id: Optional[str] = None
    source_type: str = "B"                # A/B/C/D
    title: str
    content: str


@router.get("/entries")
def get_entries(scope: Optional[str] = None, case_id: Optional[str] = None,
                db: Session = Depends(get_db)):
    return list_entries(db, scope=scope, case_id=case_id)


@router.post("/entries")
def create_entry(payload: EntryCreate, db: Session = Depends(get_db)):
    if payload.scope == "case":
        if not payload.case_id:
            raise HTTPException(400, "案件材料（scope=case）必须提供 case_id")
        case = db.query(Case).filter(Case.id == payload.case_id).first()
        if not case:
            raise HTTPException(404, "案件不存在")
    try:
        entry = add_knowledge(db, scope=payload.scope, source_type=payload.source_type,
                              title=payload.title, content=payload.content,
                              case_id=payload.case_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "id": entry.id}


@router.delete("/entries/{entry_id}")
def remove_entry(entry_id: str, db: Session = Depends(get_db)):
    if not delete_knowledge(db, entry_id):
        raise HTTPException(404, "条目不存在")
    return {"ok": True}


# ---------------------------------------------------------- 检索

class SearchRequest(BaseModel):
    query: str
    case_id: Optional[str] = None
    scope: Optional[str] = None           # case / global / None=双库
    top_k: int = 5


@router.post("/search")
def search(payload: SearchRequest, db: Session = Depends(get_db)):
    return search_knowledge(db, payload.query, case_id=payload.case_id,
                            scope=payload.scope, top_k=payload.top_k)


# ---------------------------------------------------------- 案件级操作

@router.get("/cases/{case_id}/entries")
def case_entries(case_id: str, db: Session = Depends(get_db)):
    return list_entries(db, scope="case", case_id=case_id)


class IngestRequest(BaseModel):
    evidence_texts: str


@router.post("/cases/{case_id}/ingest")
def ingest(case_id: str, payload: IngestRequest, db: Session = Depends(get_db)):
    """来源 A：证据文本自动入库（案件材料库，case_id 隔离）"""
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(404, "案件不存在")
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
def deposit(case_id: str, payload: DepositRequest, db: Session = Depends(get_db)):
    """来源 C：本案讨论/评估结论沉淀到全局经验库（跨案复用）"""
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(404, "案件不存在")
    content = payload.content.strip()
    if not content:
        raise HTTPException(400, "沉淀内容不能为空")
    entry = add_knowledge(db, scope="global", source_type="C",
                          title=payload.title, content=content)
    return {"ok": True, "id": entry.id, "title": entry.title}
