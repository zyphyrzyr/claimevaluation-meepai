import io
import zipfile

import fitz
import pytest

from core.evidence_parser import is_zip_file, parse_zip_archive


def _make_zip(files: dict, filename: str = "evidence.zip") -> bytes:
    """files: {name_in_zip: bytes}"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_is_zip_file():
    assert is_zip_file("a.zip")
    assert is_zip_file("a.ZIP")
    assert not is_zip_file("a.pdf")


def test_parse_zip_with_text_and_pdf():
    pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "PDF 内部文本", fontname="china-ss", fontsize=12)
    pdf_buf = io.BytesIO()
    doc.save(pdf_buf)
    doc.close()

    zip_bytes = _make_zip({
        "readme.txt": "这是 readme 文本".encode("utf-8"),
        "notes.md": "# 证据说明\n图片 10 张".encode("utf-8"),
        "inner/inner.pdf": pdf_buf.getvalue(),
    })
    result = parse_zip_archive(zip_bytes, "evidence.zip")
    assert result["success"] is True
    assert result["total"] == 3
    assert result["ok_count"] == 3
    assert result["failed"] == 0
    assert result["skipped"] == 0

    texts = [r["text"] for r in result["records"]]
    assert any("readme 文本" in t for t in texts)
    assert any("证据说明" in t for t in texts)
    assert any("PDF 内部文本" in t for t in texts)


def test_parse_zip_skips_unsupported_and_system_files():
    zip_bytes = _make_zip({
        "valid.txt": b"valid",
        "unsupported.exe": b"binary",
        "__MACOSX/.DS_Store": b"system",
        ".hidden": b"hidden",
    })
    result = parse_zip_archive(zip_bytes, "evidence.zip")
    assert result["success"] is True
    # valid.txt 解析，其余跳过
    assert result["total"] == 1
    assert result["skipped"] == 3
    assert result["ok_count"] == 1


def test_parse_zip_rejects_path_traversal():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("../../etc/passwd", b"evil")
    result = parse_zip_archive(buf.getvalue(), "evil.zip")
    assert result["success"] is True
    assert result["total"] == 0
    assert result["skipped"] == 1


def test_parse_zip_rejects_too_many_files():
    files = {f"file_{i}.txt": b"x" for i in range(60)}
    zip_bytes = _make_zip(files)
    result = parse_zip_archive(zip_bytes, "big.zip")
    assert result["success"] is True
    # 达到上限 50 后停止
    assert result["total"] <= 50


def test_parse_zip_bomb_by_ratio():
    # 一个看似很小但解压后极大的文本：会被压缩比拦截
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # 高度可压缩内容
        zf.writestr("bomb.txt", "x" * 10_000_000)
    result = parse_zip_archive(buf.getvalue(), "bomb.zip")
    # 可能整体成功但 bomb.txt 被跳过
    assert result["success"] is True
    assert any("zip bomb" in w.lower() for w in result["warnings"]) or result["skipped"] > 0
