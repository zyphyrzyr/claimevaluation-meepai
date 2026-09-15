"""
证据原件存取层：把上传的原始文件落盘，并提供预览所需的派生信息。

为什么需要它：原先 _write_evidence_files 只存解析文本、storage_uri 硬编码为空，
已入库文件在服务器上没有字节，无法"预览原件"。本模块补齐落盘 + 取回 + office 转 HTML。

安全要点：
- 落盘文件名一律用 file_id（generate_id 生成的纯字母数字串），**不用用户原始文件名**，
  从根本上杜绝路径穿越 / 覆盖系统文件。storage_uri 只存相对路径（{case_id}/{file_id}{ext}）。
- 取回时做路径包含断言：解析后的绝对路径必须仍在 UPLOAD_ROOT 之内，否则视为非法。
- textutil 是 macOS 专属；Linux 部署时 TEXTUTIL 为 None，doc/docx 预览降级为「可下载原件、不可在线预览」，
  且 .doc 文本提取同样不可用（python-docx 不支持 .doc），调用方会据此回退到纯文本。
"""
from __future__ import annotations

import mimetypes
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # backend/ -> project root
UPLOAD_ROOT = PROJECT_ROOT / "data" / "runtime" / "uploads"

# macOS 自带文本工具；缺失（如 Linux 部署）则 office 预览能力关闭
TEXTUTIL = shutil.which("textutil")

# 允许落盘的原件类型；其余（zip/txt/md…）不建原件记录
STORE_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".doc", ".docx"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg"}
OFFICE_EXTS = {".doc", ".docx"}


def _safe_ext(filename: str) -> str:
    """从原始文件名取扩展名，仅允许白名单；否则落到 .bin（preview_kind 自然为 none）。"""
    ext = Path(filename).suffix.lower()
    return ext if ext in STORE_EXTS else ".bin"


def _ensure_root() -> None:
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)


def save_original(case_id: str, file_id: str, filename: str, content: bytes) -> str:
    """把原件字节写到 {case_id}/{file_id}{ext}，返回相对 storage_uri。"""
    _ensure_root()
    case_dir = UPLOAD_ROOT / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    ext = _safe_ext(filename)
    storage_uri = f"{case_id}/{file_id}{ext}"
    abs_path = (UPLOAD_ROOT / storage_uri)
    abs_path.write_bytes(content)
    return storage_uri


def original_path(storage_uri: str) -> Optional[Path]:
    """把相对 storage_uri 解析成绝对路径，做路径穿越防御；不存在返回 None。"""
    if not storage_uri:
        return None
    root = UPLOAD_ROOT.resolve()
    abs_path = (root / storage_uri).resolve()
    # 最终路径必须仍在 root 内（或正好是 root 本身）
    if abs_path != root and root not in abs_path.parents:
        return None
    if not abs_path.exists():
        return None
    return abs_path


def has_blob(storage_uri: str) -> bool:
    return original_path(storage_uri) is not None


def size(storage_uri: str) -> Optional[int]:
    p = original_path(storage_uri)
    if p is None:
        return None
    try:
        return p.stat().st_size
    except OSError:
        return None


def preview_kind(storage_uri: str, filename: str) -> str:
    """pdf → pdf；图片 → image；office（有 textutil）→ html；其余 → none。"""
    p = original_path(storage_uri)
    if p is None:
        return "none"
    ext = p.suffix.lower()
    if ext in {".pdf"}:
        return "pdf"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in OFFICE_EXTS:
        return "html" if TEXTUTIL else "none"
    return "none"


def doc_bytes_to_text(content: bytes, filename: str) -> Optional[str]:
    """把 .doc 字节先用 textutil 转成 txt，再读文本；无 textutil 或转换失败返回 None。

    仅用于解析文本回填 parsed_text；不负责预览（预览走 doc_to_html）。
    """
    if TEXTUTIL is None or not filename.lower().endswith(".doc"):
        return None
    _ensure_root()
    tmp = UPLOAD_ROOT / f"_tmp_{uuid.uuid4().hex}.doc"
    out = tmp.with_suffix(".txt")
    try:
        tmp.write_bytes(content)
        subprocess.run(
            [TEXTUTIL, "-convert", "txt", str(tmp), "-output", str(out)],
            check=True, capture_output=True, timeout=30,
        )
        return out.read_text(encoding="utf-8", errors="replace") if out.exists() else None
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        tmp.unlink(missing_ok=True)
        if out.exists():
            out.unlink(missing_ok=True)


def doc_to_html(storage_uri: str) -> Optional[str]:
    """把已落盘的 .doc/.docx 转成 HTML（带缓存），供沙箱 iframe 渲染；不可用返回 None。"""
    p = original_path(storage_uri)
    if p is None or TEXTUTIL is None or p.suffix.lower() not in OFFICE_EXTS:
        return None
    cache = p.with_suffix(".preview.html")
    try:
        if not cache.exists():
            subprocess.run(
                [TEXTUTIL, "-convert", "html", str(p), "-output", str(cache)],
                check=True, capture_output=True, timeout=30,
            )
        return cache.read_text(encoding="utf-8", errors="replace") if cache.exists() else None
    except (OSError, subprocess.SubprocessError):
        return None


def raw_mime(storage_uri: str) -> str:
    p = original_path(storage_uri)
    if p is None:
        return "application/octet-stream"
    return mimetypes.guess_type(str(p))[0] or "application/octet-stream"
