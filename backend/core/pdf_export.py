"""
Markdown → PDF

与 docx_export.py 同哲学：**markdown 是评估结果的唯一内容源**，这里只是把同一份
markdown 再渲染成 PDF，而不是拿结构化数据重新排版一遍——否则维护两套渲染逻辑，
改了报告结构就得同步改两处。只支持实际用到的语法子集（标题 / 列表 / 引用 /
加粗 / 行内代码 / 分隔线），与 docx 渲染保持一致。

PDF 中文方案：用 reportlab 内置 CID 字体 STSong-Light（Adobe 简体字，无需随包附带
.ttf 文件即可正确显示中文）。纯 Python、无系统原生依赖，比 weasyprint 更省心。
CJK 必须开 wordWrap='CJK'，否则长中文行不会在页边自动断行。

排版规范（与 docx_export 对齐）：
- 主色深蓝 #1F4E79，正文近黑 #1A1A1A，辅助灰 #808080
- 语义色：阻断红 #C00000 / 警告琥珀 #BF8F00 / 通过绿 #2E7D32
- 标题层级靠「颜色 + 字号」区分（STSong-Light 无粗体字形，<b> 不显示加粗，
  故把 **加粗** 渲染成深蓝强调，保留视觉权重）
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

# 颜色规范（十六进制，与 docx_export 保持一致）
_PRIMARY = "#1F4E79"   # 深蓝藏青：标题、强调、表头
_TEXT = "#1A1A1A"      # 正文近黑
_MUTED = "#808080"     # 辅助灰：meta、页脚
_RED = "#C00000"       # 阻断
_AMBER = "#BF8F00"     # 警告
_GREEN = "#2E7D32"     # 通过

# 中文 CID 字体：STSong-Light 随 reportlab 自带，免外部字体文件。
try:
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    _FONT = "STSong-Light"
except Exception:  # 极端情况下回退（中文会显示为方块，仅保底不崩）
    _FONT = "Helvetica"

_BODY_SIZE = 10.5
_HEADING_SIZES = {1: 18, 2: 14.5, 3: 12.5, 4: 11}


def _styles():
    """与 docx_export 同字号体系：正文 10.5pt（五号），标题逐级递减。

    STSong-Light 无粗体，标题视觉权重靠「深蓝 + 大字号」承担；
    keepWithNext 保证标题不孤立在页尾。
    """
    base = ParagraphStyle(
        "body", fontName=_FONT, fontSize=_BODY_SIZE, leading=16,
        spaceAfter=4, wordWrap="CJK", textColor=colors.HexColor(_TEXT),
    )
    return {
        "h1": ParagraphStyle("h1", parent=base, fontSize=_HEADING_SIZES[1],
                             leading=24, alignment=TA_CENTER, spaceBefore=14, spaceAfter=8,
                             textColor=colors.HexColor(_PRIMARY), keepWithNext=True),
        "h2": ParagraphStyle("h2", parent=base, fontSize=_HEADING_SIZES[2],
                             leading=20, spaceBefore=12, spaceAfter=6,
                             textColor=colors.HexColor(_PRIMARY), keepWithNext=True),
        "h3": ParagraphStyle("h3", parent=base, fontSize=_HEADING_SIZES[3],
                             leading=18, spaceBefore=8, spaceAfter=4,
                             textColor=colors.HexColor(_PRIMARY), keepWithNext=True),
        "h4": ParagraphStyle("h4", parent=base, fontSize=_HEADING_SIZES[4],
                             leading=16, spaceBefore=6, spaceAfter=3,
                             textColor=colors.HexColor(_PRIMARY), keepWithNext=True),
        "meta": ParagraphStyle("meta", parent=base, fontSize=9, leading=13,
                               alignment=TA_CENTER, textColor=colors.HexColor(_MUTED)),
        "quote": ParagraphStyle("quote", parent=base, leftIndent=18, spaceAfter=4,
                                textColor=colors.HexColor(_MUTED)),
        "body": base,
    }


def _rich(text: str) -> str:
    """
    转义 XML 特殊字符后，再套 **加粗** / `行内代码` 标签。

    顺序不能反：先 escape 再插标签，插入的 <b>/<font> 才不会被二次转义成 &lt;b&gt;。
    STSong-Light 无独立粗体字形，<b> 不显示加粗——这里把 **加粗** 渲染成深蓝强调，
    视觉权重不丢，且与 docx 的宋体加粗在「强调」语义上保持一致。
    """
    esc = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    esc = re.sub(r"\*\*(.+?)\*\*", r'<font color="%s">\1</font>' % _PRIMARY, esc)
    esc = re.sub(r"`([^`]+)`", r'<font name="Courier">\1</font>', esc)
    return esc


def _bullet(text: str, indent_level: int = 0) -> Paragraph:
    """项目符号：用 • 前缀 + 缩进，避免 ListFlowable 的嵌套边角问题。"""
    style = ParagraphStyle(
        "bullet", parent=_styles()["body"], leftIndent=18 + 18 * indent_level,
    )
    return Paragraph("• " + _rich(text), style)


def _on_page(canvas, doc, header_text: str) -> None:
    """页眉页脚：页眉左侧品牌 + 右侧案件名，页脚左侧免责 + 右侧页码。"""
    canvas.saveState()
    width, height = A4

    # 页眉
    canvas.setFont(_FONT, 9)
    canvas.setFillColor(colors.HexColor(_MUTED))
    canvas.drawString(3.17 * cm, height - 1.6 * cm, "诉算 · 主诉评估结果")
    if header_text:
        canvas.drawRightString(width - 3.17 * cm, height - 1.6 * cm, header_text)
    canvas.setStrokeColor(colors.HexColor("#CCCCCC"))
    canvas.setLineWidth(0.5)
    canvas.line(3.17 * cm, height - 1.9 * cm, width - 3.17 * cm, height - 1.9 * cm)

    # 页脚
    canvas.setFont(_FONT, 8.5)
    canvas.setFillColor(colors.HexColor(_MUTED))
    canvas.drawString(3.17 * cm, 1.6 * cm, "AI 辅助生成，仅供内部决策参考")
    canvas.drawRightString(width - 3.17 * cm, 1.6 * cm,
                           f"第 {doc.page} 页")

    canvas.restoreState()


def markdown_to_pdf(markdown: str, *, title: Optional[str] = None,
                    meta: Optional[List[str]] = None):
    """
    渲染为 reportlab 的 flowables 列表（供 SimpleDocTemplate.build）。
    结构顺序与 docx_export.markdown_to_docx 对齐：title → meta 行 → 正文。
    """
    styles = _styles()
    flow = []
    meta_lines = list(meta or [])

    if title:
        # 显式传了 title：维持「标题 → meta → 正文」的旧顺序
        flow.append(Paragraph(_rich(title), styles["h1"]))
        for line in meta_lines:
            flow.append(Paragraph(_rich(line), styles["meta"]))
        meta_flushed = True
    else:
        # 未传 title（结果/庭审导出的实际情况）：meta 延迟到正文第一个 H1 之后渲染。
        # 否则「生成日期」会压在报告主标题上面，层级倒挂。
        meta_flushed = False

    def _flush_meta() -> None:
        nonlocal meta_flushed
        if meta_flushed:
            return
        for line in meta_lines:
            flow.append(Paragraph(_rich(line), styles["meta"]))
        meta_flushed = True

    for raw in (markdown or "").splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue

        # 分隔线优先于标题判定（--- 也可能被当 setext 标题，这里不需要）
        if re.fullmatch(r"-{3,}|\*{3,}", stripped):
            flow.append(HRFlowable(width="100%", thickness=0.5,
                                   color=colors.HexColor("#CCCCCC"),
                                   spaceBefore=6, spaceAfter=6))
            continue

        heading = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if heading:
            lvl = len(heading.group(1))
            flow.append(Paragraph(_rich(heading.group(2).strip()), styles[f"h{lvl}"]))
            if lvl == 1:
                _flush_meta()
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

    _flush_meta()  # 正文没有 H1 时兜底：meta 仍要出现
    return flow


def markdown_to_pdf_bytes(markdown: str, *, title: Optional[str] = None,
                          meta: Optional[List[str]] = None,
                          header_text: Optional[str] = None) -> bytes:
    """同 markdown_to_pdf，但直接返回 PDF 字节流。

    header_text 用于页眉右侧（通常传案件名）。
    """
    buffer = io.BytesIO()
    # 页边距与 docx 对齐：上下 2.54cm、左右 3.17cm（公文惯用，打印不显挤）
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        topMargin=2.54 * cm, bottomMargin=2.54 * cm,
        leftMargin=3.17 * cm, rightMargin=3.17 * cm,
        title=title or "主诉评估结果",
        author="Soft IP 主诉评估系统",
    )
    doc.build(
        markdown_to_pdf(markdown, title=title, meta=meta),
        onFirstPage=lambda c, d: _on_page(c, d, header_text or ""),
        onLaterPages=lambda c, d: _on_page(c, d, header_text or ""),
    )
    return buffer.getvalue()
