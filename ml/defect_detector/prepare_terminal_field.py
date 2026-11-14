"""Build the single-class terminal detector dataset from reviewed source frames."""
import argparse
from collections import Counter
import json
from pathlib import Path
import shutil

from prepare_corrosion_field import file_hash


SPLITS = ('train', 'val')
LABEL = 'terminal_damage'


def read_rows(review, core, incidents, holdouts):
    rows = json.loads(review.read_bytes())
    excluded = {row[key] for path in holdouts for row in json.loads(path.read_bytes())
                for key in ('sha256', 'sourceSha256') if key in row}
    seen, scenes, filenames = set(), {}, set()
    for row in rows:
        name, split, scene = row['file'], row['split'], row['scene']
        if Path(name).name != name or Path(name).suffix.lower() != '.jpg' or name in filenames:
            raise ValueError(f'Invalid or duplicate image filename: {name}')
        if split not in SPLITS or not scene or scenes.setdefault(scene, split) != split:
            raise ValueError(f'Invalid split or scene appears in multiple splits: {scene}')
        roots = {'core': core / 'images' / split, 'incidents': incidents}
        image = roots[row['input']] / name
        digest = file_hash(image)
        hashes = {digest, row.get('sourceSha256', digest)}
        if digest != row['sha256'] or hashes & (seen | excluded):
            raise ValueError(f'Invalid or duplicated image: {name}')
        seen.update(hashes)
        filenames.add(name)
        labels = []
        for obj in row['objects']:
            left, top, right, bottom = obj['xyxy']
            if obj['class'] != LABEL or not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
                raise ValueError(f'Invalid reviewed annotation: {name}')
            values = ((left + right) / 2, (top + bottom) / 2, right - left, bottom - top)
            labels.append('0 ' + ' '.join(f'{value:.8f}' for value in values) + '\n')
        yield split, image, labels


def prepare(review, core, incidents, holdouts, output):
    rows = list(read_rows(review, core, incidents, holdouts))
    images, boxes, controls = Counter(), Counter(), Counter()
    for split, _, labels in rows:
        images[split] += 1
        boxes[split] += len(labels)
        controls[split] += not labels
    if not all(boxes[split] and controls[split] for split in SPLITS):
        raise ValueError('Train and validation both need positives and hard negatives')
    output.mkdir(parents=True, exist_ok=False)
    for split in SPLITS:
        (output / 'images' / split).mkdir(parents=True)
        (output / 'labels' / split).mkdir(parents=True)
    for split, image, labels in rows:
        shutil.copyfile(image, output / 'images' / split / image.name)
        (output / 'labels' / split / image.with_suffix('.txt').name).write_text(
            ''.join(labels), encoding='utf-8')
    report = {'classes': [LABEL], 'annotationFormat': 'boxes', 'modality': 'rgb',
              'images': images, 'counts': {split: {LABEL: boxes[split]} for split in SPLITS},
              'emptyImages': controls, 'reviewSha256': file_hash(review),
              'missingTrainLabels': [], 'missingValLabels': []}
    (output / 'coverage.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('review', type=Path)
    parser.add_argument('core', type=Path)
    parser.add_argument('incidents', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--holdouts', nargs='+', type=Path, required=True)
    print(json.dumps(prepare(**vars(parser.parse_args()))))
