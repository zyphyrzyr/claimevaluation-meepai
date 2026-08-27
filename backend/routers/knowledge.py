"""
知识库路由（§7 RAG）
- 全局经验库 / 案件材料库 CRUD + 语义检索
- 案件材料自动入库（来源 A）、评分链路手动勾选注入、观点沉淀（来源 C）
"""

from typing import List, Optional

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


class InjectRequest(BaseModel):
    entry_ids: List[str]


@router.post("/cases/{case_id}/inject")
def inject(case_id: str, payload: InjectRequest, db: Session = Depends(get_db)):
    """评分链路手动勾选注入（§7 保复现性：注入哪些条目全程留审计）"""
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(404, "案件不存在")
    ctx = _load_ctx(case)
    rows = db.query(KnowledgeEntry).filter(KnowledgeEntry.id.in_(payload.entry_ids)).all()
    # 防污染：勾选了其他案件的材料时拒绝
    for r in rows:
        if r.scope == "case" and r.case_id != case_id:
            raise HTTPException(400, f"条目 {r.id}（{r.title}）属于其他案件，禁止注入")
    ctx.injected_knowledge = [
        {"id": r.id, "title": r.title, "scope": r.scope,
         "source_type": r.source_type, "snippet": (r.content or "")[:200]}
        for r in rows
    ]
    ctx.log_event("knowledge_inject", content="；".join(r.title for r in rows),
                  effect=f"已勾选注入 {len(rows)} 条知识库条目到全部 LLM 评估节点")
    _save_ctx(db, case, ctx)
    return {"ok": True, "injected": len(rows),
            "entries": ctx.injected_knowledge}


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
