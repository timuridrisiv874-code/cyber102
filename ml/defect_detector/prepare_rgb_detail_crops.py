"""Add training-only detail crops for classes with few RGB defect boxes."""
import argparse
from collections import Counter
import json
from math import ceil, sqrt
from pathlib import Path
import shutil

from PIL import Image, ImageOps

from prepare_combined_rgb import inventory
from prepare_core_rgb import CLASSES


def crop_region(box, width, height, scale):
    _, x, y, w, h = box
    minimum = min(width, height) * .24 * sqrt(scale)
    cw = min(width, ceil(max(w * width * scale, minimum)))
    ch = min(height, ceil(max(h * height * scale, minimum)))
    left = min(max(round(x * width - cw / 2), 0), width - cw)
    top = min(max(round(y * height - ch / 2), 0), height - ch)
    return left, top, left + cw, top + ch


def labels_for_crop(boxes, region, width, height):
    left, top, right, bottom = region
    cw, ch = right - left, bottom - top
    labels = []
    for label, x, y, w, h in boxes:
        x1, y1 = max(0, (x - w / 2) * width), max(0, (y - h / 2) * height)
        x2, y2 = min(width, (x + w / 2) * width), min(height, (y + h / 2) * height)
        a, b, c, d = max(left, x1), max(top, y1), min(right, x2), min(bottom, y2)
        if a >= c or b >= d:
            continue
        # Keep visible annotations, and avoid teaching on ambiguous fragments at crop edges.
        if (c - a) * (d - b) < .65 * (x2 - x1) * (y2 - y1):
            return []
        labels.append((label, ((a + c) / 2 - left) / cw, ((b + d) / 2 - top) / ch,
                       (c - a) / cw, (d - b) / ch))
    return labels


def prepare(source, target):
    originals = list(inventory(source))
    seen = {digest for digest, *_ in originals}
    if len(seen) != len(originals):
        raise ValueError('Source contains duplicate images')
    report = json.loads((source / 'coverage.json').read_bytes())
    report['detailCrops'] = []
    target.mkdir(parents=True, exist_ok=False)
    for kind in ('images', 'labels'):
        shutil.copytree(source / kind, target / kind, ignore=shutil.ignore_patterns('*.cache', '._*'))

    added = Counter()
    counts = Counter()
    scales = {CLASSES.index('terminal_damage'): (2.0, 3.5),
              CLASSES.index('wet_patch'): (2.0, 3.5), CLASSES.index('crack'): (2.5,)}
    for digest, split, path, boxes in originals:
        focus = sorted((box for box in boxes if box[0] in scales),
                       key=lambda box: (report['counts']['train'][CLASSES[box[0]]], box[3] * box[4]))
        if split != 'train' or not focus:
            continue
        with Image.open(path) as raw, ImageOps.exif_transpose(raw).convert('RGB') as photo:
            width, height = photo.size
            regions = set()
            for box in focus[:2]:
                for scale in scales[box[0]]:
                    region = crop_region(box, width, height, scale)
                    left, top, right, bottom = region
                    if (region in regions or (right - left) * (bottom - top) > .85 * width * height
                            or min(right - left, bottom - top) < 96):
                        continue
                    labels = labels_for_crop(boxes, region, width, height)
                    if not labels:
                        continue
                    regions.add(region)
                    name = f'detail-{digest[:20]}-{len(regions)}'
                    img = target / 'images' / 'train' / (name + '.jpg')
                    photo.crop(region).save(img, quality=95, subsampling=0)
                    label_file = target / 'labels' / 'train' / (name + '.txt')
                    with label_file.open('x', encoding='utf-8') as file:
                        for index, *values in labels:
                            file.write(f'{index} ' + ' '.join(f'{value:.8f}' for value in values) + '\n')
                    added[CLASSES[box[0]]] += 1
                    counts.update(CLASSES[row[0]] for row in labels)
                    report['detailCrops'].append({'source': path.name, 'sourceSha256': digest,
                                                  'file': img.name, 'xyxyPixels': region})
    report['cropsByFocusClass'] = added
    report['images']['train'] += sum(added.values())
    for label, count in counts.items():
        report['counts']['train'][label] = report['counts']['train'].get(label, 0) + count
    (target / 'coverage.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return {'crops': added, 'images': report['images'], 'counts': report['counts']}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source', type=Path)
    p.add_argument('target', type=Path)
    args = p.parse_args()
    print(json.dumps(prepare(args.source, args.target)))
