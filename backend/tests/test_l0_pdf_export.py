"""
L0 输出物导出（PDF）

庭审记录 PDF 与 Word 同源（同一份 markdown，pdf_export 复用 docx_export 的渲染哲学）。
这一层的风险点和 Word 层同构，但表现不同：

1. **中文显示**——PDF 用 STSong-Light CID 字体，必须真的把中文字形嵌进文件，
   否则用 fitz 提取出来是空或方块。
2. **中文文件名**——Content-Disposition 同样要走 RFC 5987（复用 docx 的
   _content_disposition），否则下载名变成 %E5%86%B3%E7%AD%96…pdf。
3. **文件本身是合法 PDF**——能被 PyMuPDF 打开且有 >=1 页，否则下载到打不开的壳。
"""
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("USE_MOCK", "True")

import fitz  # PyMuPDF，验证「真能打开」比自判头更可靠

from core.pdf_export import PDF_MIME, markdown_to_pdf_bytes
from routers.report import _content_disposition

SAMPLE = """\
# 模拟法庭庭审记录：测试案

> 案由：商标侵权 ｜ 业务目标：要钱

## 一、原告陈述

- **核心主张**：被告使用近似标识构成侵权
- 证据一：商标注册证
  - 子项：续展记录

## 二、被告答辩

辩称系合理使用。引用判例 `(2023)最高法民再12号`。

---

修正系数：0.9
"""


@pytest.fixture
def pdf_bytes():
    return markdown_to_pdf_bytes(SAMPLE, meta=["生成日期：2026-09-02 ｜ 修正系数 0.9"])


def _text(data: bytes) -> str:
    return "\n".join(p.get_text() for p in fitz.open(stream=io.BytesIO(data), filetype="pdf"))


# ============================================================
# A. 文件本身
# ============================================================

class TestFileFormat:

    def test_output_is_a_real_pdf(self, pdf_bytes):
        """能被引擎打开才算数，否则用户下载到的是个打不开的文件"""
        assert pdf_bytes[:4] == b"%PDF"
        assert fitz.open(stream=io.BytesIO(pdf_bytes), filetype="pdf").page_count >= 1

    def test_meta_line_is_in_the_document(self, pdf_bytes):
        assert "生成日期：2026-09-02" in _text(pdf_bytes)


# ============================================================
# B. 排版映射
# ============================================================

class TestMarkdownMapping:

    def test_heading_text_is_present(self, pdf_bytes):
        assert "模拟法庭庭审记录：测试案" in _text(pdf_bytes)

    def test_inline_bold_is_parsed(self, pdf_bytes):
        """
        **加粗** 必须把星号吃掉，而不是原样印在 PDF 里。
        （注意：STSong-Light 无独立粗体字形，视觉不加重，但星号绝不能漏出）
        """
        text = _text(pdf_bytes)
        assert "**" not in text
        assert "核心主张" in text

    def test_bullets_render_as_separate_items(self, pdf_bytes):
        """
        markdown 的 - 列表必须渲染成独立的项目（而非挤进一段或被丢弃）。
        注意：reportlab 用「• 」前缀画项目符号，PyMuPDF 文本提取不回吐该字形，
        故这里只校验列表项正文各自落地，不校验 • 字符本身。
        """
        text = _text(pdf_bytes)
        for item in ["核心主张", "证据一", "子项"]:
            assert item in text, f"列表项未渲染：{item}"

    def test_blockquote_is_rendered(self, pdf_bytes):
        assert "案由：商标侵权" in _text(pdf_bytes)

    def test_inline_code_is_rendered(self, pdf_bytes):
        assert "2023" in _text(pdf_bytes)

    def test_rule_does_not_leak_asterisks(self, pdf_bytes):
        """分隔线用 HRFlowable，正文里不该出现一串 * 或 -"""
        text = _text(pdf_bytes)
        assert "***" not in text

    def test_empty_markdown_does_not_crash(self):
        out = markdown_to_pdf_bytes("")
        assert out[:4] == b"%PDF"
        # 空内容仍是合法、可被引擎打开的 PDF（不抛异常即可）
        fitz.open(stream=io.BytesIO(out), filetype="pdf")


# ============================================================
# C. 中文渲染（最容易漏的一项）
# ============================================================

class TestChineseTypography:

    def test_chinese_text_is_extractable(self, pdf_bytes):
        """能提取出中文，说明 CID 字体真的把字形嵌进去了，而不是方块/空白"""
        text = _text(pdf_bytes)
        assert "模拟法庭" in text
        assert "商标注册证" in text

    def test_cid_font_is_registered(self):
        from reportlab.pdfbase import pdfmetrics
        # STSong-Light 一旦注册成功就应在字体表里
        assert "STSong-Light" in pdfmetrics.getRegisteredFontNames()


# ============================================================
# D. 中文文件名
# ============================================================

class TestContentDisposition:

    def test_rfc5987_and_ascii_fallback_both_present(self):
        from urllib.parse import unquote

        header = _content_disposition("某某商标案-庭审记录.pdf")
        assert "filename*=UTF-8''" in header
        assert 'filename="' in header
        encoded = header.split("filename*=UTF-8''")[1]
        assert unquote(encoded) == "某某商标案-庭审记录.pdf"

    def test_pure_chinese_name_still_gets_a_usable_fallback(self):
        header = _content_disposition("庭审记录.pdf")
        assert 'filename="report.pdf"' in header


# ============================================================
# E. 接口
# ============================================================

class TestExportEndpoints:
    """导出接口走真实 HTTP，覆盖缺失数据与正常两条分支"""

    PDF_MAGIC = b"%PDF"

    @pytest.fixture
    def case_id(self):
        from core.database import Case, SessionLocal, init_db
        init_db()
        db = SessionLocal()
        try:
            db.query(Case).filter(Case.id == "pdf-case").delete()
            db.add(Case(id="pdf-case", name="某某商标案", cause_type="商标侵权",
                        goal_type="要钱", status="completed", context_json={}))
            db.commit()
        finally:
            db.close()
        yield "pdf-case"

        db = SessionLocal()
        try:
            db.query(Case).filter(Case.id == "pdf-case").delete()
            db.commit()
        finally:
            db.close()

    @pytest.fixture
    def client(self, case_id):
        from fastapi.testclient import TestClient
        from main import app
        with TestClient(app) as c:
            yield c

    @pytest.fixture
    def moot_case(self, case_id):
        """在空案件上种一份庭审记录，供「有庭审」分支的用例复用"""
        from core.case_context import CaseContext
        from core.database import SessionLocal
        from routers.report import _load_case
        db = SessionLocal()
        try:
            case = _load_case(db, case_id)
            ctx = CaseContext.from_dict({})
            ctx.case_id = case_id
            ctx.cause_type, ctx.goal_type = "商标侵权", "要钱"
            ctx.moot_transcript = [
                {"step": 1, "step_name": "原告陈述", "role": "plaintiff",
                 "role_name": "原告代理人", "content": "我方商标合法注册且在有效期内。"},
            ]
            ctx.correction_coeff = 0.9
            case.context_json = ctx.to_dict()
            db.commit()
        finally:
            db.close()
        yield case_id

    def test_transcript_export_requires_a_moot(self, client, case_id):
        """没跑过庭审就导出，应当是明确 404 而不是一份空 PDF"""
        assert client.get(f"/api/report/{case_id}/transcript.pdf").status_code == 404

    def test_transcript_export_when_moot_exists(self, client, moot_case):
        resp = client.get(f"/api/report/{moot_case}/transcript.pdf")
        assert resp.status_code == 200
        assert resp.content[:4] == self.PDF_MAGIC
        assert resp.headers["content-type"] == PDF_MIME
        text = _text(resp.content)
        assert "原告陈述" in text and "0.9" in text

    def test_transcript_header_carries_utf8_filename(self, client, moot_case):
        header = client.get(f"/api/report/{moot_case}/transcript.pdf").headers["content-disposition"]
        assert "filename*=UTF-8''" in header
