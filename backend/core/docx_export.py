"""
Markdown → Word（.docx）

为什么从 markdown 转，而不是拿结构化数据重新排版一遍：

markdown 是评估结果的**唯一内容源**——Word 与 PDF 都从它转，北大法宝引用核验的输入
也是它。再按结构化数据排一版，等于维护两套渲染逻辑，改了报告结构就得同步改两处，
迟早出现「界面上看得到的内容 Word 里没有」。

只支持评估结果实际用到的几种语法（标题 / 列表 / 引用 / 加粗 / 行内代码 / 分隔线）。
不做通用 markdown 兼容：通用解析器会变成依赖负担，而我们根本用不到表格和图片。
"""
import io
import re
from typing import List, Optional

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt

DOCX_MIME = ("application/vnd.openxmlformats-officedocument"
             ".wordprocessingml.document")

_HEADING_SIZES = {1: 18, 2: 14.5, 3: 12.5, 4: 11}
_EAST_ASIA_FONT = "宋体"
_LATIN_FONT = "Times New Roman"
_BODY_SIZE = 10.5


def _style(run, size: float = _BODY_SIZE, bold: bool = False) -> None:
    """
    中文字体必须显式设 eastAsia，只设 font.name 的话 Word 里中文会掉回默认字体，
    而西文却是设好的——中英混排的备忘录会显示成两种字号的拼接，非常明显。

    字号用 10.5pt（五号）：这是中文正式文档的通例，比 Word 默认的 11pt 更紧凑。
    """
    run.font.size = Pt(size)
    run.bold = bold
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.get_or_add_rFonts()
    rfonts.set(qn("w:eastAsia"), _EAST_ASIA_FONT)
    rfonts.set(qn("w:ascii"), _LATIN_FONT)
    rfonts.set(qn("w:hAnsi"), _LATIN_FONT)


def _add_runs(paragraph, text: str, bold: bool = False) -> None:
    """按 **加粗** 与 `行内代码` 切分后逐段写入，保留其余字符原样"""
    for token in re.split(r"(\*\*.+?\*\*|`[^`]+`)", text):
        if not token:
            continue
        if token.startswith("**") and token.endswith("**") and len(token) > 4:
            run = paragraph.add_run(token[2:-2])
            _style(run, bold=True)
        elif token.startswith("`") and token.endswith("`") and len(token) > 2:
            run = paragraph.add_run(token[1:-1])
            _style(run)
            run.font.name = "Consolas"
        else:
            run = paragraph.add_run(token)
            _style(run, bold=bold)


def _heading(doc: Document, text: str, level: int) -> None:
    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(14 if level == 1 else 8)
    paragraph.paragraph_format.space_after = Pt(6)
    if level == 1:
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _add_runs(paragraph, text, bold=True)
    for run in paragraph.runs:
        run.font.size = Pt(_HEADING_SIZES.get(level, 11))


def _quote(doc: Document, text: str) -> None:
    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.left_indent = Pt(18)
    paragraph.paragraph_format.space_after = Pt(4)
    _add_runs(paragraph, text)
    for run in paragraph.runs:
        run.italic = True
        run.font.color.rgb = None          # 用默认色，避免浅灰在打印时看不清


def _bullet(doc: Document, text: str, indent_level: int = 0) -> None:
    paragraph = doc.add_paragraph(style="List Bullet")
    paragraph.paragraph_format.left_indent = Pt(18 + 18 * indent_level)
    paragraph.paragraph_format.space_after = Pt(2)
    _add_runs(paragraph, text)


def _rule(doc: Document) -> None:
    """分隔线：给空段落加下边框，比塞一串破折号干净"""
    paragraph = doc.add_paragraph()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "CCCCCC")
    borders.append(bottom)
    paragraph._p.get_or_add_pPr().append(borders)


def markdown_to_docx(markdown: str, *, title: Optional[str] = None,
                     meta: Optional[List[str]] = None) -> Document:
    """
    渲染为 python-docx 的 Document 对象。

    title 单独传而不从 markdown 的 H1 里取：导出时标题往往是「案件名 + 文档类型」，
    与正文里的 H1 并不一致（例：正文 H1 是「主诉评估结果：某某案」，
    而导出的文件名只需要后半段）。
    """
    doc = Document()

    # 页面边距：中文公文惯用上下 2.54cm、左右 3.17cm，Word 默认太窄，
    # 打印出来会觉得挤。
    for section in doc.sections:
        section.top_margin = section.bottom_margin = Pt(72)
        section.left_margin = section.right_margin = Pt(90)

    if title:
        _heading(doc, title, 1)

    for line in (meta or []):
        paragraph = doc.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _add_runs(paragraph, line)

    for raw in (markdown or "").splitlines():
        line = raw.rstrip()
        stripped = line.strip()

        if not stripped:
            continue

        # 分隔线要在标题判定之前：--- 也可能被当成 setext 标题，这里不需要
        if re.fullmatch(r"-{3,}|\*{3,}", stripped):
            _rule(doc)
            continue

        heading = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if heading:
            _heading(doc, heading.group(2).strip(), len(heading.group(1)))
            continue

        if stripped.startswith(">"):
            _quote(doc, stripped.lstrip(">").strip())
            continue

        bullet = re.match(r"^(\s*)[-*+]\s+(.*)$", line)
        if bullet:
            _bullet(doc, bullet.group(2).strip(), indent_level=len(bullet.group(1)) // 2)
            continue

        numbered = re.match(r"^\s*(\d+)[.、]\s+(.*)$", line)
        if numbered:
            _bullet(doc, f"{numbered.group(1)}. {numbered.group(2).strip()}")
            continue

        paragraph = doc.add_paragraph()
        paragraph.paragraph_format.space_after = Pt(4)
        _add_runs(paragraph, stripped)

    return doc


def markdown_to_docx_bytes(markdown: str, *, title: Optional[str] = None,
                           meta: Optional[List[str]] = None) -> bytes:
    buffer = io.BytesIO()
    markdown_to_docx(markdown, title=title, meta=meta).save(buffer)
    return buffer.getvalue()
