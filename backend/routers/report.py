"""
决策备忘录路由（P2）
- GET  /api/report/{case_id}/memo      生成当前状态备忘录（不落库）
- POST /api/report/{case_id}/snapshot  定稿快照（版本化，v1/v2 并存）
- GET  /api/report/{case_id}/versions  版本列表
- GET  /api/report/{case_id}/versions/{version}  读取某版本
- GET  /api/report/{case_id}/transcript 庭审记录（markdown）
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core.case_context import CaseContext
from core.database import Case, Report, get_db
from core.report_generator import generate_memo, render_memo_markdown

router = APIRouter()


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
