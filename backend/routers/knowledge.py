"""
知识库路由（§7 RAG）
- 全局经验库 / 案件材料库 CRUD + 语义检索
- 案件材料自动入库（来源 A）、评分链路手动勾选注入、观点沉淀（来源 C）
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.orm import Session

from typing import List, Optional

from core.auth import (
    case_owned, case_readable, current_user_optional, entry_owned, require_user,
)
from core.case_context import CaseContext
from core.database import Case, KnowledgeEntry, User, get_db
from core.knowledge import (
    add_knowledge, delete_knowledge, list_entries, search_knowledge,
    ingest_case_materials, info,
)
from core.report_generator import generate_memo
from core.text_extractor import extract_text_from_file
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


# ---------------------------------------------------------- 文件导入（来源 B：手动录入）

# 个人知识库仅支持文档类（txt/md/docx/pdf）；图片 OCR 走案件证据链，不在此处。
_KB_ALLOWED = ('.txt', '.md', '.markdown', '.docx', '.pdf')
_KB_MAX_SIZE = 20 * 1024 * 1024  # 20MB，独立于案情描述的 5MB 限制


async def _kb_extract(file: UploadFile):
    """校验扩展名/大小并抽取文本；异常一律以 HTTPException 抛出，供单文件/批量复用。

    关键守卫：扫描版 PDF 无文字层时 extract_text_from_file 仍返回 success=true 但
    text 为空——若不过滤，前端会把空文本填进内容框，用户点入库时只会被「内容为空」禁用，
    却看不出原因。所以这里显式拦截空文本。
    """
    name = (file.filename or '').lower()
    if not name:
        raise HTTPException(400, "请选择要上传的文件")
    if not name.endswith(_KB_ALLOWED):
        raise HTTPException(
            400,
            "仅支持文档类文件：.txt / .md / .docx / .pdf（扫描件请先转成带文字层的 PDF）",
        )
    content = await file.read()
    if len(content) > _KB_MAX_SIZE:
        raise HTTPException(400, "文件超过 20MB 上限")
    result = extract_text_from_file(content, file.filename)
    if not result.get("success"):
        raise HTTPException(400, result.get("error", "文件解析失败"))
    text = (result.get("text") or "").strip()
    if not text:
        raise HTTPException(
            400,
            "未提取到任何文字（可能是扫描版 PDF 无文字层）。请改用带文字层的 PDF，或直接粘贴文本。",
        )
    return file.filename, text, result.get("page_count", 0)


@router.post("/upload-file")
async def upload_file(file: UploadFile = File(...), user: User = Depends(require_user)):
    """单文件：抽取文本供前端预览/编辑后再入库（不直接落库）。

    返回 {filename, text, page_count, chars}，前端把 text 填入「内容」框；
    标题留空时自动填文件名（去扩展名），用户校对后点「入个人知识库」。
    """
    filename, text, page_count = await _kb_extract(file)
    return {"filename": filename, "text": text, "page_count": page_count, "chars": len(text)}


@router.post("/import-files")
async def import_files(
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """批量导入：每个文件直接生成一条全局经验（source_type=B，标题=文件名去扩展名）。

    单文件失败不影响其他文件；逐条返回 ok/error，前端汇总成功/失败数。
    """
    out = []
    for f in files:
        try:
            fname, text, _ = await _kb_extract(f)
        except HTTPException as e:
            out.append({"filename": f.filename or "未命名文件", "ok": False, "error": e.detail})
            continue
        title = (f.filename or "未命名文件").rsplit(".", 1)[0] or "未命名文件"
        title = title[:120]
        try:
            entry = add_knowledge(
                db, scope="global", source_type="B",
                title=title, content=text, user_id=user.id,
            )
            out.append({"filename": f.filename, "ok": True, "id": entry.id,
                        "title": title, "chars": len(text)})
        except Exception as e:  # noqa: BLE001
            out.append({"filename": f.filename, "ok": False, "error": f"入库失败：{e}"})
    return {
        "ok": sum(1 for r in out if r["ok"]),
        "failed": sum(1 for r in out if not r["ok"]),
        "results": out,
    }


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


@router.post("/cases/{case_id}/deposit")
def deposit(case_id: str, db: Session = Depends(get_db),
            case: Case = Depends(case_owned), user: User = Depends(require_user)):
    """来源 C：本案评估结果**完整原文**沉淀到全局经验库（跨案复用）。

    内容不再由前端拼装摘要，而是后端用 case 上下文重新生成 markdown 全文——
    与「下载评估结果」导出的 Word/PDF 严格同源（同一个 generate_memo）。
    这样观点沉淀永远等于评估结果原文，不会因前端逻辑漂移。

    本端点无请求体：前端只传 case_id，任何旧客户端传入的 body 都会被忽略。
    """
    ctx = _load_ctx(case)
    content = generate_memo(case.name, ctx)["markdown"].strip()
    if not content:
        raise HTTPException(400, "评估结果为空，无法沉淀")
    # 沉淀出来的全局经验记在自己名下：它是跨案复用的私有经验，
    # 不该变成所有人都看得到、却谁都删不掉的公共数据。
    entry = add_knowledge(db, scope="global", source_type="C",
                          title=f"{case.name} 评估结果", content=content,
                          user_id=user.id)
    return {"ok": True, "id": entry.id, "title": entry.title}
