"""
案件管理路由：建案（案情+案由+业务目标+被告信息+观点注入）、列表、详情
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.case_context import CaseContext
from core.config import SUPPORTED_CAUSE_TYPES, GOAL_TYPES
from core.database import Case, Party, get_db

router = APIRouter()


class CaseCreate(BaseModel):
    name: str
    case_description: str
    cause_type: str = "商标侵权"
    goal_type: str = "要钱"
    client_org: str = ""
    defendant_name: str = ""
    defendant_type: str = "company"
    evidence_texts: str = ""              # P1：证据文本直接粘贴；文件上传解析在 P2 接入
    viewpoints: List[str] = []            # 观点注入（§5.4 手动勾选/文字输入）


class CaseOut(BaseModel):
    id: str
    name: str
    cause_type: str
    goal_type: Optional[str]
    status: str
    created_at: str

    class Config:
        from_attributes = True


@router.get("/meta")
def meta():
    return {"cause_types": SUPPORTED_CAUSE_TYPES, "goal_types": GOAL_TYPES}


@router.post("", response_model=CaseOut)
def create_case(payload: CaseCreate, db: Session = Depends(get_db)):
    if payload.cause_type not in SUPPORTED_CAUSE_TYPES:
        raise HTTPException(400, f"不支持的案由: {payload.cause_type}")
    if payload.goal_type not in GOAL_TYPES:
        raise HTTPException(400, f"不支持的业务目标: {payload.goal_type}")

    ctx = CaseContext(
        case_description=payload.case_description,
        cause_type=payload.cause_type,
        goal_type=payload.goal_type,
        defendant_info={
            "name": payload.defendant_name,
            "type": payload.defendant_type,
            "evidence_texts": payload.evidence_texts,
        },
    )
    for v in payload.viewpoints:
        if v.strip():
            ctx.add_viewpoint(v.strip(), source="建案观点注入")

    case = Case(
        name=payload.name,
        cause_type=payload.cause_type,
        goal_type=payload.goal_type,
        client_org=payload.client_org,
        case_description=payload.case_description,
        status="pending",
    )
    db.add(case)
    db.flush()

    if payload.defendant_name:
        db.add(Party(case_id=case.id, role="defendant",
                     name=payload.defendant_name, party_type=payload.defendant_type))

    ctx.case_id = case.id
    case.context_json = ctx.to_dict()
    db.commit()
    return CaseOut(id=case.id, name=case.name, cause_type=case.cause_type,
                   goal_type=case.goal_type, status=case.status,
                   created_at=case.created_at.isoformat())


@router.get("", response_model=List[CaseOut])
def list_cases(db: Session = Depends(get_db)):
    cases = db.query(Case).order_by(Case.created_at.desc()).all()
    return [CaseOut(id=c.id, name=c.name, cause_type=c.cause_type,
                    goal_type=c.goal_type, status=c.status,
                    created_at=c.created_at.isoformat()) for c in cases]


@router.get("/{case_id}")
def case_detail(case_id: str, db: Session = Depends(get_db)):
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(404, "案件不存在")
    return {
        "id": case.id,
        "name": case.name,
        "cause_type": case.cause_type,
        "goal_type": case.goal_type,
        "status": case.status,
        "case_description": case.case_description,
        "context": case.context_json,
    }
