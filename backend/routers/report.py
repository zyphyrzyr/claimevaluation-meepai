"""
输出物导出路由（P2 + P4）
- GET  /api/report/{case_id}/result.docx      评估结果 Word 导出
- GET  /api/report/{case_id}/result.pdf       评估结果 PDF 导出
- GET  /api/report/{case_id}/transcript.docx  庭审记录 Word 导出
- GET  /api/report/{case_id}/transcript.pdf   庭审记录 PDF 导出

关于「决策备忘录」：该页面与它的定稿快照机制（snapshot / versions）已整体下架，
不再提供端点。但底层的 markdown 生成器（core/report_generator）与导出器
（core/docx_export / core/pdf_export）保留——评估结果导出正是复用它，
两者的内容源是同一份 markdown。
"""

import re
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from core.auth import case_readable
from core.case_context import CaseContext
from core.database import Case, get_db
from core.docx_export import DOCX_MIME, markdown_to_docx_bytes
from core.pdf_export import PDF_MIME, markdown_to_pdf_bytes
from core.report_generator import generate_memo

router = APIRouter()


def _content_disposition(filename: str) -> str:
    """
    中文文件名必须走 RFC 5987（filename*=UTF-8''…）。

    只给 filename= 的话，Chrome 会把文件名里的中文变成 URL 转义串下载下来，
    用户拿到的文件叫 %E5%86%B3%E7%AD%96…docx——演示现场当场社死。
    同时保留一个纯 ASCII 的 filename 兜底，老客户端才不至于完全没有名字。
    """
    # 兜底名的做法是**丢掉**非 ASCII 字符，而不是替换成下划线：
    # 「E2E-版本-决策备忘录」替换成下划线会变成 E2E-__-_____ 这种谁都不想
    # 看到的东西，丢掉则是干净的 E2E。
    base, _, ext = filename.rpartition(".")
    cleaned = re.sub(r"[^\x20-\x7e]", "", base)
    cleaned = re.sub(r'[\\/:*?"<>|]', "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(".-")
    fallback = f"{cleaned}.{ext}" if len(cleaned) >= 2 else f"report.{ext}"
    return (f'attachment; filename="{fallback}"; '
            f"filename*=UTF-8''{quote(filename)}")


def _docx_response(content: bytes, filename: str) -> Response:
    return Response(
        content=content,
        media_type=DOCX_MIME,
        headers={"Content-Disposition": _content_disposition(filename)},
    )


def _meta_lines(case: Case, ctx: CaseContext, suffix: str = "") -> list:
    """
    补充信息行——只放正文里没有的东西。

    案由、业务目标这些正文开头已经有了，再写一遍就是两行几乎一样的抬头，
    看着像复制粘贴没改干净。这里只补生成日期与版本状态。
    """
    stamp = datetime.now().strftime("%Y-%m-%d")
    return [f"生成日期：{stamp}" + (f" ｜ {suffix}" if suffix else "")]


def _load_case(db: Session, case_id: str) -> Case:
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(404, "案件不存在")
    return case


def _load_ctx(case: Case) -> CaseContext:
    ctx = CaseContext.from_dict(case.context_json or {})
    ctx.case_id = case.id
    return ctx


@router.get("/{case_id}/result.docx")
def download_result_docx(case_id: str, pkulaw: bool = False,
                         db: Session = Depends(get_db),
                         case: Case = Depends(case_readable)):
    """
    评估结果 Word 导出。

    pkulaw=true 时额外跑北大法宝检索与引用核验，文档多出「法律检索与引用核验」章节。
    默认关闭：该步骤要打外部 API，会拖慢响应，而绝大多数下载场景并不需要。
    未配置 PKULAW_API_TOKEN 时即使传 true 也会整段跳过，不留空章节。
    """
    ctx = _load_ctx(case)
    markdown = generate_memo(case.name, ctx, pkulaw=pkulaw)["markdown"]

    # 不另传 title：markdown 正文开头已有 H1「主诉评估结果：{案件名}」，
    # 再传一遍会在 Word 里出现两个一模一样的标题。
    content = markdown_to_docx_bytes(markdown, meta=_meta_lines(case, ctx),
                                     header_text=case.name)
    return _docx_response(content, f"{case.name}-评估结果.docx")


@router.get("/{case_id}/result.pdf")
def download_result_pdf(case_id: str, pkulaw: bool = False,
                        db: Session = Depends(get_db),
                        case: Case = Depends(case_readable)):
    """评估结果 PDF 导出（与 Word 导出同源：同一份 markdown 内容）"""
    ctx = _load_ctx(case)
    markdown = generate_memo(case.name, ctx, pkulaw=pkulaw)["markdown"]

    content = markdown_to_pdf_bytes(markdown, meta=_meta_lines(case, ctx),
                                    header_text=case.name)
    return Response(
        content=content,
        media_type=PDF_MIME,
        headers={"Content-Disposition": _content_disposition(f"{case.name}-评估结果.pdf")},
    )


@router.get("/{case_id}/transcript.docx")
def download_transcript_docx(case_id: str, db: Session = Depends(get_db),
                             case: Case = Depends(case_readable)):
    """庭审记录 Word 导出（独立成册）"""
    ctx = _load_ctx(case)
    if not ctx.moot_transcript:
        raise HTTPException(404, "尚未进行模拟法庭")

    lines = [f"# 模拟法庭庭审记录：{case.name}", ""]
    for r in ctx.moot_transcript:
        lines += [f"## 【{r['step_name']}】{r['role_name']}", "", r["content"], ""]
    lines += ["---", f"修正系数：{ctx.correction_coeff}"]

    content = markdown_to_docx_bytes(
        "\n".join(lines),
        meta=_meta_lines(case, ctx, suffix=f"修正系数 {ctx.correction_coeff}"))
    return _docx_response(content, f"{case.name}-庭审记录.docx")


@router.get("/{case_id}/transcript.pdf")
def download_transcript_pdf(case_id: str, db: Session = Depends(get_db),
                            case: Case = Depends(case_readable)):
    """庭审记录 PDF 导出（独立成册，与 Word 导出同源：同一份 markdown 内容）"""
    ctx = _load_ctx(case)
    if not ctx.moot_transcript:
        raise HTTPException(404, "尚未进行模拟法庭")

    lines = [f"# 模拟法庭庭审记录：{case.name}", ""]
    for r in ctx.moot_transcript:
        lines += [f"## 【{r['step_name']}】{r['role_name']}", "", r["content"], ""]
    lines += ["---", f"修正系数：{ctx.correction_coeff}"]

    content = markdown_to_pdf_bytes(
        "\n".join(lines),
        meta=_meta_lines(case, ctx, suffix=f"修正系数 {ctx.correction_coeff}"))
    return Response(
        content=content,
        media_type=PDF_MIME,
        headers={"Content-Disposition": _content_disposition(f"{case.name}-庭审记录.pdf")},
    )
