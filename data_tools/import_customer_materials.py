"""Import supplied examples without assigning defect labels or equipment identities."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import shutil
import unicodedata
import zipfile
from xml.etree import ElementTree as ET

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
GROUPS = {'Насосные агрегаты': ('pump', 'Насосные агрегаты'),
          'ЭД': ('motor', 'Электродвигатели'),
          'Запорная арматура': ('valve', 'Запорная арматура'),
          'Турбопровод': ('pipe', 'Трубопроводы')}
DOCUMENT = 'Типы дефектов промышленного оборудования.docx'
NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_docx(path):
    with zipfile.ZipFile(path) as archive:
        tree = ET.fromstring(archive.read('word/document.xml'))
    sections = []
    for paragraph in tree.findall('.//w:body/w:p', NS):
        text = ''.join(
            node.text or '' for node in paragraph.findall('.//w:t', NS))
        if not text.strip():
            continue
        if re.match(r'^\d+\.', text) or not sections:
            sections.append({'heading': text, 'paragraphs': []})
        else:
            sections[-1]['paragraphs'].append(text)
    return sections


def difference_hash(image):
    pixels = list(image.convert('L').resize((9, 8)).tobytes())
    bits = [pixels[y * 9 + x] > pixels[y * 9 + x + 1] for y in range(8) for x in range(8)]
    return f'{sum(int(bit) << i for i, bit in enumerate(bits)):016x}'


def build(source, output):
    source, output = source.resolve(), output.resolve()
    previews = output / 'previews'
    previews.mkdir(parents=True, exist_ok=True)
    images, by_hash, source_files = [], {}, []
    for folder, (category, title) in GROUPS.items():
        for path in sorted((source / folder).rglob('*')):
            if not path.is_file() or path.name.startswith('.'):
                continue
            if not path.resolve().is_relative_to(source):
                raise ValueError('Source file points outside the supplied directory')
            if path.suffix.lower() not in ('.jpg', '.jpeg', '.png'):
                raise ValueError(f'Unexpected image input: {path.relative_to(source)}')
            sha = digest(path)
            relative = unicodedata.normalize('NFC', path.relative_to(source).as_posix())
            dest = output / 'raw' / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            if path.resolve() != dest.resolve():
                shutil.copy2(path, dest)
            source_files.append({'path': relative, 'bytes': path.stat().st_size, 'sha256': sha})
            if sha in by_hash:
                by_hash[sha]['aliases'].append(relative)
                continue
            with Image.open(path) as original:
                if original.width * original.height > 40_000_000:
                    raise ValueError(f'Image exceeds import limit: {relative}')
                normalized = ImageOps.exif_transpose(original).convert('RGB')
                dhash = difference_hash(normalized)
                normalized.thumbnail((1280, 1280))
                preview = previews / f'{sha}.jpg'
                normalized.save(preview, 'JPEG', quality=85)
                row = {'id': sha, 'sha256': sha, 'category': category, 'categoryTitle': title,
                       'filename': unicodedata.normalize('NFC', path.name), 'sourcePath': relative, 'aliases': [],
                       'width': normalized.width, 'height': normalized.height,
                       'originalWidth': original.width, 'originalHeight': original.height,
                       'url': f'/api/customer/images/{sha}', 'previewSha256': digest(preview),
                       'previewBytes': preview.stat().st_size, 'differenceHash': dhash,
                       'source': 'customer_provided', 'annotationStatus': 'unreviewed',
                       'equipmentSerial': None, 'defectLabel': None,
                       'rightsStatus': 'supplied_for_case_review_not_verified_for_redistribution'}
            if row['previewBytes'] > 3 * 1024 * 1024:
                raise ValueError(f'Preview too large: {relative}')
            images.append(row)
            by_hash[sha] = row
    document = source / DOCUMENT
    if not document.resolve().is_relative_to(source):
        raise ValueError('Document points outside the supplied directory')
    dest = output / 'raw' / DOCUMENT
    if document.resolve() != dest.resolve():
        shutil.copy2(document, dest)
    source_files.append({'path': DOCUMENT, 'bytes': document.stat().st_size, 'sha256': digest(document)})
    documents = [{'id': 'external-defect-types', 'title': DOCUMENT, 'sha256': digest(document),
                  'sections': read_docx(document), 'source': 'customer_provided'}]
    counts = Counter(row['category'] for row in images)
    candidates = []
    for i, first in enumerate(images):
        for second in images[i + 1:]:
            distance = (int(first['differenceHash'], 16) ^ int(second['differenceHash'], 16)).bit_count()
            if distance <= 6:
                candidates.append({'first': first['id'], 'second': second['id'], 'distance': distance,
                                   'status': 'manual_review_required'})
    result = {'schemaVersion': 1, 'source': 'customer_examples',
              'summary': {'files': len(source_files), 'images': len(source_files) - 1,
                          'uniqueImages': len(images), 'duplicateImages': len(source_files) - 1 - len(images),
                          'categories': [{'id': id_, 'title': title, 'count': counts[id_]}
                                         for id_, title in GROUPS.values()]},
              'images': images, 'documents': documents,
              'notice': 'Материалы заказчика — примеры внешних признаков. Папка задаёт тему, а не подтверждённый класс дефекта. Связь фото с конкретным насосом и Excel не установлена.',
              'qualityNotes': ['В наборе встречаются водяные знаки фотобанков и Qwen, а также снимки экранов и похожие копии. Происхождение каждого примера требует проверки перед обучением.',
                               'Ни один файл не получает автоматически экспертную метку дефекта или координаты разметки.'],
              'possibleNearDuplicates': candidates, 'sources': source_files}
    staged = output / 'materials.json.tmp'
    staged.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    staged.replace(output / 'materials.json')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=ROOT)
    parser.add_argument('--output-root', type=Path, default=ROOT / 'data/customer')
    args = parser.parse_args()
    result = build(args.source_root, args.output_root)
    print(json.dumps({'summary': result['summary'], 'near_duplicate_candidates': len(result['possibleNearDuplicates'])}, ensure_ascii=False))


if __name__ == '__main__':
    main()
