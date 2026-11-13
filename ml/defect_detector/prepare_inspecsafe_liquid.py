"""Export reviewed InspecSafe liquid regions with equipment scenes kept together."""
import argparse
import base64
from collections import Counter
import hashlib
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import sys
import tarfile
import tempfile

from PIL import Image


SPLIT_POINTS = {
    'train': {
        'seal-demonstration': ('002742', '002744'),
        'red-pipe': ('002795',),
        'green-pipe-demonstration': ('002909',),
        'control-cabin-pumps': ('002911', '002914', '002916'),
        'parallel-pumps': ('002920', '002921'),
        'compressor-pumps': ('002922', '002923', '002924'),
        'piped-pump-bank': ('002935',),
        'green-valve-bank': ('002937', '002938', '002939'),
        'painted-floor': ('002684',),
        'dry-service-aisle': ('002807', '002813'),
        'glossy-switchgear-aisle': ('002973', '003015'),
        'clean-outdoor-equipment': ('003017', '003020'),
    },
    'val': {
        'white-pipe-bank': ('002929', '002930'),
        'vessel-base': ('002941',),
        'freestanding-motor': ('002947',),
        'dry-yellow-pipes': ('002779', '002780'),
        'dry-control-cabinets': ('002979', '002988'),
    },
}
NEGATIVE_SCENES = {
    'painted-floor', 'dry-service-aisle', 'glossy-switchgear-aisle', 'clean-outdoor-equipment',
    'dry-yellow-pipes', 'dry-control-cabinets',
}
# Exclude a label on the dry painted elbow and one overlapping annotation of the same spill.
LIQUID_INDICES = {'002795': (0,), '002937': (0,)}


def extract_train(stream, destination):
    pending = {point for scenes in SPLIT_POINTS.values() for points in scenes.values() for point in points}
    inventory = []
    with tarfile.open(fileobj=stream, mode='r|gz', bufsize=65536) as archive:
        for member in archive:
            name = member.name
            path = PurePosixPath(name)
            if (not name.startswith('DATA_PATH/train/Annotations/') or not member.isfile()
                    or path.suffix != '.json' or member.size > 12_000_000):
                continue
            parts = path.stem.rsplit('-', 2)
            if (not path.name.startswith(('oil_chemical-', 'power-')) or len(parts) != 3
                    or parts[-1] != '001' or parts[-2] not in pending):
                continue
            data = json.load(archive.extractfile(member))
            contents = base64.b64decode(data.pop('imageData'), validate=True)
            filename = path.stem + '.jpg'
            (destination / filename).write_bytes(contents)
            (destination / path.name).write_text(json.dumps(data), encoding='utf-8')
            inventory.append({'file': filename, 'source': name, 'sha256': hashlib.sha256(contents).hexdigest()})
            pending.remove(parts[-2])
            if not pending:
                break
    if pending:
        raise ValueError(f'Missing reviewed points: {", ".join(sorted(pending))}')
    (destination / 'inventory.json').write_text(json.dumps(inventory), encoding='utf-8')


def liquid_boxes(data, width, height, point):
    regions = [shape for shape in data['shapes'] if shape['label'] == 'Liquid']
    indices = LIQUID_INDICES.get(point, range(len(regions)))
    boxes = []
    for index in indices:
        region = regions[index]
        if region['shape_type'] not in ('rectangle', 'polygon'):
            raise ValueError(f'Unexpected region type in {point}')
        xs, ys = zip(*region['points'], strict=True)
        left, right = max(0, min(xs)) / width, min(width, max(xs)) / width
        top, bottom = max(0, min(ys)) / height, min(height, max(ys)) / height
        if not 0 <= left < right <= 1 or not 0 <= top < bottom <= 1:
            raise ValueError(f'Invalid liquid geometry in {point}')
        w, h = right - left, bottom - top
        boxes.append(((left + right) / 2, (top + bottom) / 2, w, h))
    return boxes


def export(source, holdouts, output):
    inventory = json.loads((source / 'inventory.json').read_text(encoding='utf-8'))
    by_point = {}
    for row in inventory:
        if not row['source'].startswith('DATA_PATH/train/Annotations/'):
            raise ValueError('Training inputs must come from the author training archive')
        point = Path(row['file']).stem.split('-')[-2]
        if point in by_point:
            raise ValueError(f'Multiple frames at point {point}')
        by_point[point] = row
    heldout = {row['sha256'] for path in holdouts
               for row in json.loads(path.read_text(encoding='utf-8'))}
    output.mkdir(parents=True, exist_ok=False)
    report = {'classes': ['wet_patch'], 'modality': 'rgb', 'counts': {}, 'scenes': {},
              'missingTrainLabels': [], 'missingValLabels': []}
    seen = set()
    for split, scenes in SPLIT_POINTS.items():
        images = output / 'images' / split
        labels = output / 'labels' / split
        images.mkdir(parents=True)
        labels.mkdir(parents=True)
        counts = Counter()
        for scene, points in scenes.items():
            for point in points:
                photo = by_point[point]
                image_path = source / photo['file']
                contents = image_path.read_bytes()
                digest = hashlib.sha256(contents).hexdigest()
                if digest != photo['sha256'] or digest in seen or digest in heldout:
                    raise ValueError(f'Mismatched or reused image {image_path}')
                seen.add(digest)
                data = json.loads(image_path.with_suffix('.json').read_text(encoding='utf-8'))
                width, height = data['imageWidth'], data['imageHeight']
                with Image.open(BytesIO(contents)) as image:
                    if image.size != (width, height) or image.getexif().get(274, 1) != 1:
                        raise ValueError(f'Unexpected image layout: {image_path}')
                boxes = liquid_boxes(data, width, height, point)
                if bool(boxes) == (scene in NEGATIVE_SCENES):
                    raise ValueError(f'Unexpected liquid annotation count for {scene}: {image_path}')
                (images / photo['file']).write_bytes(contents)
                (labels / image_path.with_suffix('.txt').name).write_text(
                    ''.join('0 ' + ' '.join(f'{value:.8f}' for value in box) + '\n' for box in boxes),
                    encoding='utf-8')
                counts['positive' if boxes else 'negative'] += 1
                counts['boxes'] += len(boxes)
        if not counts['positive'] or not counts['negative']:
            raise ValueError(f'Expected positive and control scenes in {split}')
        report['counts'][split] = counts
        report['scenes'][split] = scenes
    (output / 'coverage.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='Extracted folder, author train.tar.gz, or - for stdin')
    parser.add_argument('output', type=Path)
    parser.add_argument('holdouts', type=Path, nargs='+')
    args = parser.parse_args()
    if args.source.is_dir():
        report = export(args.source, args.holdouts, args.output)
    else:
        with tempfile.TemporaryDirectory(prefix='cyber102-inspecsafe-') as temporary:
            staging = Path(temporary)
            if args.source == Path('-'):
                extract_train(sys.stdin.buffer, staging)
            else:
                with args.source.open('rb') as stream:
                    extract_train(stream, staging)
            report = export(staging, args.holdouts, args.output)
    print(json.dumps(report))
