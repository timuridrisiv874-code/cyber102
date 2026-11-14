"""Add reviewed images to train while preserving the evaluation splits."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil

from prepare_combined_rgb import inventory
from prepare_core_rgb import CLASSES


def extend(base, review, assets, holdouts, output):
    known = {digest for digest, *_ in inventory(base)}
    for manifest in holdouts:
        for row in json.loads(manifest.read_bytes()):
            known.update(row[key] for key in ('sha256', 'sourceSha256') if key in row)
    coverage = json.loads((base / 'coverage.json').read_bytes())
    rows = json.loads(review.read_bytes())
    if not rows:
        raise ValueError('Expected reviewed training images')
    selected = []
    for row in rows:
        filename = row['file']
        if filename != Path(filename).name or Path(filename).suffix != '.jpg':
            raise ValueError(f'Invalid training filename: {filename}')
        source = assets / filename
        digest = sha256(source.read_bytes()).hexdigest()
        if digest != row['sha256'] or {digest, row.get('sourceSha256', digest)} & known:
            raise ValueError(f'Unexpected or known image: {filename}')
        known.update((digest, row.get('sourceSha256', digest)))
        labels = []
        for obj in row['objects']:
            label = obj['class']
            x1, y1, x2, y2 = obj['xyxy']
            if label not in CLASSES or not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
                raise ValueError(f'Invalid annotation: {filename}')
            coords = ((x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1)
            labels.append(f'{CLASSES.index(label)} ' + ' '.join(f'{v:.8f}' for v in coords) + '\n')
            counts = coverage['counts']['train']
            counts[label] = counts.get(label, 0) + 1
        selected.append((source, labels))
    output.mkdir(parents=True, exist_ok=False)
    for kind in ('images', 'labels'):
        shutil.copytree(base / kind, output / kind, ignore=shutil.ignore_patterns('*.cache', '._*'))
    for source, labels in selected:
        stem = f'reviewed-{source.stem}'
        with (output / 'images' / 'train' / f'{stem}.jpg').open('xb') as destination:
            with source.open('rb') as original:
                shutil.copyfileobj(original, destination)
        with (output / 'labels' / 'train' / f'{stem}.txt').open('x', encoding='utf-8') as annotations:
            annotations.writelines(labels)
    coverage['images']['train'] += len(selected)
    coverage['reviewedTrainingImages'] = rows
    (output / 'coverage.json').write_text(json.dumps(coverage, indent=2) + '\n', encoding='utf-8')
    return {'images': coverage['images'], 'counts': coverage['counts']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('base', 'review', 'assets', 'output'):
        parser.add_argument(name, type=Path)
    parser.add_argument('--holdouts', nargs='+', type=Path, required=True)
    print(json.dumps(extend(**vars(parser.parse_args()))))
