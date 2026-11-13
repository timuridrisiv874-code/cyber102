"""Build the editable Cyber102 engineering pack from its Markdown chapters."""

import argparse
import json
import re
import subprocess
from html import unescape
from pathlib import Path
from urllib.parse import quote, urlsplit

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.shared import Inches, Pt, RGBColor


ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE = ROOT / "docs" / "knowledge"
CHAPTERS = (
    "README",
    "pump-engineering",
    "ml-and-datasets",
    "vision-and-annotation",
    "integration-contract",
    "data-request",
    "acceptance-and-roadmap",
    "source-index",
)
GITHUB = "https://github.com/timuridrisiv874-code/cyber102/blob/main/"
BODY_FONT = "Arial"
CODE_FONT = "Courier New"
LINK_COLOR = "235579"
ASCII_HYPHENS = str.maketrans({chr(code): "-" for code in range(0x2010, 0x2016)})
TEXT_WIDTH = 6.9


def xml(tag, **attributes):
    element = OxmlElement(f"w:{tag}")
    for key, value in attributes.items():
        element.set(qn(f"w:{key}"), str(value))
    return element


def set_font(style, size, color="000000", bold=False):
    style.font.name = BODY_FONT
    style.font.size = Pt(size)
    style.font.color.rgb = RGBColor.from_string(color)
    style.font.bold = bold
    style.font.italic = False
    style.font.underline = False
    properties = style.element.get_or_add_rPr()
    fonts = properties.find(qn("w:rFonts"))
    for attribute in ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme"):
        fonts.attrib.pop(qn(f"w:{attribute}"), None)
    language = properties.find(qn("w:lang"))
    if language is None:
        properties.append(xml("lang", val="ru-RU"))
    else:
        language.set(qn("w:val"), "ru-RU")


def configure(doc):
    section = doc.sections[0]
    section.page_width, section.page_height = Inches(8.5), Inches(11)
    section.left_margin = section.right_margin = Inches(0.8)
    section.top_margin, section.bottom_margin = Inches(0.72), Inches(0.68)
    section.footer_distance = Inches(0.31)
    section.header_distance = Inches(0.31)

    body = doc.styles["Normal"]
    set_font(body, 11)
    body.paragraph_format.space_after = Pt(7)
    body.paragraph_format.line_spacing = 1.14
    body.paragraph_format.widow_control = True

    title = doc.styles["Title"]
    set_font(title, 29, bold=True)
    title.paragraph_format.space_before = Pt(0)
    title.paragraph_format.space_after = Pt(15)
    title.paragraph_format.keep_with_next = True
    props = title.element.get_or_add_pPr()
    border = props.find(qn("w:pBdr"))
    if border is not None:
        props.remove(border)

    for level, size in ((1, 20), (2, 14), (3, 12), (4, 11)):
        style = doc.styles[f"Heading {level}"]
        set_font(style, size, bold=True)
        style.paragraph_format.space_before = Pt(12 if level > 1 else 15)
        style.paragraph_format.space_after = Pt(6)
        style.paragraph_format.keep_with_next = True
        style.paragraph_format.keep_together = True

    for name in ("List Bullet", "List Number", "List Bullet 2", "List Number 2"):
        style = doc.styles[name]
        set_font(style, 11)
        style.paragraph_format.space_after = Pt(4)
        style.paragraph_format.line_spacing = 1.14

    set_font(doc.styles["Footer"], 9, "4C545C")
    footer = section.footer.paragraphs[0]
    footer.paragraph_format.space_after = Pt(0)
    footer.paragraph_format.tab_stops.add_tab_stop(Inches(TEXT_WIDTH), WD_ALIGN_PARAGRAPH.RIGHT)
    footer.add_run("Cyber102\t")
    field = xml("fldSimple", instr="PAGE")
    value = xml("r")
    value.append(xml("t"))
    value[0].text = "1"
    field.append(value)
    footer._p.append(field)


def format_run(paragraph, value, *, strong=False, italic=False, code=False, cell=False):
    run = paragraph.add_run(value.translate(ASCII_HYPHENS))
    run.bold = strong or None
    run.italic = italic or None
    if code:
        run.font.name = CODE_FONT
    if cell or code:
        run.font.size = Pt(9 if cell and code else 10)
    return run


def link_target(href, chapter):
    target = urlsplit(href)
    if target.scheme:
        if target.scheme not in {"http", "https", "mailto"}:
            raise ValueError(f"Unsupported link scheme {target.scheme!r}")
        return href
    if target.netloc:
        return f"https:{href}"
    destination = (chapter.parent / target.path).resolve() if target.path else chapter
    path = quote(destination.relative_to(ROOT).as_posix())
    query = f"?{target.query}" if target.query else ""
    fragment = f"#{target.fragment}" if target.fragment else ""
    return f"{GITHUB}{path}{query}{fragment}"


def add_inline(paragraph, tokens, chapter, *, strong=False, italic=False, cell=False):
    for token in tokens:
        kind = token["type"]
        style = {"strong": strong, "italic": italic, "cell": cell}
        if kind == "link":
            link = xml("hyperlink")
            target = link_target(token["href"], chapter)
            relation = paragraph.part.relate_to(
                target, RELATIONSHIP_TYPE.HYPERLINK, is_external=True
            )
            link.set(qn("r:id"), relation)
            paragraph._p.append(link)
            start = len(paragraph.runs)
            add_inline(paragraph, token["tokens"], chapter, **style)
            for run in paragraph.runs[start:]:
                run.font.color.rgb = RGBColor.from_string(LINK_COLOR)
                run.underline = True
                link.append(run._element)
        elif kind in {"strong", "em"}:
            style["strong"] = strong or kind == "strong"
            style["italic"] = italic or kind == "em"
            add_inline(paragraph, token["tokens"], chapter, **style)
        elif kind == "br":
            paragraph.add_run().add_break()
        elif kind in {"text", "escape", "codespan"}:
            if token.get("tokens"):
                add_inline(paragraph, token["tokens"], chapter, **style)
            else:
                format_run(paragraph, unescape(token["text"]), code=kind == "codespan", **style)
        else:
            raise ValueError(f"Unsupported inline Markdown {kind!r} in {chapter}")


def plain_text(tokens):
    return "".join(
        plain_text(token["tokens"]) if token.get("tokens") else unescape(token.get("text", ""))
        for token in tokens
    )


def heading_text(tokens):
    return " ".join(re.sub(r"[^\w\s]", " ", plain_text(tokens)).split())


def table_widths(chapter, columns, header):
    if columns == 2:
        return (0.28, 0.72)
    if columns == 4:
        return (0.46, 0.17, 0.16, 0.21)
    if chapter.stem == "README":
        return (0.23, 0.49, 0.28)
    if header == "Канал":
        return (0.19, 0.40, 0.41)
    return (0.24, 0.38, 0.38)


def add_table(doc, token, chapter):
    header = token["header"]
    rows = [header, *token["rows"]]
    widths = table_widths(chapter, len(header), plain_text(header[0]["tokens"]))
    table = doc.add_table(rows=len(rows), cols=len(widths))
    table.autofit = False
    properties = table._tbl.tblPr
    borders = xml("tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        borders.append(xml(edge, val="single", color="D9D9D9", sz=5))
    properties.append(borders)
    margins = xml("tblCellMar")
    for edge, width in (("top", 82), ("bottom", 82), ("left", 116), ("right", 116)):
        margins.append(xml(edge, w=width, type="dxa"))
    properties.append(margins)
    for column, width in zip(table.columns, widths):
        column.width = Inches(width * TEXT_WIDTH)
    for index, (row, row_data) in enumerate(zip(table.rows, rows)):
        row._tr.get_or_add_trPr().append(xml("cantSplit"))
        if not index:
            row._tr.get_or_add_trPr().append(xml("tblHeader"))
        for cell, item, width, align in zip(row.cells, row_data, widths, token["align"]):
            cell.width = Inches(width * TEXT_WIDTH)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            if not index:
                cell._tc.get_or_add_tcPr().append(xml("shd", fill="DCE8F1"))
            paragraph = cell.paragraphs[0]
            paragraph.paragraph_format.line_spacing = 1.12
            paragraph.paragraph_format.space_after = Pt(0)
            paragraph.paragraph_format.keep_with_next = not index
            if align in {"right", "center"}:
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            add_inline(paragraph, item["tokens"], chapter, strong=not index, cell=True)


def add_list(doc, token, chapter):
    ordered = token["ordered"]
    for index, item in enumerate(token["items"]):
        paragraph = doc.add_paragraph(style="List Bullet" if not ordered else "Normal")
        if ordered:
            paragraph.paragraph_format.left_indent = Inches(0.29)
            paragraph.paragraph_format.first_line_indent = Inches(-0.29)
            paragraph.paragraph_format.tab_stops.add_tab_stop(Inches(0.29))
            paragraph.paragraph_format.space_after = Pt(4)
            paragraph.add_run(f"{token['start'] + index}.\t")
        add_inline(paragraph, item["tokens"], chapter)


def add_chapter(doc, tokens, chapter, index):
    after_table = False
    for token in tokens:
        kind = token["type"]
        if kind == "space":
            continue
        paragraph_count = len(doc.paragraphs) if after_table else 0
        if kind == "heading":
            depth = token["depth"]
            if index == 0 and depth == 1:
                style = "Title"
            else:
                style = f"Heading {depth}"
            paragraph = doc.add_paragraph(heading_text(token["tokens"]), style)
            if depth == 1 and chapter.stem == "source-index":
                paragraph.paragraph_format.page_break_before = True
        elif kind == "paragraph":
            add_inline(doc.add_paragraph(), token["tokens"], chapter)
        elif kind == "table":
            add_table(doc, token, chapter)
        elif kind == "list":
            add_list(doc, token, chapter)
        else:
            raise ValueError(f"Unsupported block Markdown {kind!r} in {chapter}")
        if after_table and kind in {"paragraph", "list"}:
            doc.paragraphs[paragraph_count].paragraph_format.space_before = Pt(6)
        after_table = kind == "table"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", default="node", help="Node.js executable")
    parser.add_argument("--marked", default="", help="Path to marked.esm.js, when not installed next to the tokenizer")
    parser.add_argument("--output", type=Path, default=ROOT / "output/documents/cyber102-engineering-pack.docx")
    args = parser.parse_args()
    chapters = [KNOWLEDGE / f"{name}.md" for name in CHAPTERS]
    result = subprocess.run(
        [args.node, str(ROOT / "tools/tokenize_knowledge.mjs"), args.marked, *map(str, chapters)],
        check=True,
        capture_output=True,
    )
    doc = Document()
    configure(doc)
    doc.core_properties.title = "Инженерный пакет Cyber102"
    doc.core_properties.subject = "Диагностика промышленных центробежных насосов"
    doc.core_properties.author = "Команда Cyber102"
    for index, (tokens, chapter) in enumerate(zip(json.loads(result.stdout), chapters)):
        add_chapter(doc, tokens, chapter, index)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    doc.save(args.output)
    print(args.output)


if __name__ == "__main__":
    main()
