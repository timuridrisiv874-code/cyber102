"""Build oil-leak rehearsal splits and add reviewed field photographs."""
import argparse
from collections import Counter
import hashlib
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import shutil

from PIL import Image, ImageEnhance

from prepare_dfire import clip_box
from prepare_plume_training import IMAGE_EXTENSIONS, SPLITS


CLASSES = ('oil_leak',)


def source_group(row):
    member = row.get('archiveMember')
    if member:
        return PurePosixPath(member).name.split('.rf.', 1)[0]
    return None


def field_examples(review, assets, holdout):
    controls = json.loads(holdout.read_text(encoding='utf-8'))
    seen_groups = {source_group(row) for row in controls}
    seen_scenes = {row['scene'] for row in controls}
    for row in json.loads(review.read_text(encoding='utf-8')):
        split, name, scene = row['split'], row['file'], row['scene']
        group = source_group(row)
        if split not in ('train', 'val') or not group or name != Path(name).name:
            raise ValueError(f'Invalid field image entry: {name}')
        if scene in seen_scenes or group in seen_groups:
            raise ValueError(f'Repeated field scene or source image: {name}')
        seen_scenes.add(scene)
        seen_groups.add(group)
        image = assets / name
        if hashlib.sha256(image.read_bytes()).hexdigest() != row['sha256']:
            raise ValueError(f'Checksum mismatch: {name}')
        labels = []
        for obj in row['objects']:
            left, top, right, bottom = obj['xyxy']
            if obj['class'] != CLASSES[0] or not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
                raise ValueError(f'Invalid oil annotation: {name}')
            box = ((left + right) / 2, (top + bottom) / 2, right - left, bottom - top)
            labels.append('0 ' + ' '.join(f'{value:.8f}' for value in box) + '\n')
        yield split, image, ''.join(labels)


def rehearsal_examples(dataset, train_limit):
    for split in SPLITS:
        counts = Counter()
        images = dataset / 'images' / split
        for image in sorted(images.iterdir(), key=lambda p: hashlib.sha256(p.name.encode()).digest()):
            if image.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            labels = (dataset / 'labels' / split / image.with_suffix('.txt').name).read_text(encoding='utf-8')
            for line in labels.splitlines():
                index, *coords = line.split()
                if index != '0' or len(coords) != 4 or clip_box(*map(float, coords)) is None:
                    raise ValueError(f'Invalid rehearsal annotation for {image}')
            kind = 'positive' if labels.strip() else 'negative'
            if split != 'train' or counts[kind] < train_limit:
                counts[kind] += 1
                yield split, image, labels, 'rehearsal_' + kind


def build(dataset, review, assets, holdout, output, train_limit):
    source = json.loads((dataset / 'coverage.json').read_text(encoding='utf-8'))
    if source['classes'] != list(CLASSES):
        raise ValueError('Expected a single oil-leak class in the rehearsal dataset')
    examples = list(field_examples(review, assets, holdout))
    if {split for split, _, labels in examples if labels} != {'train', 'val'}:
        raise ValueError('Field positives are required in training and validation')
    seen = {row['sha256'] for row in json.loads(holdout.read_text(encoding='utf-8'))}
    output.mkdir(parents=True, exist_ok=False)
    counts = {split: Counter() for split in SPLITS}
    for split in SPLITS:
        for kind in ('images', 'labels'):
            (output / kind / split).mkdir(parents=True)

    def write(split, name, raw, labels, kind):
        image = output / 'images' / split / name
        digest = hashlib.sha256(raw).hexdigest()
        if digest in seen or image.exists():
            raise ValueError(f'Duplicate dataset image: {name}')
        seen.add(digest)
        image.write_bytes(raw)
        (output / 'labels' / split / image.with_suffix('.txt').name).write_text(labels, encoding='utf-8')
        counts[split][kind] += 1

    for split, image, labels, kind in rehearsal_examples(dataset, train_limit):
        write(split, image.name, image.read_bytes(), labels, kind)
    for split, image, labels in examples:
        write(split, image.name, image.read_bytes(), labels, 'field_positive' if labels else 'field_negative')
        if split == 'train' and labels:
            with Image.open(image) as original:
                image_rgb = original.convert('RGB')
            light, contrast = ImageEnhance.Brightness(image_rgb), ImageEnhance.Contrast(image_rgb)
            for tag, variant in (('dark', light.enhance(.78)), ('light', light.enhance(1.16)),
                                 ('soft', contrast.enhance(.76)), ('contrast', contrast.enhance(1.25))):
                with BytesIO() as buffer:
                    variant.save(buffer, format='JPEG', quality=91)
                    write(split, f'{image.stem}-{tag}.jpg', buffer.getvalue(), labels, 'field_variant')
    report = {'classes': CLASSES, 'modality': 'rgb', 'counts': counts,
              'missingTrainLabels': [], 'missingValLabels': [],
              'testScope': 'Original WTBs2025 test. Use the frozen field benchmark separately.'}
    (output / 'coverage.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dataset', type=Path)
    parser.add_argument('review', type=Path)
    parser.add_argument('assets', type=Path)
    parser.add_argument('holdout', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--rehearsal-per-stratum', type=int, default=48)
    args = parser.parse_args()
    if args.rehearsal_per_stratum < 1:
        parser.error('Rehearsal limit must be positive')
    print(json.dumps(build(args.dataset, args.review, args.assets, args.holdout, args.output,
                           args.rehearsal_per_stratum)))
