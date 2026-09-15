"""
案件管理路由：建案（案情+案由+业务目标+被告信息+观点注入）、列表、详情
"""

from typing import List, Optional
import re

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.case_context import CaseContext
from core.config import SUPPORTED_CAUSE_TYPES, GOAL_TYPES
from core.database import Case, EvidenceFile, Party, KnowledgeEntry, RetrievalRecord, get_db
from core.evidence_parser import (
    is_image_file, is_pdf_file, is_zip_file, ocr_image, parse_pdf, parse_zip_archive,
)
from core.text_extractor import extract_text_from_file, is_supported_description_file
from core import file_store

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
    parse_summary: Optional[dict] = None  # 上传文件/ZIP 解析摘要（仅本次请求含文件时返回）

    class Config:
        from_attributes = True


@router.get("/meta")
def meta():
    return {"cause_types": SUPPORTED_CAUSE_TYPES, "goal_types": GOAL_TYPES}


@router.post("/upload-description-text")
async def upload_description_text(file: UploadFile):
    """上传文件并提取文本，用于案情描述导入。

    支持 .txt / .md / .docx / .pdf；解析失败返回 400，成功返回 {filename, text, page_count}。
    文本编码优先 UTF-8，兼容 GBK/GB2312。
    """
    if not file.filename:
        raise HTTPException(400, "请选择要上传的文件")

    if not is_supported_description_file(file.filename):
        raise HTTPException(
            400,
            "不支持的文件格式，案情描述导入仅支持 .txt / .md / .docx / .pdf"
        )

    # 大小限制 5MB
    MAX_SIZE = 5 * 1024 * 1024
    content = await file.read()
    if len(content) > MAX_SIZE:
        raise HTTPException(400, "文件大小超过 5MB 限制")

    result = extract_text_from_file(content, file.filename)
    if not result.get("success"):
        raise HTTPException(400, result.get("error", "文件解析失败"))

    return {
        "filename": result["filename"],
        "text": result["text"],
        "page_count": result.get("page_count", 0),
    }


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


async def _parse_evidence(files) -> dict:
    """解析上传的证据文件（PDF/图片/ZIP）。

    返回 {"records": [...], "zip_summaries": [...]}。records 由调用方写入 EvidenceFile 并追加到 evidence_texts；
    zip_summaries 仅在包含 .zip 时出现，用于前端展示解压摘要。

    2026-09-03 新增：
    - 支持 .zip 压缩包，自动解压并批量解析内部 PDF/图片/文本
    - 压缩包内文本类文件（txt/md/docx）只提取文本，不生成 EvidenceFile 记录
    """
    records = []
    zip_summaries = []
    for file in files:
        if not file.filename:
            continue
        content = await file.read()

        # 单个 ZIP 压缩包：展开批量解析
        if is_zip_file(file.filename):
            zip_result = parse_zip_archive(content, file.filename)
            records.extend(zip_result.get("records", []))
            if zip_result.get("success"):
                zip_summaries.append({
                    "filename": file.filename,
                    "total": zip_result.get("total", 0),
                    "ok_count": zip_result.get("ok_count", 0),
                    "failed": zip_result.get("failed", 0),
                    "skipped": zip_result.get("skipped", 0),
                    "warnings": zip_result.get("warnings", []),
                })
            else:
                zip_summaries.append({
                    "filename": file.filename,
                    "total": 0,
                    "ok_count": 0,
                    "failed": 0,
                    "skipped": 0,
                    "warnings": zip_result.get("warnings", []),
                })
            continue

        if is_pdf_file(file.filename):
            result = parse_pdf(content, file.filename)
        elif is_image_file(file.filename):
            result = ocr_image(content, file.filename)
        elif file.filename.lower().endswith(".docx"):
            result = extract_text_from_file(content, file.filename)
        elif file.filename.lower().endswith(".doc"):
            text = file_store.doc_bytes_to_text(content, file.filename)
            result = {"success": bool(text), "text": text or "",
                      "error": "" if text else "无法解析 .doc（本环境缺少 textutil 或转换失败）"}
        else:
            result = {"success": False, "text": "",
                      "error": "不支持的文件格式，仅支持 PDF/PNG/JPG/JPEG/DOC/DOCX/ZIP"}
        records.append({
            "filename": file.filename,
            "content_type": file.content_type or "",
            "text": result.get("text", "") if result.get("success") else "",
            "status": "ok" if result.get("success") else "failed",
            "raw": content,
        })
    return {"records": records, "zip_summaries": zip_summaries}


def _evidence_snippets(records: list) -> str:
    return "\n\n".join(
        f"【证据文件: {r['filename']}】\n{r['text']}"
        for r in records if r["text"]
    )


def _build_parse_summary(file_records: list, zip_summaries: list) -> Optional[dict]:
    """根据解析记录构造前端展示摘要；仅本请求含文件时返回。"""
    if not file_records and not zip_summaries:
        return None
    ok = sum(1 for r in file_records if r["status"] == "ok")
    failed = sum(1 for r in file_records if r["status"] == "failed")
    skipped = sum(s.get("skipped", 0) for s in zip_summaries)
    warnings = [w for s in zip_summaries for w in s.get("warnings", [])]
    return {
        "total": len(file_records) + skipped,
        "ok": ok,
        "failed": failed,
        "skipped": skipped,
        "warnings": warnings[:5],
    }


def _rebuild_case_knowledge(db: Session, case_id: str, text: str) -> None:
    """重建本案材料库：先清旧条目再入库（add_knowledge 不去重）。

    修复：update_draft 原先直接 ingest 不清旧，导致每次编辑保存都追加一份
    「证据材料-N」重复条目。统一走「先清后增」，与删除文件链路语义一致。
    """
    from core.knowledge.service import delete_knowledge, ingest_case_materials
    try:
        for e in db.query(KnowledgeEntry).filter(
            KnowledgeEntry.scope == "case", KnowledgeEntry.case_id == case_id,
        ).all():
            delete_knowledge(db, e.id)
        if (text or "").strip():
            ingest_case_materials(db, case_id, text)
    except Exception:
        pass  # 入库失败不阻断主流程


def _write_evidence_files(records: list, case_id: str, db: Session) -> None:
    store_exts = ('.pdf', '.png', '.jpg', '.jpeg', '.doc', '.docx')
    for r in records:
        # 压缩包内纯文本类（txt/md）只提取文本追加到 evidence_texts，不生成独立原件记录；
        # 有扩展名命中的（pdf/png/jpg/jpeg/doc/docx）才建 EvidenceFile 并落盘原件。
        if not r.get("content_type") and not r["filename"].lower().endswith(store_exts):
            continue
        ef = EvidenceFile(
            case_id=case_id,
            file_name=r["filename"],
            file_type=r.get("content_type") or "",
            parse_status=r["status"],
            parsed_text=r["text"],
            storage_uri="",
        )
        db.add(ef)
        db.flush()  # 先拿 id，再用 id 命名落盘，规避文件名路径穿越
        raw = r.get("raw")
        if raw is not None and r["filename"].lower().endswith(store_exts):
            ef.storage_uri = file_store.save_original(case_id, ef.id, r["filename"], raw)


@router.post("", response_model=CaseOut)
async def create_case(request: Request, db: Session = Depends(get_db)):
    payload, files = await _parse_create_request(request)

    if not payload.name.strip():
        raise HTTPException(400, "请填写案件名称")

    if payload.cause_type not in SUPPORTED_CAUSE_TYPES:
        raise HTTPException(400, f"不支持的案由: {payload.cause_type}")
    if payload.goal_type not in GOAL_TYPES:
        raise HTTPException(400, f"不支持的业务目标: {payload.goal_type}")

    # ---- 草稿模式：名称已在校验通过，其余字段留空也可存；上传文件一并解析落库 ----
    parse_result = {"records": [], "zip_summaries": []}
    if files:
        parse_result = await _parse_evidence(files)
    file_records = parse_result["records"]
    zip_summaries = parse_result["zip_summaries"]
    parsed_text = _evidence_snippets(file_records)

    if payload.draft:
        parties = _build_parties(payload)
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
            status="draft",
        )
        db.add(case)
        db.flush()
        _write_evidence_files(file_records, case.id, db)
        ctx.case_id = case.id
        case.context_json = ctx.to_dict()
        db.commit()
        if payload.evidence_texts.strip():
            from core.knowledge import ingest_case_materials
            try:
                ingest_case_materials(db, case.id, payload.evidence_texts)
            except Exception:
                pass
        return CaseOut(id=case.id, name=case.name, cause_type=case.cause_type,
                       goal_type=case.goal_type, status=case.status,
                       created_at=case.created_at.isoformat(),
                       parse_summary=_build_parse_summary(file_records, zip_summaries))

    # ---- 正式建案：全字段必填校验 ----
    if not payload.client_org.strip():
        # 主诉评估的发起方就是原告，缺了它红线引擎的主体资格检查只能报
        # 「有当事人但没有原告」并硬门禁拦截整个评估——与其让用户在流程末端
        # 撞上红线，不如在录入时就拦下来。
        raise HTTPException(400, "请填写我司主体（原告）：主诉评估必须由权利人发起")

    parties = _build_parties(payload)

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
                   created_at=case.created_at.isoformat(),
                   parse_summary=_build_parse_summary(file_records, zip_summaries))


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
    evidence_files = [
        {"id": ef.id, "file_name": ef.file_name, "parse_status": ef.parse_status}
        for ef in db.query(EvidenceFile).filter(EvidenceFile.case_id == case_id).all()
    ]
    return {
        "id": case.id,
        "name": case.name,
        "cause_type": case.cause_type,
        "goal_type": case.goal_type,
        "status": case.status,
        "case_description": case.case_description,
        "client_org": case.client_org,
        "evidence_files": evidence_files,
        "context": case.context_json,
    }


@router.put("/{case_id}", response_model=CaseOut)
async def update_draft(case_id: str, request: Request, db: Session = Depends(get_db)):
    """编辑案件：覆盖文本字段、重新解析上传文件、重建当事人与 context。草稿/已评估案件均可改，状态保持原值；评估运行中禁止。"""
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(404, "案件不存在")
    if case.status == "evaluating":
        raise HTTPException(400, "评估运行中，请等待本次评估完成后再编辑")

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
    parse_result = await _parse_evidence(files) if files else {"records": [], "zip_summaries": []}
    file_records = parse_result["records"]
    zip_summaries = parse_result["zip_summaries"]
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

    # 证据文本变化后重建本案材料库（先清后增，防重复条目）
    _rebuild_case_knowledge(db, case.id, ctx.defendant_info["evidence_texts"])

    return CaseOut(id=case.id, name=case.name, cause_type=case.cause_type,
                   goal_type=case.goal_type, status=case.status,
                   created_at=case.created_at.isoformat(),
                   parse_summary=_build_parse_summary(file_records, zip_summaries))


@router.post("/{case_id}/start-evaluation", response_model=CaseOut)
def start_evaluation(case_id: str, db: Session = Depends(get_db)):
    """启动评估：校验带 * 的必填项，补全当事人与 context，置 pending。草稿/已评估案件均可重新评估；评估进行中禁止。"""
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(404, "案件不存在")
    if case.status == "evaluating":
        raise HTTPException(400, "评估运行中，请等待本次评估完成后再启动评估")

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


@router.delete("/{case_id}/evidence-files/{file_id}")
def delete_evidence_file(case_id: str, file_id: str, db: Session = Depends(get_db)):
    """删除已上传的证据文件：移除 EvidenceFile 记录、从 evidence_texts 剥离其解析片段、重建本案材料库。草稿/已评估案件均可；评估进行中禁止。"""
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(404, "案件不存在")
    if case.status == "evaluating":
        raise HTTPException(400, "评估运行中，请等待本次评估完成后再删除文件")
    ef = db.query(EvidenceFile).filter(
        EvidenceFile.id == file_id, EvidenceFile.case_id == case_id,
    ).first()
    if not ef:
        raise HTTPException(404, "文件记录不存在")

    # 从 evidence_texts 剥离该文件的解析片段（按「【证据文件: 文件名】」整段移除，含其内部换行与分页行）
    ctx = CaseContext.from_dict(case.context_json or {})
    # 注意：必须深拷贝一份，否则 di 会别名到 session 里 case.context_json 的同一对象，
    # 就地修改会污染 JSON 列的变更快照、导致 SQLAlchemy 在 commit 时不发 UPDATE（现象：内存已改、落库没改）。
    di = dict(ctx.defendant_info or {})
    text = di.get("evidence_texts", "") or ""
    marker = f"【证据文件: {ef.file_name}】"
    # 以 marker 起、到下一个 marker（或文末）之间的整段都删掉；包括 PDF 解析产生的「--- 第 N 页 ---」分页行
    pattern = re.escape(marker) + r".*?(?=\n*【证据文件: |\Z)"
    remaining = re.sub(pattern, "", text, flags=re.DOTALL)
    remaining = re.sub(r"(\n\s*)+", "\n", remaining).strip()
    di["evidence_texts"] = remaining
    ctx.defendant_info = di
    # 强制生成全新的 dict 并显式标记 JSON 列已脏，确保 commit 一定落库
    case.context_json = dict(ctx.to_dict())
    from sqlalchemy.orm import attributes
    attributes.flag_modified(case, "context_json")

    db.delete(ef)
    db.commit()

    # 重建本案材料库（先清后增，保证删后一致）
    _rebuild_case_knowledge(db, case_id, remaining)

    return {"ok": True}


def _get_ef(case_id: str, file_id: str, db: Session) -> EvidenceFile:
    """校验 file_id 确实属于该 case_id（防越权读他人文件）。"""
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(404, "案件不存在")
    ef = db.query(EvidenceFile).filter(
        EvidenceFile.id == file_id, EvidenceFile.case_id == case_id,
    ).first()
    if not ef:
        raise HTTPException(404, "文件记录不存在")
    return ef


@router.get("/{case_id}/evidence-files/{file_id}")
def evidence_file_detail(case_id: str, file_id: str, db: Session = Depends(get_db)):
    """预览用元信息：解析文本、是否有原件、大小、预览类型。"""
    ef = _get_ef(case_id, file_id, db)
    return {
        "id": ef.id,
        "file_name": ef.file_name,
        "parse_status": ef.parse_status,
        "parsed_text": ef.parsed_text or "",
        "has_blob": file_store.has_blob(ef.storage_uri),
        "size": file_store.size(ef.storage_uri),
        "preview_kind": file_store.preview_kind(ef.storage_uri, ef.file_name),
    }


@router.get("/{case_id}/evidence-files/{file_id}/raw")
def evidence_file_raw(case_id: str, file_id: str, db: Session = Depends(get_db)):
    """原始字节流（inline），供 PDF 原生渲染 / 图片显示 / 下载原件。无原件返回 404。"""
    ef = _get_ef(case_id, file_id, db)
    p = file_store.original_path(ef.storage_uri)
    if p is None:
        raise HTTPException(404, "原始文件不可用：该文件可能上传于旧版本，仅保留解析文本")
    return FileResponse(
        p,
        media_type=file_store.raw_mime(ef.storage_uri),
        filename=ef.file_name,
        content_disposition_type="inline",
    )


@router.get("/{case_id}/evidence-files/{file_id}/preview")
def evidence_file_preview(case_id: str, file_id: str, db: Session = Depends(get_db)):
    """doc/docx 经 textutil 转出的 HTML（带缓存），供沙箱 iframe 渲染；不可用返回 406。"""
    ef = _get_ef(case_id, file_id, db)
    html = file_store.doc_to_html(ef.storage_uri)
    if html is None:
        raise HTTPException(406, "该文件类型不支持在线预览（仅 doc/docx 可预览，且需本机 textutil）")
    return HTMLResponse(html)


@router.delete("/{case_id}")
def delete_case(case_id: str, db: Session = Depends(get_db)):
    """删除案件：清案件材料库（向量 + DB）、检索记录，再删案件本体；
    SQLAlchemy cascade 自动带走 Party/EvidenceFile/RuleHit/ScoreSnapshot/MootRound/Report/AuditEvent。"""
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(404, "案件不存在")

    # 1. 案件材料库（scope=case）先清 DB 行，与向量清理解耦（保证一致性）
    #    delete_knowledge 内部先删向量后删 DB 行，向量库一旦异常会把 DB 行删除吞掉，
    #    导致材料库残留。这里改为：先算好各条目向量块 id → 删并提交 DB 行 → 再 best-effort 清向量。
    from core.knowledge.service import chunk_text, get_store
    case_entries = db.query(KnowledgeEntry).filter(
        KnowledgeEntry.scope == "case", KnowledgeEntry.case_id == case_id,
    ).all()
    # 删除前按各条目内容算出向量块 id（content 随后随 DB 行消失）
    entry_chunk_ids: list = []
    for e in case_entries:
        n = len(chunk_text(e.content))
        entry_chunk_ids.append([f"{e.id}:{i}" for i in range(n)])
    for e in case_entries:
        db.delete(e)
    db.commit()  # DB 行先落定：即便向量清理抛错，材料库也不残留
    # 2. 清理向量块（best-effort，失败不阻断）
    for ids in entry_chunk_ids:
        try:
            get_store().delete(ids)
        except Exception:
            pass

    # 3. 检索记录无外键级联，手动清
    db.query(RetrievalRecord).filter(RetrievalRecord.case_id == case_id).delete()

    # 4. 删案件本体（级联带走其余子表）
    db.delete(case)
    db.commit()
    return {"ok": True}
