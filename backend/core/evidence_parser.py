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
