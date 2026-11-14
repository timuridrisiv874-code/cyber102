"""Prepare a metal-crack experiment with visually reviewed field examples."""
import argparse
from collections import Counter
import hashlib
from io import BytesIO
import json
from pathlib import Path

from PIL import Image, ImageEnhance

from prepare_core_rgb import CLASSES, SPLITS, read_labels


# Keep corrosion and the other original classes where those annotations remain.
DROP_UNSUPPORTED_CRACK = {'case-14', 'case-23', 'case-48'}
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}


def core_examples(root, limit):
    for split in SPLITS:
        images = root / 'images' / split
        steel = 0
        for image in sorted(images.iterdir(), key=lambda p: hashlib.sha256(p.name.encode()).digest()):
            if image.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            label_file = root / 'labels' / split / image.with_suffix('.txt').name
            labels = read_labels(label_file, CLASSES)
            if split == 'train' and image.name.startswith('steelcrack-'):
                if steel >= limit:
                    continue
                steel += 1
            if split == 'train' and image.stem in DROP_UNSUPPORTED_CRACK:
                labels = [(label, box) for label, box in labels if label != 'crack']
            yield split, image, labels


def field_examples(manifest, folder, known):
    scenes = set()
    for row in json.loads(manifest.read_text(encoding='utf-8')):
        scene, filename, split = row['scene'], row['file'], row['split']
        if scene in scenes or Path(filename).name != filename or split not in ('train', 'val'):
            raise ValueError(f'Invalid field example: {filename}')
        scenes.add(scene)
        image = folder / filename
        raw = image.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != row['sha256'] or digest in known or row['sourceSha256'] in known:
            raise ValueError(f'Checksum mismatch or duplicate field image: {filename}')
        known.update((digest, row['sourceSha256']))
        labels = []
        for obj in row['objects']:
            left, top, right, bottom = obj['xyxy']
            if obj['class'] != 'crack' or not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
                raise ValueError(f'Invalid crack annotation: {filename}')
            labels.append(('crack', ((left + right) / 2, (top + bottom) / 2, right - left, bottom - top)))
        if not labels:
            raise ValueError(f'Missing crack annotation: {filename}')
        yield split, image, raw, labels


def build(base, manifest, assets, controls, output, limit):
    base_info = json.loads((base / 'coverage.json').read_text(encoding='utf-8'))
    if base_info['classes'] != list(CLASSES):
        raise ValueError('Unexpected class order in the base dataset')
    known = {hashlib.sha256(path.read_bytes()).hexdigest() for root in controls for path in root.iterdir()
             if path.suffix.lower() in IMAGE_EXTENSIONS and path.is_file()}
    original_controls = set(known)
    fields = list(field_examples(manifest, assets, known))
    if {split for split, _, _, _ in fields} != {'train', 'val'}:
        raise ValueError('Field positives are required in training and validation')
    output.mkdir(parents=True, exist_ok=False)
    counts = {split: Counter() for split in SPLITS}
    images = Counter()
    for split in SPLITS:
        (output / 'images' / split).mkdir(parents=True)
        (output / 'labels' / split).mkdir(parents=True)

    stored = set(original_controls)

    def write(split, filename, raw, labels):
        photo = output / 'images' / split / filename
        digest = hashlib.sha256(raw).hexdigest()
        if digest in stored or photo.exists():
            raise ValueError(f'Duplicate image: {filename}')
        stored.add(digest)
        photo.write_bytes(raw)
        label = output / 'labels' / split / photo.with_suffix('.txt').name
        with label.open('x', encoding='utf-8') as stream:
            for name, box in labels:
                stream.write(f'{CLASSES.index(name)} ' + ' '.join(f'{n:.8f}' for n in box) + '\n')
                counts[split][name] += 1
        images[split] += 1

    for split, image, labels in core_examples(base, limit):
        write(split, image.name, image.read_bytes(), labels)
    for split, image, raw, labels in fields:
        write(split, image.name, raw, labels)
        if split == 'train':
            with Image.open(BytesIO(raw)) as loaded:
                frame = loaded.convert('RGB')
            light = ImageEnhance.Brightness(frame)
            contrast = ImageEnhance.Contrast(frame)
            variants = [('light', light.enhance(1.18)), ('lowlight', light.enhance(.78)),
                        ('highcontrast', contrast.enhance(1.18)), ('lowcontrast', contrast.enhance(.77))]
            for tag, variant in variants:
                with BytesIO() as buffer:
                    variant.save(buffer, format='JPEG', quality=93)
                    write(split, f'{image.stem}-{tag}.jpg', buffer.getvalue(), labels)
    report = {'classes': CLASSES, 'modality': 'rgb', 'images': images, 'counts': counts,
              'correctedOriginalTrainingFrames': sorted(DROP_UNSUPPORTED_CRACK),
              'missingTrainLabels': [name for name in CLASSES if not counts['train'][name]],
              'missingValLabels': [name for name in CLASSES if not counts['val'][name]]}
    (output / 'coverage.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base', type=Path)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('assets', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--control', type=Path, action='append', required=True)
    parser.add_argument('--max-steelcrack', type=int, default=50)
    args = parser.parse_args()
    if args.max_steelcrack < 1:
        parser.error('--max-steelcrack must be positive')
    print(json.dumps(build(args.base, args.manifest, args.assets, args.control, args.output,
                           args.max_steelcrack)))
