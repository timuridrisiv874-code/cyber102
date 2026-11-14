"""Export reviewed SWRD radiographs as overlapping weld windows for training."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

from PIL import Image

from evaluate import strip_tiles


CLASSES = {
    '气孔': 'weld_porosity', '裂纹': 'weld_crack', '咬边': 'weld_undercut',
    '夹渣': 'weld_slag', '未熔合': 'weld_lack_of_fusion',
}
DATE = re.compile(r'20[0-9]{6}')


def source_date(name):
    match = DATE.search(name)
    if not match:
        raise ValueError(f'Missing acquisition date in {name}')
    return match[0]


def window_labels(shapes, region):
    left, top, right, bottom = region
    width, height = right - left, bottom - top
    boxes = []
    for shape in shapes:
        if shape['label'] not in CLASSES:
            continue
        xs, ys = zip(*shape['points'], strict=True)
        x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
        a, b, c, d = max(x1, left), max(y1, top), min(x2, right), min(y2, bottom)
        if a >= c or b >= d:
            continue
        if (c - a) * (d - b) < .5 * (x2 - x1) * (y2 - y1):
            # Discard ambiguous boundary crops instead of adding false negatives.
            return None
        boxes.append((CLASSES[shape['label']], ((a + c) / 2 - left) / width,
                      ((b + d) / 2 - top) / height, (c - a) / width, (d - b) / height))
    return boxes


def export(inventory, images, holdout, output):
    rows = json.loads(inventory.read_text(encoding='utf-8'))
    heldout = json.loads(holdout.read_text(encoding='utf-8'))
    dates = {'test': {source_date(row['file']) for row in heldout}, 'val': set(), 'train': set()}
    hashes = {row['sha256'] for row in heldout}
    counts = {'train': Counter(), 'val': Counter()}
    image_counts = Counter()
    positives = Counter()
    output.mkdir(parents=True, exist_ok=False)
    for split in counts:
        for kind in ('images', 'labels'):
            (output / kind / split).mkdir(parents=True)
    class_ids = {name: index for index, name in enumerate(CLASSES.values())}
    for row in sorted(rows, key=lambda entry: entry['entry']['name']):
        split = row['split']
        name = Path(row['entry']['name']).stem
        path = images / (name + '.png')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        date = source_date(name)
        if digest in hashes or any(date in values for group, values in dates.items() if group != split):
            raise ValueError(f'Conflicting source across splits: {name}')
        hashes.add(digest)
        dates[split].add(date)
        with Image.open(path) as source:
            if source.size != (row['width'], row['height']):
                raise ValueError(f'Mismatched radiograph geometry: {path}')
            windows = strip_tiles(*source.size) or [(0, 0, *source.size)]
            had_negative = False
            for i, region in enumerate(windows):
                labels = window_labels(row['shapes'], region)
                if labels is None or (not labels and had_negative):
                    continue
                stem = f'{name}-{i:03d}'
                source.crop(region).save(output / 'images' / split / f'{stem}.png')
                (output / 'labels' / split / f'{stem}.txt').write_text(''.join(
                    f'{class_ids[label]} ' + ' '.join(f'{coord:.8f}' for coord in box) + '\n'
                    for label, *box in labels), encoding='utf-8')
                counts[split].update(label for label, *_ in labels)
                image_counts[split] += 1
                positives[split] += bool(labels)
                had_negative |= not labels
    report = {'classes': list(CLASSES.values()), 'modality': 'xray', 'source': 'SWRD',
              'counts': counts, 'images': image_counts, 'positiveWindows': positives,
              'acquisitionDates': {split: sorted(days) for split, days in dates.items()},
              'missingTrainLabels': [label for label in CLASSES.values() if not counts['train'][label]],
              'missingValLabels': [label for label in CLASSES.values() if not counts['val'][label]]}
    (output / 'coverage.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inventory', type=Path)
    parser.add_argument('images', type=Path)
    parser.add_argument('holdout', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    print(json.dumps(export(args.inventory, args.images, args.holdout, args.output)))
