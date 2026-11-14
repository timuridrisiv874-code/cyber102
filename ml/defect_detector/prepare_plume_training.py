"""Add a site-disjoint steam development set to a D-Fire rehearsal subset."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil

from PIL import Image, ImageEnhance, ImageFilter

from prepare_dfire import CLASSES as DFIRE_CLASSES, STRATA, clip_box


CLASSES = (*DFIRE_CLASSES, 'steam')
SPLITS = ('train', 'val', 'test')
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png'}


def dfire_examples(dataset, per_stratum):
    for split in SPLITS:
        counts = Counter()
        images = dataset / 'images' / split
        for image in sorted(images.iterdir(), key=lambda path: hashlib.sha256(path.name.encode()).digest()):
            if image.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            labels = (dataset / 'labels' / split / image.with_suffix('.txt').name).read_text(encoding='utf-8')
            ids = set()
            for row in labels.splitlines():
                index, *coordinates = row.split()
                if index not in ('0', '1') or len(coordinates) != 4 or clip_box(*map(float, coordinates)) is None:
                    raise ValueError(f'Invalid D-Fire annotation for {image}')
                ids.add(int(index))
            stratum = STRATA[sum(1 << index for index in ids)]
            if split != 'train' or counts[stratum] < per_stratum:
                counts[stratum] += 1
                yield split, image, labels, stratum
        expected = per_stratum if split == 'train' else 1
        if any(counts[stratum] < expected for stratum in STRATA):
            raise ValueError(f'Insufficient D-Fire {split} data: {dict(counts)}')


def steam_examples(manifest, assets, holdout):
    controls = json.loads(holdout.read_text(encoding='utf-8'))
    seen_scenes = {row['scene'] for row in controls}
    seen_hashes = {row[key] for row in controls for key in ('sha256', 'sourceSha256')}
    for row in json.loads(manifest.read_text(encoding='utf-8')):
        split, scene, filename = row['split'], row['scene'], row['file']
        if split not in ('train', 'val') or filename != Path(filename).name:
            raise ValueError(f'Invalid steam entry: {filename}')
        if scene in seen_scenes or {row['sha256'], row['sourceSha256']} & seen_hashes:
            raise ValueError(f'Repeated steam scene or image: {scene}')
        seen_scenes.add(scene)
        seen_hashes.update((row['sha256'], row['sourceSha256']))
        path = assets / filename
        if hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']:
            raise ValueError(f'Steam image checksum mismatch: {filename}')
        labels = []
        for obj in row['objects']:
            left, top, right, bottom = obj['xyxy']
            if obj['class'] != 'steam' or not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
                raise ValueError(f'Invalid steam box: {filename}')
            values = ((left + right) / 2, (top + bottom) / 2, right - left, bottom - top)
            labels.append('2 ' + ' '.join(f'{v:.8f}' for v in values) + '\n')
        yield split, path, ''.join(labels), scene


def steam_variants(image):
    bright = ImageEnhance.Brightness(image)
    contrast = ImageEnhance.Contrast(image)
    return {
        'bright': bright.enhance(1.20),
        'dark': bright.enhance(.78),
        'lowcontrast': contrast.enhance(.75),
        'highcontrast': contrast.enhance(1.25),
        'desaturated': ImageEnhance.Color(image).enhance(.35),
        'soft': image.filter(ImageFilter.GaussianBlur(1.4)),
        'haze': Image.blend(image, Image.new('RGB', image.size, (194, 198, 202)), .17),
    }


def build(dfire, manifest, assets, holdout, output, per_stratum):
    source_config = json.loads((dfire / 'coverage.json').read_text(encoding='utf-8'))
    if source_config['classes'] != list(DFIRE_CLASSES):
        raise ValueError('D-Fire label IDs do not match the expected class order')
    steam = list(steam_examples(manifest, assets, holdout))
    if {split for split, _, labels, _ in steam if labels} != {'train', 'val'}:
        raise ValueError('Steam positives are required in training and validation')
    output.mkdir(parents=True, exist_ok=False)
    for split in SPLITS:
        for kind in ('images', 'labels'):
            (output / kind / split).mkdir(parents=True)
    counts = {split: Counter() for split in SPLITS}
    steam_sites = {split: [] for split in ('train', 'val')}
    seen = set()

    def write(split, image, labels, stratum):
        digest = hashlib.sha256(image.read_bytes()).digest()
        destination = output / 'images' / split / image.name
        if digest in seen or destination.exists():
            raise ValueError(f'Duplicate dataset image: {image}')
        seen.add(digest)
        shutil.copyfile(image, destination)
        (output / 'labels' / split / image.with_suffix('.txt').name).write_text(labels, encoding='utf-8')
        counts[split][stratum] += 1

    for split, image, labels, stratum in dfire_examples(dfire, per_stratum):
        write(split, image, labels, 'dfire_' + stratum)
    for split, image, labels, scene in steam:
        write(split, image, labels, 'steam' if labels else 'steam_clear')
        steam_sites[split].append(scene)
        if split == 'train':
            with Image.open(image) as original:
                for variant, frame in steam_variants(original.convert('RGB')).items():
                    path = output / 'images' / split / f'{image.stem}-{variant}.jpg'
                    frame.save(path, quality=91)
                    digest = hashlib.sha256(path.read_bytes()).digest()
                    if digest in seen:
                        raise ValueError(f'Duplicate transformed image: {path}')
                    seen.add(digest)
                    (output / 'labels' / split / path.with_suffix('.txt').name).write_text(labels, encoding='utf-8')
                    counts[split]['steam_variant'] += 1

    report = {'classes': CLASSES, 'modality': 'rgb', 'imageCountsByStratum': counts,
              'steamSites': steam_sites, 'missingTrainLabels': [], 'missingValLabels': [],
              'testScope': 'Original D-Fire test (smoke/flame). Evaluate steam on the separate controls.'}
    (output / 'coverage.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dfire', type=Path)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('assets', type=Path)
    parser.add_argument('holdout', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--dfire-train-per-stratum', type=int, default=36)
    args = parser.parse_args()
    if args.dfire_train_per_stratum < 1:
        parser.error('--dfire-train-per-stratum must be positive')
    print(json.dumps(build(args.dfire, args.manifest, args.assets, args.holdout, args.output,
                           args.dfire_train_per_stratum)))
