"""
决策备忘录路由（P2 + P4 输出物导出）
- GET  /api/report/{case_id}/memo      生成当前状态备忘录（不落库）
- GET  /api/report/{case_id}/memo.docx       备忘录 Word 导出
- GET  /api/report/{case_id}/transcript.docx 庭审记录 Word 导出
- POST /api/report/{case_id}/snapshot  定稿快照（版本化，v1/v2 并存）
- GET  /api/report/{case_id}/versions  版本列表
- GET  /api/report/{case_id}/versions/{version}  读取某版本
- GET  /api/report/{case_id}/transcript 庭审记录（markdown）
"""

import re
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from core.case_context import CaseContext
from core.database import Case, Report, get_db
from core.docx_export import DOCX_MIME, markdown_to_docx_bytes
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


@router.get("/{case_id}/memo")
def get_memo(case_id: str, pkulaw: bool = False, db: Session = Depends(get_db)):
    """
    生成当前状态的决策备忘录（实时，不落库）

    pkulaw=true 时额外跑北大法宝检索与引用核验，报告多出「法律检索与引用核验」章节。
    默认关闭：该步骤要打外部 API，会拖慢响应，而绝大多数预览场景并不需要。
    未配置 PKULAW_API_TOKEN 时即使传 true 也会整段跳过，不留空章节。
    """
    case = _load_case(db, case_id)
    ctx = _load_ctx(case)
    return generate_memo(case.name, ctx, pkulaw=pkulaw)


@router.get("/{case_id}/memo.docx")
def download_memo_docx(case_id: str, version: int = 0, pkulaw: bool = False,
                       db: Session = Depends(get_db)):
    """
    备忘录 Word 导出。

    version=0（默认）导当前状态，version=N 导第 N 版定稿快照——
    对外发出的版本必须是定稿那一份，而不是「现在又跑了一次」的结果。
    """
    case = _load_case(db, case_id)

    if version:
        report = (db.query(Report)
                  .filter(Report.case_id == case.id, Report.report_type == "memo",
                          Report.version == version).first())
        if not report:
            raise HTTPException(404, f"版本 v{version} 不存在")
        markdown, suffix = report.markdown_content, f"定稿版本 v{version}"
    else:
        ctx = _load_ctx(case)
        markdown = generate_memo(case.name, ctx, pkulaw=pkulaw)["markdown"]
        suffix = "当前状态（未定稿）"

    # 不另传 title：markdown 正文开头已有 H1「主诉评估决策备忘录：{案件名}」，
    # 再传一遍会在 Word 里出现两个一模一样的标题。
    content = markdown_to_docx_bytes(
        markdown, meta=_meta_lines(case, ctx=_load_ctx(case), suffix=suffix))
    return _docx_response(content, f"{case.name}-决策备忘录.docx")


@router.get("/{case_id}/transcript.docx")
def download_transcript_docx(case_id: str, db: Session = Depends(get_db)):
    """庭审记录 Word 导出（独立成册）"""
    case = _load_case(db, case_id)
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


@router.post("/{case_id}/snapshot")
def snapshot(case_id: str, pkulaw: bool = False, db: Session = Depends(get_db)):
    """
    定稿快照：对当前状态拍照存档，版本号自增，历史版本可对比

    pkulaw=true 时先跑法宝核验再存档——定稿是对外输出的版本，引用真实性核验
    在这个环节价值最大。默认关闭以保持定稿的幂等（同样的输入同样的版本内容）。
    """
    case = _load_case(db, case_id)
    ctx = _load_ctx(case)
    if ctx.scores.get("final") is None:
        raise HTTPException(400, "案件尚未完成评估，无法定稿")

    memo = generate_memo(case.name, ctx, pkulaw=pkulaw)
    latest = (db.query(Report)
              .filter(Report.case_id == case.id, Report.report_type == "memo")
              .order_by(Report.version.desc()).first())
    version = (latest.version + 1) if latest else 1
    db.add(Report(case_id=case.id, report_type="memo", version=version,
                  markdown_content=memo["markdown"]))
    db.commit()
    return {"ok": True, "version": version}


@router.get("/{case_id}/versions")
def list_versions(case_id: str, db: Session = Depends(get_db)):
    case = _load_case(db, case_id)
    reports = (db.query(Report)
               .filter(Report.case_id == case.id, Report.report_type == "memo")
               .order_by(Report.version.desc()).all())
    return [{"id": r.id, "version": r.version, "generated_at": r.generated_at.isoformat()}
            for r in reports]


@router.get("/{case_id}/versions/{version}")
def get_version(case_id: str, version: int, db: Session = Depends(get_db)):
    case = _load_case(db, case_id)
    report = (db.query(Report)
              .filter(Report.case_id == case.id, Report.report_type == "memo",
                      Report.version == version).first())
    if not report:
        raise HTTPException(404, f"版本 v{version} 不存在")
    return {"id": report.id, "version": report.version,
            "markdown": report.markdown_content,
            "generated_at": report.generated_at.isoformat()}


@router.get("/{case_id}/transcript")
def get_transcript(case_id: str, db: Session = Depends(get_db)):
    """庭审记录（独立成册，可导出 PDF，P4 接）"""
    case = _load_case(db, case_id)
    ctx = _load_ctx(case)
    if not ctx.moot_transcript:
        raise HTTPException(404, "尚未进行模拟法庭")
    lines = [f"# 模拟法庭庭审记录：{case.name}", ""]
    for r in ctx.moot_transcript:
        lines.append(f"## 【{r['step_name']}】{r['role_name']}")
        lines.append("")
        lines.append(r["content"])
        lines.append("")
    lines.append(f"---")
    lines.append(f"修正系数：{ctx.correction_coeff}")
    return {"markdown": "\n".join(lines), "correction_coeff": ctx.correction_coeff}
