"""Preserve the Ostrava splits and make matched box and polygon benchmarks."""
import argparse
from collections import Counter
import hashlib
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

import numpy as np
from PIL import Image

from prepare_s2ds import binary_components, exterior_polygon


SOURCE = 'https://doi.org/10.5281/zenodo.11235637'
ARCHIVE_MD5 = 'e30f1f62eb85018c2faee44909e69a06'
SPLITS = {'train': 'train', 'validation': 'val', 'test': 'test'}
OBJECT_CLASS = 'corrosion'


def read_masks(archive, path, width, height):
    member = path.parent.parent / 'masks' / path.with_suffix('.png').name
    with Image.open(BytesIO(archive.read(str(member)))) as mask:
        if mask.size != (width, height):
            raise ValueError(f'Image and mask size differ: {path}')
        pixels = np.asarray(mask.convert('L'))
    return (pixels >= (128 if pixels.max() > 1 else 1)).astype(np.uint8)


def prepare(archive_path, output):
    with archive_path.open('rb') as stream:
        if hashlib.file_digest(stream, 'md5').hexdigest() != ARCHIVE_MD5:
            raise ValueError('Ostrava archive does not match the published checksum')
    box_root, segment_root = output / 'boxes', output / 'segments'
    for root in (box_root, segment_root):
        root.mkdir(parents=True, exist_ok=False)
        for split in SPLITS.values():
            for kind in ('images', 'labels'):
                (root / kind / split).mkdir(parents=True)
    images, objects, empty = Counter(), Counter(), Counter()
    seen = set()
    test_rows = []
    with ZipFile(archive_path) as archive:
        for name in sorted(archive.namelist()):
            path = PurePosixPath(name)
            if path.suffix != '.jpg' or path.parent.name != 'images':
                continue
            split = SPLITS[path.parent.parent.name]
            photo_bytes = archive.read(name)
            raw_hash = hashlib.sha256(photo_bytes).hexdigest()
            if raw_hash in seen:
                raise ValueError(f'Duplicate source photograph: {path}')
            seen.add(raw_hash)
            with Image.open(BytesIO(photo_bytes)) as source:
                width, height = source.size
                mask = read_masks(archive, path, width, height)
                photo = source.convert('RGB')
            photo.thumbnail((1024, 1024))
            with BytesIO() as stream:
                photo.save(stream, 'JPEG', quality=91)
                jpeg = stream.getvalue()
            photo.close()
            filename = f'ostrava-{split}-{path.stem}.jpg'
            labels = {box_root: [], segment_root: []}
            annotations = []
            for left, top, w, h, region in binary_components(mask, 500):
                if min(w, h) < 12:
                    continue
                box = ((left + w / 2) / width, (top + h / 2) / height, w / width, h / height)
                polygon = ((exterior_polygon(region) + (left, top)) / (width, height)).flatten()
                labels[box_root].append(box)
                labels[segment_root].append(polygon)
                annotations.append({'class': OBJECT_CLASS,
                                    'xyxy': [left / width, top / height, (left + w) / width, (top + h) / height]})
            if not annotations and mask.any():
                raise ValueError(f'Mask would be turned into a false negative: {path}')
            for root, coords in labels.items():
                (root / 'images' / split / filename).write_bytes(jpeg)
                label_path = root / 'labels' / split / Path(filename).with_suffix('.txt').name
                label_path.write_text(''.join('0 ' + ' '.join(f'{value:.8f}' for value in row) + '\n'
                                              for row in coords), encoding='utf-8')
            images[split] += 1
            objects[split] += len(annotations)
            empty[split] += not annotations
            if split == 'test':
                test_rows.append({'file': filename, 'scene': filename, 'objects': annotations,
                                  'sourceSha256': raw_hash, 'sha256': hashlib.sha256(jpeg).hexdigest()})
    if images != {'train': 75, 'val': 15, 'test': 15}:
        raise ValueError(f'Unexpected split sizes: {images}')
    for root, form in ((box_root, 'boxes'), (segment_root, 'polygons')):
        coverage = {'classes': [OBJECT_CLASS], 'annotationFormat': form, 'source': SOURCE,
                    'sourceLicense': 'CC BY 4.0', 'images': images, 'emptyImages': empty,
                    'counts': {part: {OBJECT_CLASS: objects[part]} for part in images}}
        (root / 'coverage.json').write_text(json.dumps(coverage, indent=2) + '\n', encoding='utf-8')
    (output / 'test-annotations.json').write_text(json.dumps(test_rows, indent=2) + '\n', encoding='utf-8')
    return images, objects, empty


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.archive, args.output)))
