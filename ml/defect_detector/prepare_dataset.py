"""Merge reviewed YOLO detection exports into one explicit, audited label map."""
import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
import shutil

import yaml

SPLITS = {'train': 'train', 'valid': 'val', 'val': 'val', 'test': 'test'}


def load_names(path):
    values = yaml.safe_load(path.read_text(encoding='utf-8'))['names']
    return dict(enumerate(values)) if isinstance(values, list) else {int(k): v for k, v in values.items()}


def convert_label(text, names, mapping, label_ids):
    result = []
    for line in text.splitlines():
        if not line.strip():
            continue
        index, *position = line.split()
        coords = [float(value) for value in position]
        if len(coords) != 4 or not all(math.isfinite(value) and 0 <= value <= 1 for value in coords):
            raise ValueError('Expected four normalized YOLO box coordinates')
        x, y, w, h = coords
        if min(w, h) <= 0 or min(x - w / 2, y - h / 2) < -.002 or max(x + w / 2, y + h / 2) > 1.002:
            raise ValueError('Box lies outside the source image')
        mapped = mapping[names[int(index)]]
        result.append((label_ids[mapped], *coords))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sources', type=Path, help='JSON list of source directories, mapping and modality')
    parser.add_argument('output', type=Path)
    parser.add_argument('--modality', default='rgb', choices=['rgb', 'xray'])
    args = parser.parse_args()
    catalog = json.loads(Path(__file__).with_name('classes.json').read_text())['labels']
    classes = [row['id'] for row in catalog if row['modality'] in (args.modality, 'rgb_or_xray')]
    ids = {name: i for i, name in enumerate(classes)}
    counts = defaultdict(Counter)
    rows = {}
    for source in sorted(json.loads(args.sources.read_text()), key=lambda row: row['name']):
        if source['modality'] != args.modality:
            continue
        root = (args.sources.parent / source['directory']).resolve()
        data = root / 'data.yaml'
        names = load_names(data)
        mapping = source['mapping']
        if set(names.values()) != set(mapping):
            raise ValueError(f'{root}: every source label needs an explicit mapping')
        for folder, split in SPLITS.items():
            for image in sorted((root / folder / 'images').glob('*')):
                if image.suffix.lower() not in {'.jpg','.jpeg','.png','.webp'}:
                    continue
                label = root / folder / 'labels' / image.with_suffix('.txt').name
                if not label.exists():
                    # A reviewed negative must have its own empty annotation file.
                    continue
                annotations = convert_label(label.read_text(), names, mapping, ids)
                digest = hashlib.sha256(image.read_bytes()).hexdigest()
                old = rows.get(digest)
                if old and (old[1] != split or old[2] != annotations):
                    raise ValueError(f'Conflicting duplicate or split leakage for {image.name}')
                rows[digest] = (image, split, annotations)
    if not rows:
        raise ValueError('No annotated examples found for this modality')
    args.output.mkdir(exist_ok=False, parents=True)
    for digest, (image, split, labels) in rows.items():
        image_folder = args.output / 'images' / split
        label_folder = args.output / 'labels' / split
        image_folder.mkdir(parents=True, exist_ok=True)
        label_folder.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(image, image_folder / (digest + image.suffix.lower()))
        (label_folder / (digest + '.txt')).write_text(''.join(
            f'{cl} {x:.6f} {y:.6f} {w:.6f} {h:.6f}\n' for cl, x, y, w, h in labels))
        counts[split].update(classes[cl] for cl, *_ in labels)
    report = {split: {name: counts[split][name] for name in classes} for split in ('train', 'val', 'test')}
    result = {'classes': classes, 'modality': args.modality, 'counts': report,
              'missingTrainLabels': [name for name in classes if not counts['train'][name]],
              'missingValLabels': [name for name in classes if not counts['val'][name]]}
    (args.output / 'coverage.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
