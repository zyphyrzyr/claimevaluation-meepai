"""
L0 证据文件上传

覆盖：
1. JSON 建案路径保持向后兼容；
2. multipart 上传 PDF 后，EvidenceFile 表写入解析记录，文本追加到 evidence_texts；
3. 不支持的文件格式不阻断建案，但记录失败状态。
"""
import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("USE_MOCK", "True")

import fitz
from fastapi.testclient import TestClient


def _pdf_bytes(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    # 使用 PyMuPDF 内置中文字体，确保提取时中文不变成方块
    page.insert_text((72, 72), text, fontname="china-ss", fontsize=12)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


@pytest.fixture
def client():
    from main import app
    from core.database import init_db
    init_db()
    with TestClient(app) as c:
        yield c


def _create_payload():
    return {
        "name": "上传测试案",
        "cause_type": "商标侵权",
        "goal_type": "要钱",
        "case_description": "测试案情描述",
        "client_org": "我方公司",
        "defendant_name": "被告公司",
        "evidence_texts": "手工粘贴的证据文本。",
    }


class TestEvidenceUpload:

    def test_json_create_case_still_works(self, client):
        """改动 create_case 后，原有 JSON 调用方不能坏。"""
        resp = client.post("/api/cases", json=_create_payload())
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "上传测试案"

    def test_pdf_upload_creates_evidence_file_and_appends_text(self, client):
        """multipart 上传 PDF，解析文本追加到 evidence_texts 并入库 EvidenceFile。"""
        payload = _create_payload()
        pdf = _pdf_bytes("商标注册证第1234567号，核定使用商品第25类服装。")
        resp = client.post(
            "/api/cases",
            data={"payload": json.dumps(payload, ensure_ascii=False)},
            files=[("evidence_files", ("cert.pdf", pdf, "application/pdf"))],
        )
        assert resp.status_code == 200, resp.text
        case_id = resp.json()["id"]

        detail = client.get(f"/api/cases/{case_id}").json()
        ctx = detail["context"]
        evidence_texts = ctx["defendant_info"]["evidence_texts"]

        assert "手工粘贴的证据文本" in evidence_texts
        assert "商标注册证第1234567号" in evidence_texts
        assert "【证据文件: cert.pdf】" in evidence_texts

        from core.database import SessionLocal, EvidenceFile
        db = SessionLocal()
        try:
            ef = db.query(EvidenceFile).filter(EvidenceFile.case_id == case_id).first()
            assert ef is not None
            assert ef.file_name == "cert.pdf"
            assert ef.file_type == "application/pdf"
            assert ef.parse_status == "ok"
            assert "商标注册证第1234567号" in ef.parsed_text
        finally:
            db.close()

    def test_unsupported_file_format_does_not_block_case_creation(self, client):
        """上传不支持的格式时，建案应继续，EvidenceFile 标记为失败。"""
        payload = _create_payload()
        resp = client.post(
            "/api/cases",
            data={"payload": json.dumps(payload, ensure_ascii=False)},
            files=[("evidence_files", ("virus.exe", b"MZ", "application/octet-stream"))],
        )
        assert resp.status_code == 200, resp.text
        case_id = resp.json()["id"]

        from core.database import SessionLocal, EvidenceFile
        db = SessionLocal()
        try:
            ef = db.query(EvidenceFile).filter(EvidenceFile.case_id == case_id).first()
            assert ef is not None
            assert ef.file_name == "virus.exe"
            assert ef.parse_status == "failed"
            assert ef.parsed_text == ""
        finally:
            db.close()
