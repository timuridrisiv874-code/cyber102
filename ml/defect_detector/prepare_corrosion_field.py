"""Add reviewed industrial corrosion examples to the Ostrava box dataset."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil

from prepare_core_rgb import CLASSES, read_labels


SPLITS = ('train', 'val', 'test')


def file_hash(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read_review(path, source, external):
    seen, names = set(), set()
    for row in json.loads(path.read_bytes()):
        name, split = row['file'], row['split']
        if not name or Path(name).name != name or name in names or split not in ('train', 'val'):
            raise ValueError(f'Invalid reviewed image: {name}')
        photo = source / 'images' / split / name
        digest = file_hash(photo)
        if digest != row['sha256'] or digest in seen or digest in external:
            raise ValueError(f'Unexpected or duplicated image: {name}')
        seen.add(digest)
        names.add(name)
        annotation = source / 'labels' / split / photo.with_suffix('.txt').name
        if file_hash(annotation) != row['labelsSha256']:
            raise ValueError(f'Source annotation changed: {name}')
        boxes = [box for label, box in read_labels(annotation, CLASSES) if label == 'corrosion']
        if row['corrosionBoxes'] != len(boxes) or row['cleanSurface'] is not (not boxes):
            raise ValueError(f'Review and corrosion annotation disagree: {name}')
        label = ''.join('0 ' + ' '.join(f'{edge:.8f}' for edge in box) + '\n' for box in boxes)
        yield split, photo, label, len(boxes), digest


def prepare(ostrava, source, review, external_manifest, output):
    info = json.loads((ostrava / 'coverage.json').read_bytes())
    if info['classes'] != ['corrosion'] or info['annotationFormat'] != 'boxes':
        raise ValueError('Expected a single-class Ostrava box dataset')
    external = {row['sha256'] for row in json.loads(external_manifest.read_bytes())}
    reviewed = list(read_review(review, source, external))
    if not all(any(row[0] == split and row[3] > 0 for row in reviewed)
               and any(row[0] == split and row[3] == 0 for row in reviewed) for split in ('train', 'val')):
        raise ValueError('Train and validation each need reviewed positives and clean controls')
    seen = external | {row[4] for row in reviewed}
    original = {}
    for split in SPLITS:
        paths = sorted((ostrava / 'images' / split).glob('*.jpg'))
        if len(paths) != info['images'][split]:
            raise ValueError(f'Ostrava split changed: {split}')
        original[split] = paths
        for photo in paths:
            digest = file_hash(photo)
            if digest in seen:
                raise ValueError(f'Duplicate across input sets: {photo.name}')
            seen.add(digest)

    output.mkdir(parents=True, exist_ok=False)
    counts = {split: Counter() for split in SPLITS}
    for split, photos in original.items():
        images, labels = (output / kind / split for kind in ('images', 'labels'))
        images.mkdir(parents=True)
        labels.mkdir(parents=True)
        for photo in photos:
            shutil.copyfile(photo, images / photo.name)
            name = photo.with_suffix('.txt').name
            shutil.copyfile(ostrava / 'labels' / split / name, labels / name)
    for split, photo, annotation, objects, _ in reviewed:
        target = f'field-{photo.name}'
        shutil.copyfile(photo, output / 'images' / split / target)
        (output / 'labels' / split / Path(target).with_suffix('.txt').name).write_text(annotation, encoding='utf-8')
        counts[split].update(images=1, boxes=objects, clean=int(objects == 0))
    result = {**info, 'modality': 'rgb', 'fieldReviewSha256': file_hash(review), 'field': counts,
              'missingTrainLabels': [], 'missingValLabels': []}
    for split, added in counts.items():
        result['images'][split] += added['images']
        result['counts'][split]['corrosion'] += added['boxes']
        result['emptyImages'][split] += added['clean']
    (output / 'coverage.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ostrava', type=Path)
    parser.add_argument('source', type=Path)
    parser.add_argument('review', type=Path)
    parser.add_argument('external_manifest', type=Path)
    parser.add_argument('output', type=Path)
    print(json.dumps(prepare(**vars(parser.parse_args()))))
