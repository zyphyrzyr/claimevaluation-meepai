"""
通用文本提取器
- txt / md：UTF-8 解码
- docx：python-docx
- pdf：pymupdf
用于「案情描述文件导入」以及 ZIP 包内文本类文件提取。
"""

import io
from typing import Dict


# ── 可选依赖（不阻塞启动）──

try:
    import docx
    _DOCX_OK = True
except Exception:
    _DOCX_OK = False

try:
    import fitz
    _FITZ_OK = True
except Exception:
    _FITZ_OK = False


# ── 扩展名判断 ──

def is_text_file(filename: str) -> bool:
    name = filename.lower()
    return name.endswith('.txt') or name.endswith('.md') or name.endswith('.markdown')


def is_docx_file(filename: str) -> bool:
    return filename.lower().endswith('.docx')


def is_supported_description_file(filename: str) -> bool:
    name = filename.lower()
    return (
        name.endswith('.txt') or
        name.endswith('.md') or
        name.endswith('.docx') or
        name.endswith('.pdf')
    )


# ── 文本提取 ──

def _extract_txt(text_bytes: bytes, filename: str) -> Dict:
    for encoding in ('utf-8', 'gbk', 'gb2312', 'utf-16'):
        try:
            text = text_bytes.decode(encoding)
            return {
                "success": True,
                "text": text.strip(),
                "filename": filename,
                "error": "",
                "page_count": 0,
            }
        except (UnicodeDecodeError, LookupError):
            continue
    return {
        "success": False,
        "text": "",
        "filename": filename,
        "error": "文本文件编码无法识别，请使用 UTF-8 编码后重新上传",
        "page_count": 0,
    }


def _extract_docx(docx_bytes: bytes, filename: str) -> Dict:
    if not _DOCX_OK:
        return {
            "success": False,
            "text": "",
            "filename": filename,
            "error": "python-docx 未安装，无法解析 .docx 文件",
            "page_count": 0,
        }
    try:
        doc = docx.Document(io.BytesIO(docx_bytes))
        paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        text = "\n\n".join(paragraphs)
        return {
            "success": True,
            "text": text,
            "filename": filename,
            "error": "",
            "page_count": 0,
        }
    except Exception as e:
        return {
            "success": False,
            "text": "",
            "filename": filename,
            "error": f"docx 解析失败: {e}",
            "page_count": 0,
        }


def _extract_pdf(pdf_bytes: bytes, filename: str) -> Dict:
    if not _FITZ_OK:
        return {
            "success": False,
            "text": "",
            "filename": filename,
            "error": "pymupdf 未安装，无法解析 PDF 文件",
            "page_count": 0,
        }
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        full_text = []
        for page in doc:
            text = page.get_text()
            if text.strip():
                full_text.append(f"--- 第 {page.number + 1} 页 ---\n{text.strip()}")
        return {
            "success": True,
            "text": "\n\n".join(full_text),
            "filename": filename,
            "error": "",
            "page_count": len(doc),
        }
    except Exception as e:
        return {
            "success": False,
            "text": "",
            "filename": filename,
            "error": f"PDF 解析失败: {e}",
            "page_count": 0,
        }


def extract_text_from_file(file_bytes: bytes, filename: str) -> Dict:
    """提取任意支持的文本类文件内容。返回 {success, text, filename, error, page_count}。"""
    if is_text_file(filename):
        return _extract_txt(file_bytes, filename)
    if is_docx_file(filename):
        return _extract_docx(file_bytes, filename)
    # PDF 也复用此入口，保持统一
    if filename.lower().endswith('.pdf'):
        return _extract_pdf(file_bytes, filename)
    return {
        "success": False,
        "text": "",
        "filename": filename,
        "error": "不支持的文件格式，案情描述导入仅支持 .txt / .md / .docx / .pdf",
        "page_count": 0,
    }
