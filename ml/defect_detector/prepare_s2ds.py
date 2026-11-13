"""Convert S2DS masks to detections of efflorescence and structural surface damage."""
import argparse
from collections import Counter
import hashlib
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

import cv2
import numpy as np
from PIL import Image


CLASSES = ('efflorescence', 'masonry_damage')
COLORS = ((0, 255, 255), (255, 0, 0))


def mask_boxes(mask, min_pixels):
    height, width = mask.shape[:2]
    for label, color in enumerate(COLORS):
        binary = cv2.inRange(mask, color, color)
        _, _, regions, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
        for left, top, size_x, size_y, area in regions[1:]:
            if area >= min_pixels:
                yield label, (left + size_x / 2) / width, (top + size_y / 2) / height, size_x / width, size_y / height


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--min-pixels', type=int, default=32)
    args = parser.parse_args()
    if args.min_pixels < 1:
        parser.error('--min-pixels must be positive')
    args.output.mkdir(parents=True)
    splits = ('train', 'val', 'test')
    for split in splits:
        for category in ('images', 'labels'):
            (args.output / category / split).mkdir(parents=True)
    counts = {split: Counter() for split in splits}
    image_counts = Counter()
    seen = {}
    with ZipFile(args.archive) as archive:
        for resource in sorted(archive.namelist()):
            member = PurePosixPath(resource)
            split = member.parent.name
            if split not in splits or not member.name.endswith('_lab.png'):
                continue
            image_path = member.with_name(member.name.removesuffix('_lab.png') + '.png')
            with Image.open(BytesIO(archive.read(resource))) as label_image:
                boxes = list(mask_boxes(np.asarray(label_image.convert('RGB')), args.min_pixels))
            image_bytes = archive.read(str(image_path))
            digest = hashlib.sha256(image_bytes).digest()
            signature = split, boxes
            if digest in seen:
                if seen[digest] != signature:
                    raise ValueError(f'Duplicate image with inconsistent split or labels: {image_path}')
                continue
            seen[digest] = signature
            output = args.output / 'images' / split / ('s2ds-' + image_path.stem + '.jpg')
            with Image.open(BytesIO(image_bytes)) as image:
                image.convert('RGB').save(output, quality=95)
            (args.output / 'labels' / split / output.with_suffix('.txt').name).write_text(''.join(
                f'{label} {x:.8f} {y:.8f} {w:.8f} {h:.8f}\n' for label, x, y, w, h in boxes),
                encoding='utf-8')
            counts[split].update(CLASSES[label] for label, *_ in boxes)
            image_counts[split] += 1
    report = {'classes': CLASSES, 'modality': 'rgb', 'images': image_counts, 'counts': counts,
              'source': 'S2DS by Christian Benz and Volker Rodehorst',
              'minMaskComponentPixels': args.min_pixels,
              'missingTrainLabels': [label for label in CLASSES if not counts['train'][label]],
              'missingValLabels': [label for label in CLASSES if not counts['val'][label]]}
    (args.output / 'coverage.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
