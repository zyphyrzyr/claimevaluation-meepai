import io

import fitz
import pytest

from core.text_extractor import extract_text_from_file, is_supported_description_file


def test_extract_txt_utf8():
    text = "原告 LV 商标侵权\n被告某某公司侵权"
    result = extract_text_from_file(text.encode("utf-8"), "case.txt")
    assert result["success"] is True
    assert "LV" in result["text"]


def test_extract_txt_gbk():
    text = "原告 LV 商标侵权"
    result = extract_text_from_file(text.encode("gbk"), "case_gbk.txt")
    assert result["success"] is True
    assert "LV" in result["text"]


def test_extract_md():
    text = "# 案情\n- 侵权店铺：10 家"
    result = extract_text_from_file(text.encode("utf-8"), "case.md")
    assert result["success"] is True
    assert "侵权店铺" in result["text"]


def test_extract_docx():
    pytest.importorskip("docx")
    from docx import Document

    doc = Document()
    doc.add_paragraph("原告为 LV 中国，被告为某奶茶店。")
    buf = io.BytesIO()
    doc.save(buf)
    result = extract_text_from_file(buf.getvalue(), "case.docx")
    assert result["success"] is True
    assert "奶茶店" in result["text"]


def test_extract_pdf():
    pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "商标侵权诉讼案情描述", fontname="china-ss", fontsize=12)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    result = extract_text_from_file(buf.getvalue(), "case.pdf")
    assert result["success"] is True
    assert "商标侵权" in result["text"]
    assert result["page_count"] == 1


def test_unsupported_extension():
    result = extract_text_from_file(b"x", "case.xls")
    assert result["success"] is False
    assert "不支持" in result["error"]


def test_is_supported_description_file():
    assert is_supported_description_file("a.txt")
    assert is_supported_description_file("a.md")
    assert is_supported_description_file("a.docx")
    assert is_supported_description_file("a.pdf")
    assert not is_supported_description_file("a.doc")
    assert not is_supported_description_file("a.png")
