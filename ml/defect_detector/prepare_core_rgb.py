"""Build a site-focused RGB dataset from a broader industrial training export."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil


CLASSES = ('corrosion', 'wet_patch', 'crack', 'terminal_damage')
SPLITS = ('train', 'val', 'test')


def read_labels(path, names):
    rows = []
    for line in path.read_text(encoding='utf-8').splitlines():
        if line.strip():
            index, *coordinates = line.split()
            rows.append((names[int(index)], tuple(map(float, coordinates))))
    return rows


def compact_examples(dataset, names, max_crack_images):
    for split in SPLITS:
        images = sorted((dataset / 'images' / split).iterdir(),
                        key=lambda path: hashlib.sha256(path.name.encode()).digest())
        crack_count = 0
        for image in images:
            if image.suffix.lower() not in ('.jpg', '.jpeg', '.png', '.webp'):
                continue
            if image.name.startswith(('aug-', 'cable-', 'pwdid-')):
                continue
            if split == 'train' and image.stem == 'ref-00':
                # This reference shows visible corrosion despite its empty label.
                continue
            if image.name.startswith('steelcrack-') and split == 'train':
                if crack_count >= max_crack_images:
                    continue
                crack_count += 1
            labels = read_labels(dataset / 'labels' / split / image.with_suffix('.txt').name, names)
            if any(label not in CLASSES for label, _ in labels):
                continue
            yield image, split, labels


def reviewed_examples(review):
    for row in json.loads(review.read_text(encoding='utf-8')):
        split = row['split']
        if split == 'benchmark':
            continue
        if split not in SPLITS:
            raise ValueError(f'Unknown split {split}')
        labels = []
        for obj in row['objects']:
            left, top, right, bottom = obj['xyxy']
            if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
                raise ValueError(f"Invalid box in {row['file']}")
            labels.append((obj['class'], ((left + right) / 2, (top + bottom) / 2, right - left, bottom - top)))
        yield review.parent / row['file'], split, labels


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dataset', type=Path)
    parser.add_argument('review', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--max-crack-images', type=int, default=160)
    args = parser.parse_args()
    if args.max_crack_images < 1:
        parser.error('--max-crack-images must be positive')
    names = json.loads((args.dataset.parent / 'annotations.json').read_text(encoding='utf-8'))['classes']
    args.output.mkdir(parents=True, exist_ok=False)
    for split in SPLITS:
        for category in ('images', 'labels'):
            (args.output / category / split).mkdir(parents=True)
    counts = {split: Counter() for split in SPLITS}
    sizes = Counter()
    seen = set()
    for examples in (compact_examples(args.dataset, names, args.max_crack_images), reviewed_examples(args.review)):
        for image, split, labels in examples:
            key = hashlib.sha256(image.read_bytes()).hexdigest()
            if key in seen:
                raise ValueError(f'Duplicate image: {image}')
            seen.add(key)
            name = image.with_suffix('.txt').name
            shutil.copyfile(image, args.output / 'images' / split / image.name)
            output = args.output / 'labels' / split / name
            with output.open('x', encoding='utf-8') as file:
                file.writelines(f'{CLASSES.index(label)} ' + ' '.join(f'{v:.8f}' for v in coords) + '\n'
                                for label, coords in labels)
            counts[split].update(label for label, _ in labels)
            sizes[split] += 1
    report = {'classes': CLASSES, 'modality': 'rgb', 'images': sizes, 'counts': counts,
              'missingTrainLabels': [label for label in CLASSES if not counts['train'][label]],
              'missingValLabels': [label for label in CLASSES if not counts['val'][label]]}
    (args.output / 'coverage.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
