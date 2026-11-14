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


def binary_components(binary, min_pixels):
    _, components, regions, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    for index, (left, top, size_x, size_y, area) in enumerate(regions[1:], start=1):
        if area >= min_pixels:
            region = np.uint8(components[top:top + size_y, left:left + size_x] == index)
            yield left, top, size_x, size_y, region


def mask_components(mask, min_pixels):
    for label, color in enumerate(COLORS):
        for region in binary_components(cv2.inRange(mask, color, color), min_pixels):
            yield label, *region


def exterior_polygon(region):
    contours, _ = cv2.findContours(region, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if len(contours) != 1:
        raise ValueError('Connected mask region must have one exterior outline')
    outline = cv2.approxPolyDP(contours[0], epsilon=.5, closed=True).reshape(-1, 2)
    if len(np.unique(outline, axis=0)) < 3 or cv2.contourArea(outline) == 0:
        size_y, size_x = region.shape
        return np.array([[0, 0], [size_x, 0], [size_x, size_y], [0, size_y]])
    return outline


def mask_boxes(mask, min_pixels):
    height, width = mask.shape[:2]
    for label, left, top, size_x, size_y, _ in mask_components(mask, min_pixels):
        yield label, (left + size_x / 2) / width, (top + size_y / 2) / height, size_x / width, size_y / height


def mask_polygons(mask, min_pixels):
    height, width = mask.shape[:2]
    for label, left, top, _, _, region in mask_components(mask, min_pixels):
        outline = exterior_polygon(region)
        yield label, *(((outline + (left, top)) / (width, height)).flatten())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--min-pixels', type=int, default=32)
    parser.add_argument('--segments', action='store_true', help='Export external polygons from the masks')
    parser.add_argument('--labels-only', action='store_true', help='Use the same image filenames without re-encoding')
    args = parser.parse_args()
    if args.min_pixels < 1:
        parser.error('--min-pixels must be positive')
    args.output.mkdir(parents=True)
    splits = ('train', 'val', 'test')
    for split in splits:
        for category in (('labels',) if args.labels_only else ('images', 'labels')):
            (args.output / category / split).mkdir(parents=True)
    counts = {split: Counter() for split in splits}
    image_counts = Counter()
    seen = {}
    annotations = mask_polygons if args.segments else mask_boxes
    with ZipFile(args.archive) as archive:
        for resource in sorted(archive.namelist()):
            member = PurePosixPath(resource)
            split = member.parent.name
            if split not in splits or not member.name.endswith('_lab.png'):
                continue
            image_path = member.with_name(member.name.removesuffix('_lab.png') + '.png')
            with Image.open(BytesIO(archive.read(resource))) as label_image:
                regions = list(annotations(np.asarray(label_image.convert('RGB')), args.min_pixels))
            image_bytes = archive.read(str(image_path))
            digest = hashlib.sha256(image_bytes).digest()
            signature = split, regions
            if digest in seen:
                if seen[digest] != signature:
                    raise ValueError(f'Duplicate image with inconsistent split or labels: {image_path}')
                continue
            seen[digest] = signature
            output = args.output / 'images' / split / ('s2ds-' + image_path.stem + '.jpg')
            if not args.labels_only:
                with Image.open(BytesIO(image_bytes)) as image:
                    image.convert('RGB').save(output, quality=95)
            (args.output / 'labels' / split / output.with_suffix('.txt').name).write_text(''.join(
                f'{label} ' + ' '.join(f'{value:.8f}' for value in coordinates) + '\n'
                for label, *coordinates in regions),
                encoding='utf-8')
            counts[split].update(CLASSES[label] for label, *_ in regions)
            image_counts[split] += 1
    report = {'classes': CLASSES, 'modality': 'rgb', 'images': image_counts, 'counts': counts,
              'source': 'S2DS by Christian Benz and Volker Rodehorst',
              'annotationFormat': 'polygons' if args.segments else 'boxes',
              'minMaskComponentPixels': args.min_pixels,
              'missingTrainLabels': [label for label in CLASSES if not counts['train'][label]],
              'missingValLabels': [label for label in CLASSES if not counts['val'][label]]}
    (args.output / 'coverage.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
