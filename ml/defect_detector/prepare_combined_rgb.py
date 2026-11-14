"""Extend the four-class RGB dataset with reviewed terminal and liquid examples."""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import shutil

from prepare_core_rgb import CLASSES, SPLITS


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def inventory(root, *, extra_label=None):
    metadata = json.loads((root / 'coverage.json').read_bytes())
    labels = (extra_label,) if extra_label else CLASSES
    if tuple(metadata['classes']) != labels:
        raise ValueError(f'Incorrect class order for {root}')
    for split in SPLITS if extra_label is None else SPLITS[:2]:
        for photo in sorted((root / 'images' / split).iterdir()):
            if photo.name.startswith('.') or photo.suffix.lower() not in ('.jpg', '.jpeg', '.png', '.webp'):
                continue
            rows = []
            label_file = root / 'labels' / split / photo.with_suffix('.txt').name
            for line in label_file.read_text(encoding='utf-8').splitlines():
                if not line.strip():
                    continue
                index, *coordinates = line.split()
                i = int(index)
                if not 0 <= i < len(labels) or len(coordinates) != 4:
                    raise ValueError(f'Invalid label in {label_file}')
                x, y, w, h = map(float, coordinates)
                if not (0 < w <= 1 and 0 < h <= 1 and 0 <= x <= 1 and 0 <= y <= 1):
                    raise ValueError(f'Invalid box in {label_file}')
                rows.append((CLASSES.index(labels[i]), x, y, w, h))
            yield digest(photo), split, photo, rows


def prepare(base, terminals, liquid, holdouts, output):
    excluded = {row[field] for path in holdouts for row in json.loads(path.read_bytes())
                for field in ('sha256', 'sourceSha256') if field in row}
    records, names = {}, set()
    added, merged = Counter(), Counter()
    for key, root, label in (('base', base, None), ('terminal', terminals, 'terminal_damage'),
                             ('liquid', liquid, 'wet_patch')):
        for image_hash, split, photo, boxes in inventory(root, extra_label=label):
            if image_hash in excluded:
                raise ValueError(f'Exact overlap with a holdout: {photo}')
            if image_hash in records:
                old_split, _, _, old = records[image_hash]
                if old_split != split or key == 'base':
                    raise ValueError(f'Overlapping dataset splits or duplicate base image: {photo}')
                if old_split == 'test':
                    raise ValueError(f'Attempted to update an internal test image: {photo}')
                if boxes:
                    selected = CLASSES.index(label)
                    old[:] = [box for box in old if box[0] != selected] + boxes
                merged[key] += 1
                continue
            filename = photo.name if key == 'base' else f'{key}-{photo.name}'
            label_name = Path(filename).with_suffix('.txt').name
            if (split, label_name) in names:
                raise ValueError(f'Duplicate label filename: {filename}')
            names.add((split, label_name))
            records[image_hash] = (split, photo, filename, boxes)
            added[key] += 1

    counts = {split: Counter() for split in SPLITS}
    images = Counter()
    for split, _, _, boxes in records.values():
        images[split] += 1
        counts[split].update(CLASSES[box[0]] for box in boxes)
    if any(not counts[split][label] for split in SPLITS[:2] for label in CLASSES):
        raise ValueError('Train and validation must cover all four classes')

    output.mkdir(parents=True, exist_ok=False)
    for split in SPLITS:
        for directory in ('images', 'labels'):
            (output / directory / split).mkdir(parents=True)
    for split, photo, filename, boxes in records.values():
        shutil.copyfile(photo, output / 'images' / split / filename)
        label_path = output / 'labels' / split / Path(filename).with_suffix('.txt').name
        label_path.write_text(''.join(f'{i} {x:.8f} {y:.8f} {w:.8f} {h:.8f}\n'
                                     for i, x, y, w, h in boxes), encoding='utf-8')
    report = {'classes': CLASSES, 'modality': 'rgb', 'images': images, 'counts': counts,
              'addedImages': added, 'mergedImages': merged, 'holdoutHashes': len(excluded),
              'missingTrainLabels': [], 'missingValLabels': []}
    (output / 'coverage.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('base', 'terminals', 'liquid', 'output'):
        parser.add_argument(name, type=Path)
    parser.add_argument('--holdouts', nargs='+', type=Path, required=True)
    print(json.dumps(prepare(**vars(parser.parse_args()))))
