"""
L1 上传能力集成测试
- 案情描述文件导入（txt/docx/pdf）
- 证据 ZIP 批量上传与解析摘要
"""
import io
import json
import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("USE_MOCK", "True")

import fitz
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from main import app
    from core.database import init_db
    init_db()
    with TestClient(app) as c:
        yield c


def _pdf_bytes(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text, fontname="china-ss", fontsize=12)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def _make_zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


class TestDescriptionUpload:

    def test_upload_description_txt(self, client):
        files = {"file": ("desc.txt", io.BytesIO("原告 LV 侵权".encode("utf-8")), "text/plain")}
        resp = client.post("/api/cases/upload-description-text", files=files)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "LV" in data["text"]
        assert data["filename"] == "desc.txt"

    def test_upload_description_unsupported(self, client):
        files = {"file": ("desc.xls", io.BytesIO(b"x"), "application/octet-stream")}
        resp = client.post("/api/cases/upload-description-text", files=files)
        assert resp.status_code == 400
        assert "不支持" in resp.text

    def test_upload_description_too_large(self, client):
        files = {"file": ("big.txt", io.BytesIO(b"x" * (5 * 1024 * 1024 + 1)), "text/plain")}
        resp = client.post("/api/cases/upload-description-text", files=files)
        assert resp.status_code == 400
        assert "超过" in resp.text


class TestEvidenceZipUpload:

    def test_create_case_with_zip_evidence(self, client):
        zip_bytes = _make_zip({
            "readme.txt": "证据清单：图片 10 张".encode("utf-8"),
            "proof.pdf": _pdf_bytes("PDF 证据文本"),
        })
        files = {
            "evidence_files": ("evidence.zip", io.BytesIO(zip_bytes), "application/zip"),
        }
        payload = {
            "name": "ZIP 证据案件",
            "cause_type": "商标侵权",
            "goal_type": "要钱",
            "client_org": "我方",
            "defendant_name": "被告",
            "case_description": "案情",
            "evidence_texts": "",
            "draft": False,
        }
        resp = client.post(
            "/api/cases",
            data={"payload": json.dumps(payload)},
            files=files,
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()
        assert result["status"] == "pending"
        summary = result.get("parse_summary")
        assert summary is not None
        assert summary["total"] == 2
        assert summary["ok"] == 2
        assert summary["failed"] == 0

        detail = client.get(f"/api/cases/{result['id']}").json()
        assert "证据清单" in detail["context"]["defendant_info"]["evidence_texts"]
        assert "PDF 证据文本" in detail["context"]["defendant_info"]["evidence_texts"]

    def test_update_draft_with_zip_appends_evidence(self, client):
        created = client.post("/api/cases", json={"name": "草稿", "draft": True}).json()
        case_id = created["id"]

        zip_bytes = _make_zip({"appendix.md": "追加证据".encode("utf-8")})
        resp = client.put(
            f"/api/cases/{case_id}",
            data={"payload": json.dumps({"name": "草稿", "draft": True, "evidence_texts": "原始"})},
            files={"evidence_files": ("append.zip", io.BytesIO(zip_bytes), "application/zip")},
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()
        summary = result.get("parse_summary")
        assert summary["total"] == 1
        assert summary["ok"] == 1

        detail = client.get(f"/api/cases/{case_id}").json()
        assert "原始" in detail["context"]["defendant_info"]["evidence_texts"]
        assert "追加证据" in detail["context"]["defendant_info"]["evidence_texts"]
