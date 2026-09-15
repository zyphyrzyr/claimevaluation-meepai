"""
证据文件解析器
- PDF：pymupdf 文本提取
- 图片：PaddleOCR / Tesseract / RapidOCR（多引擎自动切换）
"""

import io
from typing import Dict

# ── 可选依赖（不阻塞启动）──

try:
    import numpy as np
    _NUMPY_OK = True
except Exception:
    _NUMPY_OK = False

try:
    import fitz  # pymupdf
    _FITZ_OK = True
except Exception:
    _FITZ_OK = False

try:
    from PIL import Image
    _PIL_OK = True
except Exception:
    _PIL_OK = False

# ── 检测可用的 OCR 引擎（懒加载，不阻塞启动）──

_TESSERACT_OK = False
_PADDLEOCR_OK = False
_RAPIDOCR_OK = False
_paddle_engine = None
_rapid_engine = None

# Tesseract
try:
    import pytesseract
    _TESSERACT_OK = True
except ImportError:
    pass

# PaddleOCR（首次 OCR 时 lazy init）

def _init_paddleocr():
    global _paddle_engine, _PADDLEOCR_OK
    if _PADDLEOCR_OK or _paddle_engine:
        return _PADDLEOCR_OK
    try:
        from paddleocr import PaddleOCR
        _paddle_engine = PaddleOCR(lang='ch', use_textline_orientation=True)
        _PADDLEOCR_OK = True
    except Exception:
        _PADDLEOCR_OK = False
    return _PADDLEOCR_OK

# RapidOCR（备用）

def _init_rapidocr():
    global _rapid_engine, _RAPIDOCR_OK
    if _RAPIDOCR_OK or _rapid_engine:
        return _RAPIDOCR_OK
    try:
        from rapidocr_onnxruntime import RapidOCR
        _rapid_engine = RapidOCR()
        _RAPIDOCR_OK = True
    except Exception:
        _RAPIDOCR_OK = False
    return _RAPIDOCR_OK


# ── PDF 解析 ──

def parse_pdf(file_bytes: bytes, filename: str) -> Dict:
    """解析 PDF 文件，提取全部文本"""
    if not _FITZ_OK:
        return {
            "success": False, "text": "", "page_count": 0, "filename": filename,
            "error": "pymupdf 未安装。请运行: pip install pymupdf"
        }

    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        full_text = []
        for page in doc:
            text = page.get_text()
            if text.strip():
                full_text.append(f"--- 第 {page.number + 1} 页 ---\n{text.strip()}")

        return {
            "success": True, "text": "\n\n".join(full_text),
            "page_count": len(doc), "filename": filename, "error": ""
        }
    except Exception as e:
        return {"success": False, "text": "", "page_count": 0, "filename": filename, "error": str(e)}


# ── 图片 OCR（多引擎自动切换）──

def ocr_image(image_bytes: bytes, filename: str) -> Dict:
    """识别图片中的文字，自动选择可用引擎"""
    if not _PIL_OK:
        return {
            "success": False, "text": "", "filename": filename,
            "error": "Pillow 未安装。请运行: pip install Pillow"
        }

    image = Image.open(io.BytesIO(image_bytes))

    # 引擎 1：PaddleOCR（lazy init）
    if _init_paddleocr():
        try:
            if not _NUMPY_OK:
                raise RuntimeError("numpy 未安装")
            img_array = np.array(image.convert("RGB"))
            result = _paddle_engine.ocr(img_array)

            texts = []
            if result and result[0]:
                for line in result[0]:
                    if line and len(line) >= 2:
                        texts.append(line[1][0])

            if texts:
                return {"success": True, "text": "\n".join(texts), "filename": filename, "error": "", "engine": "PaddleOCR"}
            else:
                return {"success": False, "text": "", "filename": filename,
                        "error": "PaddleOCR 未识别到文字", "engine": "PaddleOCR"}
        except Exception:
            pass

    # 引擎 2：Tesseract
    if _TESSERACT_OK:
        try:
            text = pytesseract.image_to_string(image, lang="chi_sim+eng")
            if text.strip():
                return {"success": True, "text": text.strip(), "filename": filename, "error": "", "engine": "Tesseract"}
        except Exception:
            pass

    # 引擎 3：RapidOCR（lazy init）
    if _init_rapidocr():
        try:
            if not _NUMPY_OK:
                raise RuntimeError("numpy 未安装")
            img_array = np.array(image.convert("RGB"))
            result, _ = _rapid_engine(img_array)

            texts = []
            if result:
                for line in result:
                    if line and len(line) >= 2:
                        texts.append(line[1])

            if texts:
                return {"success": True, "text": "\n".join(texts), "filename": filename, "error": "", "engine": "RapidOCR"}
        except Exception:
            pass

    # 所有引擎都不可用
    return {
        "success": False, "text": "", "filename": filename,
        "error": (
            "未找到可用的 OCR 引擎。请安装以下任一：\n\n"
            "方式1（推荐）: pip install paddlepaddle paddleocr\n"
            "方式2（备选）: pip install rapidocr-onnxruntime\n"
            "方式3（系统）: brew install tesseract"
        )
    }


def is_image_file(filename: str) -> bool:
    ext = filename.lower()
    return ext.endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.tiff'))


def is_pdf_file(filename: str) -> bool:
    return filename.lower().endswith('.pdf')


def is_zip_file(filename: str) -> bool:
    return filename.lower().endswith('.zip')


# ── ZIP 批量解压解析 ──

MAX_ZIP_FILES = 50          # 压缩包内文件数上限
MAX_ZIP_UNCOMPRESSED = 100 * 1024 * 1024   # 解压后总体积 100MB
MAX_ZIP_COMPRESS_RATIO = 100              # 压缩比上限 100:1


def _safe_zip_name(name: str) -> str:
    """校验并清理 zip 内文件名，防止路径穿越与非法字符。"""
    import os
    name = name.replace('\\', '/')
    parts = [p for p in name.split('/') if p and p not in ('.', '..')]
    if not parts:
        return ''
    # 再次拒绝任何带 .. 的原始路径
    if '..' in name.split('/'):
        return ''
    # 忽略 macOS 资源文件与系统文件
    base = parts[-1]
    if base.startswith('.') or base == 'Thumbs.db' or base.startswith('__MACOSX'):
        return ''
    if any(ord(c) < 32 or c in ('<', '>', ':', '"', '|', '?', '*') for c in base):
        return ''
    return '/'.join(parts)


def _is_supported_inner(filename: str) -> bool:
    name = filename.lower()
    return (
        name.endswith('.pdf') or
        name.endswith('.png') or
        name.endswith('.jpg') or
        name.endswith('.jpeg') or
        name.endswith('.txt') or
        name.endswith('.md') or
        name.endswith('.docx')
    )


def parse_zip_archive(zip_bytes: bytes, filename: str) -> Dict:
    """解析 ZIP 压缩包，返回 {success, records: list, warnings: list, total, ok_count, failed, skipped}。

    records 结构与 _parse_evidence 一致，每个元素对应压缩包内一个被解析的文件。
    文本类文件（txt/md/docx）只提取文本；PDF/图片走原有解析器并生成 EvidenceFile 记录。
    """
    import zipfile
    from core.text_extractor import extract_text_from_file

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except Exception as e:
        return {
            "success": False,
            "records": [],
            "warnings": [f"无法打开压缩包: {e}"],
            "total": 0,
            "ok_count": 0,
            "failed": 0,
            "skipped": 0,
        }

    records = []
    warnings = []
    total = 0
    ok_count = 0
    failed = 0
    skipped = 0
    uncompressed_total = 0

    for info in zf.infolist():
        if info.is_dir():
            continue

        safe_name = _safe_zip_name(info.filename)
        if not safe_name:
            skipped += 1
            warnings.append(f"跳过不安全或系统文件: {info.filename}")
            continue

        if not _is_supported_inner(safe_name):
            skipped += 1
            warnings.append(f"跳过不支持的文件: {safe_name}")
            continue

        if total >= MAX_ZIP_FILES:
            warnings.append(f"压缩包内文件数超过 {MAX_ZIP_FILES}，后续文件被忽略")
            break
        total += 1

        # zip bomb 初筛：压缩比
        ratio = info.file_size / max(info.compress_size, 1)
        if ratio > MAX_ZIP_COMPRESS_RATIO:
            warnings.append(f"跳过疑似 zip bomb 的文件（压缩比过高）: {safe_name}")
            skipped += 1
            continue

        try:
            raw = zf.read(info)
        except Exception as e:
            warnings.append(f"读取失败 {safe_name}: {e}")
            failed += 1
            continue

        uncompressed_total += len(raw)
        if uncompressed_total > MAX_ZIP_UNCOMPRESSED:
            warnings.append(f"解压后总体积超过 {MAX_ZIP_UNCOMPRESSED // 1024 // 1024}MB，后续文件被忽略")
            break

        if is_text_file(safe_name) or safe_name.lower().endswith('.docx'):
            # 文本类文件：只提取文本，不生成 EvidenceFile 记录（content_type 为空标识）
            res = extract_text_from_file(raw, safe_name)
            records.append({
                "filename": safe_name,
                "content_type": "",
                "text": res.get("text", "") if res.get("success") else "",
                "status": "ok" if res.get("success") else "failed",
                "source_zip": filename,
                "error": res.get("error", ""),
            })
            if res.get("success"):
                ok_count += 1
            else:
                failed += 1
                warnings.append(f"{safe_name} 文本提取失败: {res.get('error', '')}")
        else:
            # PDF / 图片：走既有解析器
            if is_pdf_file(safe_name):
                result = parse_pdf(raw, safe_name)
            elif is_image_file(safe_name):
                result = ocr_image(raw, safe_name)
            else:
                result = {"success": False, "text": "", "error": "未知格式"}

            records.append({
                "filename": safe_name,
                "content_type": "",
                "text": result.get("text", "") if result.get("success") else "",
                "status": "ok" if result.get("success") else "failed",
                "source_zip": filename,
                "error": result.get("error", ""),
            })
            if result.get("success"):
                ok_count += 1
            else:
                failed += 1
                warnings.append(f"{safe_name} 解析失败: {result.get('error', '')}")

    return {
        "success": True,
        "records": records,
        "warnings": warnings,
        "total": total,
        "ok_count": ok_count,
        "failed": failed,
        "skipped": skipped,
    }


def is_text_file(filename: str) -> bool:
    """文本类文件（用于 ZIP 内部分流）。"""
    name = filename.lower()
    return name.endswith('.txt') or name.endswith('.md') or name.endswith('.markdown')
