"""Export the product guide and checkpoint speaker notes to editable Word files."""
import argparse
import json
from pathlib import Path
import subprocess

from docx import Document
from docx.shared import Inches, Pt

from tools import build_knowledge_pack as layout


ROOT = Path(__file__).resolve().parents[1]
BRIEFS = (
    ('TIMUR/product-guide.md', 'output/documents/cyber102-product-guide.docx'),
    ('TIMUR/checkpoint-1-speaker-kit.md', 'output/documents/cyber102-checkpoint-1.docx'),
)


def blocks(doc, tokens, chapter, depth=0):
    for token in tokens:
        kind = token['type']
        if kind in ('space', 'hr'):
            continue
        if kind == 'heading':
            level = token['depth']
            style = 'Title' if level == 1 else f'Heading {min(4, level - 1)}'
            text = layout.heading_text(token['tokens'])
            paragraph = doc.add_paragraph(text, style)
            if chapter.name == 'checkpoint-1-speaker-kit.md' and text in ('Показ на 2 минуты', 'Вопросы жюри'):
                paragraph.paragraph_format.page_break_before = True
        elif kind in ('paragraph', 'text'):
            paragraph = doc.add_paragraph()
            if depth:
                paragraph.paragraph_format.left_indent = Inches(0.20 * depth)
            layout.add_inline(paragraph, token.get('tokens', [{'type': 'text', 'text': token.get('text', '')}]), chapter)
        elif kind == 'list':
            for index, item in enumerate(token['items']):
                child_tokens = item['tokens']
                for number, child in enumerate(child_tokens):
                    if child['type'] in ('text', 'paragraph'):
                        paragraph = doc.add_paragraph()
                        paragraph.paragraph_format.left_indent = Inches(0.20 * (depth + 1))
                        paragraph.paragraph_format.first_line_indent = Inches(-0.20)
                        paragraph.paragraph_format.space_after = Pt(4)
                        if number == 0:
                            prefix = f"{int(token.get('start') or 1) + index}. " if token.get('ordered') else '• '
                            paragraph.add_run(prefix)
                        layout.add_inline(paragraph, child.get('tokens', [{'type': 'text', 'text': child.get('text', '')}]), chapter)
                    else:
                        blocks(doc, [child], chapter, depth + 1)
        elif kind == 'blockquote':
            blocks(doc, token['tokens'], chapter, depth + 1)
        elif kind == 'table':
            layout.add_table(doc, token, chapter)
            doc.add_paragraph().paragraph_format.space_after = Pt(2)
        elif kind == 'code':
            paragraph = doc.add_paragraph()
            layout.format_run(paragraph, token['text'], code=True)
        else:
            raise ValueError(f'Unsupported Markdown block {kind} in {chapter}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node', required=True)
    parser.add_argument('--marked', required=True)
    args = parser.parse_args()
    for source, destination in BRIEFS:
        chapter = ROOT / source
        result = subprocess.run([args.node, str(ROOT / 'tools/tokenize_knowledge.mjs'), args.marked, str(chapter)], check=True, capture_output=True)
        doc = Document()
        layout.configure(doc)
        section = doc.sections[0]
        section.page_width, section.page_height = Inches(8.27), Inches(11.69)
        section.left_margin = section.right_margin = Inches(0.7)
        layout.TEXT_WIDTH = 6.87
        doc.styles['Title'].font.size = Pt(25)
        doc.styles['Heading 1'].font.size = Pt(16)
        doc.styles['Heading 2'].font.size = Pt(12)
        doc.styles['Normal'].paragraph_format.line_spacing = 1.10
        doc.core_properties.author = 'Команда Cyber102'
        doc.core_properties.title = chapter.read_text(encoding='utf-8').splitlines()[0].lstrip('# ')
        doc.core_properties.subject = 'Описание продукта и материалы первого чекпоинта'
        blocks(doc, json.loads(result.stdout)[0], chapter)
        path = ROOT / destination
        path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(path)
        print(path)


if __name__ == '__main__':
    main()
