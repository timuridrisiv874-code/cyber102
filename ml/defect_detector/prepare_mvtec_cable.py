"""Prepare a supervised cable experiment using a fixed split of MVTec AD masks."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random

from PIL import Image


CLASSES = {
    'bent_wire': 'wire_bent', 'cable_swap': 'cable_swap', 'combined': 'cable_combined',
    'cut_inner_insulation': 'inner_insulation_cut', 'cut_outer_insulation': 'outer_insulation_cut',
    'missing_cable': 'cable_missing', 'missing_wire': 'wire_missing', 'poke_insulation': 'insulation_puncture',
}


def samples(source):
    # Reproduce the existing online-v2 positive split so the old checkpoint can be compared.
    rng = random.Random(102)
    for category, label in CLASSES.items():
        photos = sorted((source / 'test' / category).glob('*.png'))
        if len(photos) < 8:
            raise ValueError(f'Insufficient source photos for {category}')
        rng.shuffle(photos)
        for index, photo in enumerate(photos):
            split = 'test' if index == 0 else 'val' if index <= 2 else 'train'
            mask = source / 'ground_truth' / category / f'{photo.stem}_mask.png'
            yield split, photo, label, mask
    for photo in sorted((source / 'train' / 'good').glob('*.png')):
        yield 'train', photo, None, None
    for index, photo in enumerate(sorted((source / 'test' / 'good').glob('*.png'))):
        yield ('val' if index % 4 == 0 else 'test'), photo, None, None


def annotation(mask_path, size):
    with Image.open(mask_path) as image:
        if image.size != size:
            raise ValueError(f'Mask/image size mismatch for {mask_path.name}')
        bounds = image.convert('L').point(lambda value: 255 if value >= 128 else 0).getbbox()
    if not bounds:
        raise ValueError(f'Empty ground-truth mask for {mask_path.name}')
    w, h = size
    left, top, right, bottom = bounds
    return [left / w, top / h, right / w, bottom / h]


def prepare(source, output):
    dataset = list(samples(source))
    if not all(any(s == split and label is None for s, _, label, _ in dataset) for split in ('train', 'val', 'test')):
        raise ValueError('Missing normal cable controls for one or more splits')
    output.mkdir(parents=True, exist_ok=False)
    labels = list(CLASSES.values())
    counts = {split: Counter() for split in ('train', 'val', 'test')}
    size = Counter()
    seen = set()
    test = []
    for split, photo, label, mask in dataset:
        digest = hashlib.sha256(photo.read_bytes()).hexdigest()
        if digest in seen:
            raise ValueError(f'Duplicate source photo: {photo.name}')
        seen.add(digest)
        image_folder, labels_folder = (output / kind / split for kind in ('images', 'labels'))
        image_folder.mkdir(parents=True, exist_ok=True)
        labels_folder.mkdir(parents=True, exist_ok=True)
        # The originals use the same basename across categories and across normal splits.
        stem = f'{split}-{photo.parent.name}-{photo.stem}'
        image_path = image_folder / f'{stem}.jpg'
        with Image.open(photo) as raw:
            image = raw.convert('RGB')
            box = annotation(mask, image.size) if mask else None
            image.thumbnail((1024, 1024))
            image.save(image_path, quality=91)
        coordinates = ''
        objects = []
        if box:
            l, t, r, b = box
            values = ((l + r) / 2, (t + b) / 2, r - l, b - t)
            coordinates = f'{labels.index(label)} ' + ' '.join(f'{value:.8f}' for value in values) + '\n'
            objects.append({'class': label, 'xyxy': box})
            counts[split][label] += 1
        (labels_folder / f'{stem}.txt').write_text(coordinates, encoding='utf-8')
        size[split] += 1
        if split == 'test':
            test.append({'file': image_path.name, 'scene': stem, 'objects': objects,
                         'sourceFile': photo.relative_to(source).as_posix(),
                         'sourceSha256': digest, 'sha256': hashlib.sha256(image_path.read_bytes()).hexdigest()})
    info = {'classes': labels, 'images': size, 'counts': counts, 'modality': 'rgb',
            'source': 'MVTec AD cable', 'license': 'CC BY-NC-SA 4.0',
            'missingTrainLabels': [label for label in labels if not counts['train'][label]],
            'missingValLabels': [label for label in labels if not counts['val'][label]]}
    (output / 'coverage.json').write_text(json.dumps(info, indent=2) + '\n', encoding='utf-8')
    (output / 'test-annotations.json').write_text(json.dumps(test, indent=2) + '\n', encoding='utf-8')
    return info


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source.resolve(strict=True), args.output)))
