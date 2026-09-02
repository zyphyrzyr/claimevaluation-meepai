"""
Markdown → PDF

与 docx_export.py 同哲学：**markdown 是庭审记录的唯一内容源**，这里只是把同一份
markdown 再渲染成 PDF，而不是拿结构化数据重新排版一遍——否则维护两套渲染逻辑，
改了报告结构就得同步改两处。只支持实际用到的语法子集（标题 / 列表 / 引用 /
加粗 / 行内代码 / 分隔线），与 docx 渲染保持一致。

PDF 中文方案：用 reportlab 内置 CID 字体 STSong-Light（Adobe 筒化字，无需随包附带
.ttf 文件即可正确显示中文）。纯 Python、无系统原生依赖，比 weasyprint 更省心。
CJK 必须开 wordWrap='CJK'，否则长中文行不会在页边自动断行。
"""
import io
import re
from typing import List, Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate

PDF_MIME = "application/pdf"

# 中文 CID 字体：STSong-Light 随 reportlab 自带，免外部字体文件。
try:
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    _FONT = "STSong-Light"
except Exception:  # 极端情况下回退（中文会显示为方块，仅保底不崩）
    _FONT = "Helvetica"

_BODY_SIZE = 10.5
_HEADING_SIZES = {1: 18, 2: 14.5, 3: 12.5, 4: 11}


def _styles():
    """与 docx_export 同字号体系：正文 10.5pt（五号），标题逐级递减。"""
    base = ParagraphStyle(
        "body", fontName=_FONT, fontSize=_BODY_SIZE, leading=16,
        spaceAfter=4, wordWrap="CJK",
    )
    return {
        "h1": ParagraphStyle("h1", parent=base, fontSize=_HEADING_SIZES[1],
                             leading=24, alignment=TA_CENTER, spaceBefore=14, spaceAfter=6),
        "h2": ParagraphStyle("h2", parent=base, fontSize=_HEADING_SIZES[2],
                             leading=20, spaceBefore=10, spaceAfter=4),
        "h3": ParagraphStyle("h3", parent=base, fontSize=_HEADING_SIZES[3],
                             leading=18, spaceBefore=8, spaceAfter=3),
        "h4": ParagraphStyle("h4", parent=base, fontSize=_HEADING_SIZES[4],
                             leading=16, spaceBefore=6, spaceAfter=3),
        "meta": ParagraphStyle("meta", parent=base, fontSize=9, leading=13,
                               alignment=TA_CENTER, textColor=colors.grey),
        "quote": ParagraphStyle("quote", parent=base, leftIndent=18, spaceAfter=4),
        "body": base,
    }


def _rich(text: str) -> str:
    """
    转义 XML 特殊字符后，再套 **加粗** / `行内代码` 标签。

    顺序不能反：先 escape 再插标签，插入的 <b>/<font> 才不会被二次转义成 &lt;b&gt;。
    STSong-Light 无独立粗体字形，<b> 在 CID 字体下不显示加粗——标题已用更大字号
    区分层级，正文加粗仅作语义标记，视觉权重差异有限（与 docx 的宋体加粗不同，属
    已知取舍）。
    """
    esc = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    esc = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc)
    esc = re.sub(r"`([^`]+)`", r'<font name="Courier">\1</font>', esc)
    return esc


def _bullet(text: str, indent_level: int = 0) -> Paragraph:
    """项目符号：用 • 前缀 + 缩进，避免 ListFlowable 的嵌套边角问题。"""
    style = ParagraphStyle(
        "bullet", parent=_styles()["body"], leftIndent=18 + 18 * indent_level,
    )
    return Paragraph("• " + _rich(text), style)


def markdown_to_pdf(markdown: str, *, title: Optional[str] = None,
                    meta: Optional[List[str]] = None):
    """
    渲染为 reportlab 的 flowables 列表（供 SimpleDocTemplate.build）。
    结构顺序与 docx_export.markdown_to_docx 对齐：title → meta 行 → 正文。
    """
    styles = _styles()
    flow = []
    if title:
        flow.append(Paragraph(_rich(title), styles["h1"]))
    for line in (meta or []):
        flow.append(Paragraph(_rich(line), styles["meta"]))

    for raw in (markdown or "").splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue

        # 分隔线优先于标题判定（--- 也可能被当 setext 标题，这里不需要）
        if re.fullmatch(r"-{3,}|\*{3,}", stripped):
            flow.append(HRFlowable(width="100%", thickness=0.5,
                                   color=colors.grey, spaceBefore=6, spaceAfter=6))
            continue

        heading = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if heading:
            lvl = len(heading.group(1))
            flow.append(Paragraph(_rich(heading.group(2).strip()), styles[f"h{lvl}"]))
            continue

        if stripped.startswith(">"):
            flow.append(Paragraph(_rich(stripped.lstrip(">").strip()), styles["quote"]))
            continue

        bullet = re.match(r"^(\s*)[-*+]\s+(.*)$", line)
        if bullet:
            flow.append(_bullet(bullet.group(2).strip(),
                                indent_level=len(bullet.group(1)) // 2))
            continue

        numbered = re.match(r"^\s*(\d+)[.、]\s+(.*)$", line)
        if numbered:
            flow.append(Paragraph(f"{numbered.group(1)}. " + _rich(numbered.group(2).strip()),
                                  styles["body"]))
            continue

        flow.append(Paragraph(_rich(stripped), styles["body"]))

    return flow


def markdown_to_pdf_bytes(markdown: str, *, title: Optional[str] = None,
                          meta: Optional[List[str]] = None) -> bytes:
    """同 markdown_to_pdf，但直接返回 PDF 字节流。"""
    buffer = io.BytesIO()
    # 页边距与 docx 对齐：上下 2.54cm、左右 3.17cm（公文惯用，打印不显挤）
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        topMargin=2.54 * cm, bottomMargin=2.54 * cm,
        leftMargin=3.17 * cm, rightMargin=3.17 * cm,
    )
    doc.build(markdown_to_pdf(markdown, title=title, meta=meta))
    return buffer.getvalue()
