"""
案件管理路由：建案（案情+案由+业务目标+被告信息+观点注入）、列表、详情
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.case_context import CaseContext
from core.config import SUPPORTED_CAUSE_TYPES, GOAL_TYPES
from core.database import Case, EvidenceFile, Party, get_db
from core.evidence_parser import is_image_file, is_pdf_file, ocr_image, parse_pdf

router = APIRouter()


class CaseCreate(BaseModel):
    name: str = ""
    case_description: str = ""
    cause_type: str = "商标侵权"
    goal_type: str = "要钱"
    client_org: str = ""
    defendant_name: str = ""
    defendant_type: str = "company"
    evidence_texts: str = ""              # 证据文本直接粘贴；文件上传解析后也会追加到此字段
    viewpoints: List[str] = []            # 观点注入（§5.4 手动勾选/文字输入）
    draft: bool = False                  # 草稿模式：仅校验案件名称，其余字段可留空


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


async def _parse_create_request(request: Request) -> tuple[CaseCreate, list[UploadFile]]:
    """同时支持 JSON 与 multipart/form-data（含证据文件上传）。"""
    ct = request.headers.get("content-type", "")
    if ct.startswith("multipart/form-data"):
        form = await request.form()
        payload_json = form.get("payload")
        if not payload_json:
            raise HTTPException(400, "multipart 请求必须提供 payload 字段（JSON 字符串）")
        payload = CaseCreate.model_validate_json(payload_json)
        files = form.getlist("evidence_files") or []
        return payload, files
    payload = CaseCreate.model_validate(await request.json())
    return payload, []


def _build_parties(payload) -> list:
    """红线引擎的主体资格检查依赖 parties（评测报告 P0-1）。"""
    parties = []
    if payload.client_org.strip():
        parties.append({"role": "plaintiff", "name": payload.client_org.strip(),
                        "party_type": "company"})
    if payload.defendant_name.strip():
        parties.append({"role": "defendant", "name": payload.defendant_name.strip(),
                        "party_type": payload.defendant_type})
    return parties


def _build_context(payload, parties) -> CaseContext:
    ctx = CaseContext(
        case_description=payload.case_description,
        cause_type=payload.cause_type,
        goal_type=payload.goal_type,
        defendant_info={
            "name": payload.defendant_name,
            "type": payload.defendant_type,
            "evidence_texts": payload.evidence_texts,
        },
        parties=parties,
    )
    for v in payload.viewpoints:
        if v.strip():
            ctx.add_viewpoint(v.strip(), source="建案观点注入")
    return ctx


async def _parse_evidence(files) -> list:
    """解析上传的证据文件（PDF/图片），返回记录列表（含解析文本/状态）。不落库，由调用方在拿到 case_id 后写 EvidenceFile。"""
    records = []
    for file in files:
        if not file.filename:
            continue
        content = await file.read()
        if is_pdf_file(file.filename):
            result = parse_pdf(content, file.filename)
        elif is_image_file(file.filename):
            result = ocr_image(content, file.filename)
        else:
            result = {"success": False, "text": "",
                      "error": "不支持的文件格式，仅支持 PDF/PNG/JPG/JPEG"}
        records.append({
            "filename": file.filename,
            "content_type": file.content_type or "",
            "text": result.get("text", "") if result.get("success") else "",
            "status": "ok" if result.get("success") else "failed",
        })
    return records


def _evidence_snippets(records: list) -> str:
    return "\n\n".join(
        f"【证据文件: {r['filename']}】\n{r['text']}"
        for r in records if r["text"]
    )


def _write_evidence_files(records: list, case_id: str, db: Session) -> None:
    for r in records:
        db.add(EvidenceFile(
            case_id=case_id,
            file_name=r["filename"],
            file_type=r["content_type"],
            parse_status=r["status"],
            parsed_text=r["text"],
            storage_uri="",
        ))


@router.post("", response_model=CaseOut)
async def create_case(request: Request, db: Session = Depends(get_db)):
    payload, files = await _parse_create_request(request)

    if not payload.name.strip():
        raise HTTPException(400, "请填写案件名称")

    if payload.cause_type not in SUPPORTED_CAUSE_TYPES:
        raise HTTPException(400, f"不支持的案由: {payload.cause_type}")
    if payload.goal_type not in GOAL_TYPES:
        raise HTTPException(400, f"不支持的业务目标: {payload.goal_type}")

    # ---- 草稿模式：名称已在校验通过，其余字段留空也可存 ----
    if payload.draft:
        parties = _build_parties(payload)
        ctx = _build_context(payload, parties)
        case = Case(
            name=payload.name,
            cause_type=payload.cause_type,
            goal_type=payload.goal_type,
            client_org=payload.client_org,
            case_description=payload.case_description,
            status="draft",
        )
        case.context_json = ctx.to_dict()
        db.add(case)
        db.commit()
        return CaseOut(id=case.id, name=case.name, cause_type=case.cause_type,
                       goal_type=case.goal_type, status=case.status,
                       created_at=case.created_at.isoformat())

    # ---- 正式建案：全字段必填校验 ----
    if not payload.client_org.strip():
        # 主诉评估的发起方就是原告，缺了它红线引擎的主体资格检查只能报
        # 「有当事人但没有原告」并硬门禁拦截整个评估——与其让用户在流程末端
        # 撞上红线，不如在录入时就拦下来。
        raise HTTPException(400, "请填写我司主体（原告）：主诉评估必须由权利人发起")

    parties = _build_parties(payload)

    # 解析上传的证据文件（PDF/图片），解析失败不阻断建案
    file_records = await _parse_evidence(files) if files else []
    parsed_text = _evidence_snippets(file_records)
    if parsed_text:
        base = payload.evidence_texts.strip()
        payload.evidence_texts = (base + "\n\n" if base else "") + parsed_text

    ctx = _build_context(payload, parties)
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

    # 原告此前只落在 Case.client_org，未进 Party 表，导致案件详情读不到当事人
    for p in parties:
        db.add(Party(case_id=case.id, role=p["role"],
                     name=p["name"], party_type=p["party_type"]))

    # 写入 EvidenceFile 记录（含解析状态，便于详情页展示）
    _write_evidence_files(file_records, case.id, db)

    ctx.case_id = case.id
    case.context_json = ctx.to_dict()
    db.commit()

    # 来源 A：证据文本自动入库到本案材料库（case_id 隔离，供 RAG 自动召回）
    if payload.evidence_texts.strip():
        from core.knowledge import ingest_case_materials
        try:
            ingest_case_materials(db, case.id, payload.evidence_texts)
        except Exception:
            pass  # 入库失败不阻断建案

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
        "client_org": case.client_org,
        "context": case.context_json,
    }


@router.put("/{case_id}", response_model=CaseOut)
async def update_draft(case_id: str, request: Request, db: Session = Depends(get_db)):
    """仅草稿可编辑：覆盖文本字段、重新解析上传文件、重建当事人与 context。状态保持 draft。"""
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(404, "案件不存在")
    if case.status != "draft":
        raise HTTPException(400, "仅草稿状态可编辑，已启动评估的案件不可再改")

    payload, files = await _parse_create_request(request)
    if payload.cause_type not in SUPPORTED_CAUSE_TYPES:
        raise HTTPException(400, f"不支持的案由: {payload.cause_type}")
    if payload.goal_type not in GOAL_TYPES:
        raise HTTPException(400, f"不支持的业务目标: {payload.goal_type}")
    if not payload.name.strip():
        raise HTTPException(400, "请填写案件名称")

    case.name = payload.name
    case.cause_type = payload.cause_type
    case.goal_type = payload.goal_type
    case.client_org = payload.client_org
    case.case_description = payload.case_description

    parties = _build_parties(payload)
    ctx = _build_context(payload, parties)
    ctx.case_id = case.id

    # 重新解析上传文件并追加到证据文本（旧文件记录保留，新上传的追加上去）
    file_records = await _parse_evidence(files) if files else []
    parsed_text = _evidence_snippets(file_records)
    if parsed_text:
        base = ctx.defendant_info["evidence_texts"].strip()
        ctx.defendant_info["evidence_texts"] = (base + "\n\n" if base else "") + parsed_text
    _write_evidence_files(file_records, case.id, db)

    # 同步 Party 表（删除旧的，写最新）
    for p in db.query(Party).filter(Party.case_id == case_id).all():
        db.delete(p)
    for p in parties:
        db.add(Party(case_id=case.id, role=p["role"],
                     name=p["name"], party_type=p["party_type"]))

    case.context_json = ctx.to_dict()
    db.commit()

    # 证据文本变化后重新入库本案材料库
    if ctx.defendant_info["evidence_texts"].strip():
        from core.knowledge import ingest_case_materials
        try:
            ingest_case_materials(db, case.id, ctx.defendant_info["evidence_texts"])
        except Exception:
            pass

    return CaseOut(id=case.id, name=case.name, cause_type=case.cause_type,
                   goal_type=case.goal_type, status=case.status,
                   created_at=case.created_at.isoformat())


@router.post("/{case_id}/start-evaluation", response_model=CaseOut)
def start_evaluation(case_id: str, db: Session = Depends(get_db)):
    """草稿启动评估：校验带 * 的必填项，补全当事人与 context，置 pending。"""
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(404, "案件不存在")
    if case.status != "draft":
        raise HTTPException(400, "仅草稿状态可启动评估")

    ctx = CaseContext.from_dict(case.context_json or {})
    ctx.case_id = case.id

    di = ctx.defendant_info or {}
    missing = []
    if not (case.client_org or "").strip():
        missing.append("我司主体（原告）")
    if not (di.get("name", "") or "").strip():
        missing.append("被告名称")
    if not (case.case_description or "").strip():
        missing.append("案情描述")
    evidence_text = (di.get("evidence_texts", "") or "").strip()
    has_files = db.query(EvidenceFile).filter(EvidenceFile.case_id == case_id).count() > 0
    if not evidence_text and not has_files:
        missing.append("证据材料文本或上传文件")
    if missing:
        raise HTTPException(400, "以下必填项未完成，无法启动评估：" + "、".join(missing))

    # 确保当事人已写入 context（草稿当时可能未填）
    parties = []
    if (case.client_org or "").strip():
        parties.append({"role": "plaintiff", "name": case.client_org.strip(),
                        "party_type": "company"})
    if (di.get("name", "") or "").strip():
        parties.append({"role": "defendant", "name": di["name"].strip(),
                        "party_type": di.get("type", "company")})
    ctx.parties = parties
    # 同步 Party 表，保证详情与红线引擎读取一致
    for p in db.query(Party).filter(Party.case_id == case_id).all():
        db.delete(p)
    for p in parties:
        db.add(Party(case_id=case.id, role=p["role"],
                     name=p["name"], party_type=p["party_type"]))

    case.context_json = ctx.to_dict()
    case.status = "pending"
    db.commit()

    return CaseOut(id=case.id, name=case.name, cause_type=case.cause_type,
                   goal_type=case.goal_type, status=case.status,
                   created_at=case.created_at.isoformat())
