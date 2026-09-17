"""
案件管理路由：建案（案情+案由+业务目标+被告信息+观点注入）、列表、详情
"""

from typing import Dict, List, Optional
import re

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from core.auth import case_owned, case_readable, current_user_optional, require_user
from core.case_context import CaseContext
from core.config import SUPPORTED_CAUSE_TYPES, GOAL_TYPES
from core.database import (
    Case, EvidenceFile, Party, KnowledgeEntry, RetrievalRecord, User, get_db,
)
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


class CaseRow(CaseOut):
    """列表行：在基础字段上补当事人。

    当事人存在 parties 表（按 case_id 拆开），不在 cases 表里；而搜索要按原被告匹配，
    结果里也必须看得见——否则搜「顾家家居」搜到了，用户看不出匹配在哪一行。
    没有当事人记录的案件这两个字段是 None（早期草稿与 E2E 测试案有不少这种情况）。
    """
    plaintiff: Optional[str] = None
    defendant: Optional[str] = None
    # 公共示例数据（user_id 为空）。前端据此把行标成「公共·只读」，
    # 否则用户点进去做一半发现改不了，界面上看不出原因。
    is_public: bool = False


class CasePage(BaseModel):
    """分页信封。

    列表不再是裸数组：全库已有 279 个案件，裸数组会逼着前端一口气全渲染，
    既没有「第几页」的概念，服务端也失去了只取一页的余地。
    """
    items: List[CaseRow]
    total: int
    page: int
    page_size: int


# 每页条数上限：前端会传 10 / 20 / 50，这里再兜一道，避免有人手拼 URL 拉全库
DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 50


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
async def create_case(
    request: Request,
    db: Session = Depends(get_db),
    # 建案要登录：不登录建的案子挂在谁名下无从判断，
    # 而「无主案件」正是这次要消灭的状态——它既不能被别人看到（跨账号串数据），
    # 也不能被建它的人看到（换个浏览器就没了）。
    user: User = Depends(require_user),
):
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
            user_id=user.id,
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
        user_id=user.id,
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


@router.get("", response_model=CasePage)
def list_cases(q: str = "", page: int = 1, page_size: int = DEFAULT_PAGE_SIZE,
               db: Session = Depends(get_db),
               user: Optional[User] = Depends(current_user_optional)):
    """案件列表（分页 + 关键词搜索）。

    q 按空格分词，**词与词之间是 AND**（每个词都得命中），每个词匹配
    案件名称或该案下任一当事人名称——所以「栖木 顾家」能直接定位到那件案子。

    为什么必须走子查询而不是只匹配名称：当事人落在 parties 表，而案件名称里**不一定**
    写着当事人（全库只有个位数案件名含「诉」字），靠名称推断当事人的路子不成立——
    截图里「栖木家居有限公司诉顾家家居股份有限公司…」这种是少数。

    可见范围：**自己的案件 + 公共案件**（user_id 为空的存量数据）。
    未登录时只剩下公共那部分，所以匿名进来看不到别人建的东西。
    """
    page = max(1, page)
    page_size = min(MAX_PAGE_SIZE, max(1, page_size))

    query = db.query(Case)
    if user is None:
        query = query.filter(Case.user_id.is_(None))
    else:
        query = query.filter(or_(Case.user_id == user.id, Case.user_id.is_(None)))
    for term in (q or "").split():
        like = f"%{term}%"
        query = query.filter(or_(
            Case.name.ilike(like),
            Case.id.in_(db.query(Party.case_id).filter(Party.name.ilike(like))),
        ))

    total = query.count()
    # 页码越界自愈：删掉最后一页仅剩的几条后，前端手上的页码会指向不存在的页。
    # 这里夹到最后一页并把它回给前端，前端据此同步页码——比让前端算「该不该退一页」可靠。
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, total_pages)
    # 次级排序键不能省：同一天建案的很多（E2E-* 系列全落在 9/1），
    # 相同 created_at 之间顺序不稳定，翻页时会重复出现某些行、又漏掉另一些。
    rows = (query.order_by(Case.created_at.desc(), Case.id.desc())
            .offset((page - 1) * page_size).limit(page_size).all())

    # 本页当事人的一次取完，别在循环里逐案查（N+1）
    names: Dict[str, Dict[str, List[str]]] = {}
    ids = [c.id for c in rows]
    if ids:
        for p in db.query(Party).filter(Party.case_id.in_(ids)).all():
            if not p.name:
                continue
            slot = names.setdefault(p.case_id, {"plaintiff": [], "defendant": []})
            if p.role in slot:
                slot[p.role].append(p.name)

    items = []
    for c in rows:
        slot = names.get(c.id, {})
        items.append(CaseRow(
            id=c.id, name=c.name, cause_type=c.cause_type,
            goal_type=c.goal_type, status=c.status,
            created_at=c.created_at.isoformat(),
            # 一个案子可能有多个原告/被告，顿号连起来给前端一行显示
            plaintiff="、".join(slot.get("plaintiff") or []) or None,
            defendant="、".join(slot.get("defendant") or []) or None,
            is_public=(c.user_id is None),
        ))
    return CasePage(items=items, total=total, page=page, page_size=page_size)


@router.get("/{case_id}")
def case_detail(case: Case = Depends(case_readable), db: Session = Depends(get_db)):
    evidence_files = [
        {"id": ef.id, "file_name": ef.file_name, "parse_status": ef.parse_status}
        for ef in db.query(EvidenceFile).filter(EvidenceFile.case_id == case.id).all()
    ]
    return {
        "id": case.id,
        "name": case.name,
        "cause_type": case.cause_type,
        "goal_type": case.goal_type,
        "status": case.status,
        "is_public": case.user_id is None,
        "case_description": case.case_description,
        "client_org": case.client_org,
        "evidence_files": evidence_files,
        "context": case.context_json,
    }


@router.put("/{case_id}", response_model=CaseOut)
async def update_draft(
    request: Request, case: Case = Depends(case_owned), db: Session = Depends(get_db)
):
    """编辑案件：覆盖文本字段、重新解析上传文件、重建当事人与 context。草稿/已评估案件均可改，状态保持原值；评估运行中禁止。"""
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
    for p in db.query(Party).filter(Party.case_id == case.id).all():
        db.delete(p)
    for p in parties:
        db.add(Party(case_id=case.id, role=p["role"],
                     name=p["name"], party_type=p["party_type"]))

    # 关键修复：_build_context 会重建一个空 context，若直接落库会把已有评估结果整笔抹掉
    # （「编辑案件」「仅开始模拟法庭」都会走这条 PUT，正是 8d9bcc0c 等案例结果被清空的根因）。
    # 只让「案件输入」字段随编辑更新，把「评估产出 + 用户输入」从旧 context 搬回新 ctx。
    old_ctx = CaseContext.from_dict(case.context_json or {})
    _CTX_PRESERVE = (
        # 决策层产出
        "dimension_results", "scores", "confidence", "red_flags", "recommendation",
        "defendant_profile", "recovery_ability",
        # 证据盘点产出（重跑会整体重算覆盖，编辑不该清掉）
        "evidence_matrix", "gap_list", "extra_evidence",
        "evidence_completeness", "evidence_note",
        # 模拟法庭
        "moot_transcript", "correction_coeff",
        # 用户输入（编辑案件不应丢失用户注入的观点/知识、追问历史、审计轨迹）
        "injected_knowledge", "user_viewpoints", "advisor_messages", "audit_trail",
    )
    for _f in _CTX_PRESERVE:
        setattr(ctx, _f, getattr(old_ctx, _f))

    case.context_json = ctx.to_dict()
    db.commit()

    # 证据文本变化后重建本案材料库（先清后增，防重复条目）
    _rebuild_case_knowledge(db, case.id, ctx.defendant_info["evidence_texts"])

    return CaseOut(id=case.id, name=case.name, cause_type=case.cause_type,
                   goal_type=case.goal_type, status=case.status,
                   created_at=case.created_at.isoformat(),
                   parse_summary=_build_parse_summary(file_records, zip_summaries))


@router.post("/{case_id}/start-evaluation", response_model=CaseOut)
def start_evaluation(case: Case = Depends(case_owned), db: Session = Depends(get_db)):
    """启动评估：校验带 * 的必填项，补全当事人与 context，置 pending。草稿/已评估案件均可重新评估；评估进行中禁止。"""
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
    has_files = db.query(EvidenceFile).filter(EvidenceFile.case_id == case.id).count() > 0
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
    for p in db.query(Party).filter(Party.case_id == case.id).all():
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


@router.post("/{case_id}/claim", response_model=CaseOut)
def claim_case(case: Case = Depends(case_readable), db: Session = Depends(get_db),
               user: User = Depends(require_user)):
    """把一条公共案件认领到自己名下。

    为什么需要它：存量案件迁移后全是公共的，而公共数据是只读的（否则谁都能改
    共享数据）。没有这个口子，用户连自己过去积累的案件都改不动 —— 从「能改」变成
    「只能看」是一次静默的功能倒退。认领是唯一的、显式的一次性动作，
    认领后该案件就只属于认领人，其他人列表里不再出现。
    """
    if case.user_id is not None:
        # 已是自己的：重复认领不该报错，否则前端「认领」按钮点两次就炸
        if case.user_id != user.id:
            raise HTTPException(403, "该案件已被其他用户认领")
        return CaseOut(id=case.id, name=case.name, cause_type=case.cause_type,
                       goal_type=case.goal_type, status=case.status,
                       created_at=case.created_at.isoformat())

    case.user_id = user.id
    db.commit()
    return CaseOut(id=case.id, name=case.name, cause_type=case.cause_type,
                   goal_type=case.goal_type, status=case.status,
                   created_at=case.created_at.isoformat())


@router.delete("/{case_id}/evidence-files/{file_id}")
def delete_evidence_file(file_id: str, case: Case = Depends(case_owned),
                         db: Session = Depends(get_db)):
    """删除已上传的证据文件：移除 EvidenceFile 记录、从 evidence_texts 剥离其解析片段、重建本案材料库。草稿/已评估案件均可；评估进行中禁止。"""
    if case.status == "evaluating":
        raise HTTPException(400, "评估运行中，请等待本次评估完成后再删除文件")
    ef = db.query(EvidenceFile).filter(
        EvidenceFile.id == file_id, EvidenceFile.case_id == case.id,
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
    _rebuild_case_knowledge(db, case.id, remaining)

    return {"ok": True}


def _get_ef(case: Case, file_id: str, db: Session) -> EvidenceFile:
    """校验 file_id 确实属于该案件（防越权读他人文件）。

    入参改成 case 对象而不是 case_id：归属判定已经在依赖项里做过一次，
    这里再按 id 查一遍不仅多余，还容易被后加的调用点漏掉那次查询。
    """
    ef = db.query(EvidenceFile).filter(
        EvidenceFile.id == file_id, EvidenceFile.case_id == case.id,
    ).first()
    if not ef:
        raise HTTPException(404, "文件记录不存在")
    return ef


@router.get("/{case_id}/evidence-files/{file_id}")
def evidence_file_detail(file_id: str, case: Case = Depends(case_readable),
                         db: Session = Depends(get_db)):
    """预览用元信息：解析文本、是否有原件、大小、预览类型。"""
    ef = _get_ef(case, file_id, db)
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
def evidence_file_raw(file_id: str, case: Case = Depends(case_readable),
                      db: Session = Depends(get_db)):
    """原始字节流（inline），供 PDF 原生渲染 / 图片显示 / 下载原件。无原件返回 404。"""
    ef = _get_ef(case, file_id, db)
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
def evidence_file_preview(file_id: str, case: Case = Depends(case_readable),
                          db: Session = Depends(get_db)):
    """doc/docx 经 textutil 转出的 HTML（带缓存），供沙箱 iframe 渲染；不可用返回 406。"""
    ef = _get_ef(case, file_id, db)
    html = file_store.doc_to_html(ef.storage_uri)
    if html is None:
        raise HTTPException(406, "该文件类型不支持在线预览（仅 doc/docx 可预览，且需本机 textutil）")
    return HTMLResponse(html)


@router.delete("/{case_id}")
def delete_case(case: Case = Depends(case_owned), db: Session = Depends(get_db)):
    """删除案件：清案件材料库（向量 + DB）、检索记录，再删案件本体；
    SQLAlchemy cascade 自动带走 Party/EvidenceFile/RuleHit/ScoreSnapshot/MootRound/Report/AuditEvent。"""
    # 1. 案件材料库（scope=case）先清 DB 行，与向量清理解耦（保证一致性）
    #    delete_knowledge 内部先删向量后删 DB 行，向量库一旦异常会把 DB 行删除吞掉，
    #    导致材料库残留。这里改为：先算好各条目向量块 id → 删并提交 DB 行 → 再 best-effort 清向量。
    from core.knowledge.service import chunk_text, get_store
    case_entries = db.query(KnowledgeEntry).filter(
        KnowledgeEntry.scope == "case", KnowledgeEntry.case_id == case.id,
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
    db.query(RetrievalRecord).filter(RetrievalRecord.case_id == case.id).delete()

    # 4. 删案件本体（级联带走其余子表）
    db.delete(case)
    db.commit()
    return {"ok": True}
