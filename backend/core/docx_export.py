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
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

DOCX_MIME = ("application/vnd.openxmlformats-officedocument"
             ".wordprocessingml.document")

_HEADING_SIZES = {1: 18, 2: 14.5, 3: 12.5, 4: 11}
_EAST_ASIA_FONT = "宋体"
_LATIN_FONT = "Times New Roman"
_BODY_SIZE = 10.5

# 颜色规范（十六进制，与 pdf_export 保持一致）
_PRIMARY = "1F4E79"   # 深蓝藏青：标题、强调
_TEXT = "1A1A1A"      # 正文近黑
_MUTED = "808080"     # 辅助灰：meta、页脚


def _style(run, size: float = _BODY_SIZE, bold: bool = False,
           color: str = _TEXT) -> None:
    """
    中文字体必须显式设 eastAsia，只设 font.name 的话 Word 里中文会掉回默认字体，
    而西文却是设好的——中英混排的备忘录会显示成两种字号的拼接，非常明显。

    字号用 10.5pt（五号）：这是中文正式文档的通例，比 Word 默认的 11pt 更紧凑。
    颜色默认正文近黑 _TEXT，标题 / meta 层再覆盖成 _PRIMARY / _MUTED。
    """
    run.font.size = Pt(size)
    run.bold = bold
    run.font.color.rgb = RGBColor.from_string(color)
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
    paragraph.paragraph_format.keep_with_next = True  # 标题不孤立在页尾
    if level == 1:
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _add_runs(paragraph, text, bold=True)
    for run in paragraph.runs:
        run.font.size = Pt(_HEADING_SIZES.get(level, 11))
        run.font.color.rgb = RGBColor.from_string(_PRIMARY)


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


def _add_bottom_border(paragraph, color: str = "CCCCCC", sz: str = "4",
                       space: str = "2") -> None:
    """给段落加下边框（页眉分隔线 / 正文分隔线共用）"""
    p_pr = paragraph._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), sz)
    bottom.set(qn("w:space"), space)
    bottom.set(qn("w:color"), color)
    borders.append(bottom)
    p_pr.append(borders)


def _rule(doc: Document) -> None:
    """分隔线：给空段落加下边框，比塞一串破折号干净"""
    paragraph = doc.add_paragraph()
    _add_bottom_border(paragraph, "CCCCCC", "6", "1")


def _add_field(run, instr: str) -> None:
    """插入 Word 域（页脚页码用 PAGE 域）"""
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    text = OxmlElement("w:instrText")
    text.set(qn("xml:space"), "preserve")
    text.text = instr
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.append(begin)
    run._r.append(text)
    run._r.append(end)


# 正文表格：可用宽度 = A4 21cm − 左右边距 3.17cm×2 = 14.66cm
_TABLE_WIDTH_CM = 14.66


def _split_row(line: str) -> List[str]:
    """把一行 `| a | b |` 切成单元格文本（去首尾竖线，逐格 strip）"""
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _is_table_sep(line: str) -> bool:
    """表格分隔行：如 `| --- | ---: |`，去掉 | : - 空格后应只剩空"""
    return bool(re.fullmatch(r"[\s|:\-]+", line)) and "-" in line


def _table_col_widths(n: int) -> List[float]:
    """列宽比例：2 列时名称列稍宽（得分列窄）；其余均分"""
    if n == 2:
        return [0.62, 0.38]
    return [1.0 / n] * n


def _shade(cell, fill: str) -> None:
    """单元格底色（表头深蓝 / 数据行斑马纹）"""
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), fill)
    tc_pr.append(shd)


def _repeat_header_row(row) -> None:
    """标记表头行「跨页重复」（w:tblHeader），与 PDF 的 repeatRows 对齐"""
    tr_pr = row._tr.get_or_add_trPr()
    tbl_header = OxmlElement("w:tblHeader")
    tbl_header.set(qn("w:val"), "true")
    tr_pr.append(tbl_header)


def _add_table(doc: Document, header: List[str], rows: List[List[str]]) -> None:
    """
    渲染一张表格：深蓝表头白字 + 斑马纹数据行，中文字体宋体。

    python-docx 的 add_table 默认列宽不匀，这里按 _table_col_widths 显式设宽，
    否则 4 列缺口表会被 Word 摊成等宽、文字挤压。
    """
    ncols = len(header)
    table = doc.add_table(rows=0, cols=ncols)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    widths = [Cm(_TABLE_WIDTH_CM * r) for r in _table_col_widths(ncols)]

    # 表头
    hdr = table.add_row().cells
    for j, text in enumerate(header):
        _shade(hdr[j], _PRIMARY)
        hdr[j].width = widths[j]
        p = hdr[j].paragraphs[0]
        run = p.add_run(text)
        _style(run, size=9.5, bold=True, color="FFFFFF")
    _repeat_header_row(table.rows[0])

    # 数据行（奇数行浅灰斑马纹）
    for r_i, row in enumerate(rows):
        cells = table.add_row().cells
        for j in range(ncols):
            cells[j].width = widths[j]
            text = row[j] if j < len(row) else ""
            p = cells[j].paragraphs[0]
            _add_runs(p, text)
            for run in p.runs:
                run.font.size = Pt(9.5)
            if r_i % 2 == 1:
                _shade(cells[j], "F2F6FA")

    doc.add_paragraph()  # 表格后空行，避免与下一段贴死


def _brand_run(paragraph, text: str, size: float) -> None:
    """页眉页脚文字：辅助灰 + 小字号"""
    run = paragraph.add_run(text)
    _style(run, size=size, color=_MUTED)
    return run


def _setup_branding(doc: Document, header_text: Optional[str]) -> None:
    """
    页眉页脚（与 pdf_export._on_page 对齐）：
    - 页眉：左侧「诉算 · 主诉评估结果」+ 右侧案件名，下加细分隔线
    - 页脚：左侧免责声明 + 右侧「第 X 页」（PAGE 域，Word 打开自动算页码）
    """
    section = doc.sections[0]
    usable = section.page_width - section.left_margin - section.right_margin

    header = section.header
    hp = header.paragraphs[0]
    hp.text = ""
    hp.paragraph_format.tab_stops.add_tab_stop(usable, WD_TAB_ALIGNMENT.RIGHT)
    _brand_run(hp, "诉算 · 主诉评估结果", 9)
    if header_text:
        hp.add_run("\t")
        _brand_run(hp, header_text, 9)
    _add_bottom_border(hp, "CCCCCC", "4", "2")

    footer = section.footer
    fp = footer.paragraphs[0]
    fp.text = ""
    fp.paragraph_format.tab_stops.add_tab_stop(usable, WD_TAB_ALIGNMENT.RIGHT)
    _brand_run(fp, "AI 辅助生成，仅供内部决策参考", 8.5)
    fp.add_run("\t")
    _brand_run(fp, "第 ", 8.5)
    page_run = fp.add_run()
    _style(page_run, size=8.5, color=_MUTED)
    _add_field(page_run, "PAGE")
    _brand_run(fp, " 页", 8.5)


def markdown_to_docx(markdown: str, *, title: Optional[str] = None,
                     meta: Optional[List[str]] = None,
                     header_text: Optional[str] = None) -> Document:
    """
    渲染为 python-docx 的 Document 对象。

    title 单独传而不从 markdown 的 H1 里取：导出时标题往往是「案件名 + 文档类型」，
    与正文里的 H1 并不一致（例：正文 H1 是「主诉评估结果：某某案」，
    而导出的文件名只需要后半段）。

    header_text 用于页眉右侧（通常传案件名），与 pdf_export 的 header_text 对齐。
    """
    doc = Document()

    # 页面边距：中文公文惯用上下 2.54cm、左右 3.17cm，Word 默认太窄，
    # 打印出来会觉得挤。
    for section in doc.sections:
        section.top_margin = section.bottom_margin = Pt(72)
        section.left_margin = section.right_margin = Pt(90)

    _setup_branding(doc, header_text)

    def _meta_paragraph(line: str) -> None:
        paragraph = doc.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _add_runs(paragraph, line)
        for run in paragraph.runs:
            run.font.size = Pt(9)
            run.font.color.rgb = RGBColor.from_string(_MUTED)

    meta_lines = list(meta or [])
    if title:
        # 显式传了 title：维持「标题 → meta → 正文」的旧顺序
        _heading(doc, title, 1)
        for line in meta_lines:
            _meta_paragraph(line)
        meta_flushed = True
    else:
        # 未传 title（结果/庭审导出的实际情况）：meta 延迟到正文第一个 H1 之后渲染。
        # 否则「生成日期」会压在报告主标题上面，层级倒挂。
        meta_flushed = False

    lines = (markdown or "").splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip()
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # 表格块：当前行以 | 开头，且下一行是分隔行（| --- | ---: |）
        if stripped.startswith("|") and i + 1 < len(lines) and _is_table_sep(lines[i + 1].strip()):
            header = _split_row(stripped)
            rows: List[List[str]] = []
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(_split_row(lines[i].strip()))
                i += 1
            _add_table(doc, header, rows)
            continue

        # 分隔线要在标题判定之前：--- 也可能被当成 setext 标题，这里不需要
        if re.fullmatch(r"-{3,}|\*{3,}", stripped):
            _rule(doc)
            i += 1
            continue

        heading = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if heading:
            _heading(doc, heading.group(2).strip(), len(heading.group(1)))
            if len(heading.group(1)) == 1 and not meta_flushed:
                for m in meta_lines:
                    _meta_paragraph(m)
                meta_flushed = True
            i += 1
            continue

        if stripped.startswith(">"):
            _quote(doc, stripped.lstrip(">").strip())
            i += 1
            continue

        bullet = re.match(r"^(\s*)[-*+]\s+(.*)$", line)
        if bullet:
            _bullet(doc, bullet.group(2).strip(), indent_level=len(bullet.group(1)) // 2)
            i += 1
            continue

        numbered = re.match(r"^\s*(\d+)[.、]\s+(.*)$", line)
        if numbered:
            _bullet(doc, f"{numbered.group(1)}. {numbered.group(2).strip()}")
            i += 1
            continue

        paragraph = doc.add_paragraph()
        paragraph.paragraph_format.space_after = Pt(4)
        _add_runs(paragraph, stripped)
        i += 1

    if not meta_flushed:  # 正文没有 H1 时兜底：meta 仍要出现
        for m in meta_lines:
            _meta_paragraph(m)

    return doc


def markdown_to_docx_bytes(markdown: str, *, title: Optional[str] = None,
                           meta: Optional[List[str]] = None,
                           header_text: Optional[str] = None) -> bytes:
    buffer = io.BytesIO()
    markdown_to_docx(markdown, title=title, meta=meta,
                     header_text=header_text).save(buffer)
    return buffer.getvalue()
