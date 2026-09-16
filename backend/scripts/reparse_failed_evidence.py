#!/usr/bin/env python3
"""重解析证据：对已上传但系统未能读取（parse_status=failed）的证据原件重新 OCR/解析，
回写 parsed_text/parse_status，并重建个案材料库与 ctx.evidence_texts。

背景：2026-09-16 栖木家居案因环境缺 OCR 引擎，5 张 PNG 证据全部解析失败，
证据文本缺失导致红线引擎误判"证据缺失"而硬门禁拦截。安装 rapidocr 后本脚本把
历史失败证据补跑出来，让红线判定基于真实证据内容。

用法：
    python reparse_failed_evidence.py [case_id]
case_id 省略时，处理全库所有 failed 证据文件。
"""
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
sys.path.insert(0, BACKEND)

from core.database import SessionLocal, Case, EvidenceFile, KnowledgeEntry
from core import file_store
from core.evidence_parser import ocr_image, is_image_file, parse_pdf, is_pdf_file
from core.text_extractor import extract_text_from_file
from core.knowledge.service import delete_knowledge, ingest_case_materials


def evidence_snippets(records):
    return "\n\n".join(
        f"【证据文件: {r['file_name']}】\n{r['text']}"
        for r in records
        if r.get("text")
    )


def reparse_case(db, case_id):
    files = (
        db.query(EvidenceFile)
        .filter(EvidenceFile.case_id == case_id, EvidenceFile.parse_status == "failed")
        .all()
    )
    print(f"[case {case_id}] {len(files)} 个 failed 文件")
    if not files:
        return 0

    reparsed = 0
    for ef in files:
        p = file_store.original_path(ef.storage_uri)
        if not p:
            print(f"  skip {ef.file_name}: 无原件 ({ef.storage_uri})")
            continue
        content = p.read_bytes()
        if is_pdf_file(ef.file_name):
            res = parse_pdf(content, ef.file_name)
        elif is_image_file(ef.file_name):
            res = ocr_image(content, ef.file_name)
        elif ef.file_name.lower().endswith(".docx"):
            res = extract_text_from_file(content, ef.file_name)
        else:
            res = {"success": False, "text": "", "error": "unsupported"}
        if res.get("success") and res.get("text", "").strip():
            ef.parse_status = "ok"
            ef.parsed_text = res["text"]
            reparsed += 1
            print(f"  OK   {ef.file_name}: {len(res['text'])} 字")
        else:
            print(f"  FAIL {ef.file_name}: {res.get('error')}")
    db.commit()

    # 重建 ctx.evidence_texts：保留手工粘贴部分，替换所有文件片段
    case = db.query(Case).filter(Case.id == case_id).first()
    if case is None:
        return reparsed
    from core.case_context import CaseContext

    ctx = CaseContext.from_dict(case.context_json or {})
    di = ctx.defendant_info or {}
    current = di.get("evidence_texts") or ""
    manual = "\n\n".join(
        b for b in current.split("\n\n") if not b.lstrip().startswith("【证据文件:")
    ).strip()
    all_recs = [
        {"file_name": f.file_name, "text": f.parsed_text or ""}
        for f in db.query(EvidenceFile).filter(EvidenceFile.case_id == case_id).all()
    ]
    fresh = evidence_snippets(all_recs)
    di["evidence_texts"] = (manual + "\n\n" if manual else "") + fresh
    ctx.defendant_info = di
    case.context_json = ctx.to_dict()
    db.commit()

    # 重建个案材料库（先清后增）
    for e in (
        db.query(KnowledgeEntry)
        .filter(KnowledgeEntry.scope == "case", KnowledgeEntry.case_id == case_id)
        .all()
    ):
        delete_knowledge(db, e.id)
    if fresh.strip():
        try:
            ingest_case_materials(db, case_id, fresh)
        except Exception as exc:  # 入库失败不阻断主流程
            print(f"  警告：个案材料库重建失败（{exc}），不影响证据解析回写")
    db.commit()
    print(f"  -> 重解析 {reparsed} 个；evidence_texts 共 {len(fresh)} 字")
    return reparsed


def main():
    case_id = sys.argv[1] if len(sys.argv) > 1 else None
    db = SessionLocal()
    try:
        if case_id:
            reparse_case(db, case_id)
        else:
            total = 0
            for c in db.query(Case).all():
                total += reparse_case(db, c.id)
            print(f"全库共重解析 {total} 个失败文件")
    finally:
        db.close()


if __name__ == "__main__":
    main()
