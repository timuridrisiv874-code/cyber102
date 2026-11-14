"""Copy the original S2DS splits with visually verified cross-split pairs removed."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil


SPLITS = ('train', 'val', 'test')
# Test photo first, followed by its near duplicate in the original training folder.
DUPLICATES = {'s2ds-710': 's2ds-555', 's2ds-689': 's2ds-174',
              's2ds-686': 's2ds-539', 's2ds-742': 's2ds-480'}


def prepare(source, output, image_source):
    info = json.loads((source / 'coverage.json').read_bytes())
    classes = info['classes']
    excluded = {'train': set(DUPLICATES.values()), 'val': set(), 'test': set(DUPLICATES)}
    for split in SPLITS:
        if any(not (image_source / 'images' / split / (name + '.jpg')).is_file()
               or not (source / 'labels' / split / (name + '.txt')).is_file() for name in excluded[split]):
            raise ValueError(f'Missing reviewed duplicate in {split}')
    output.mkdir(parents=True, exist_ok=False)
    images, counts, observed = {}, {}, set()
    for split in SPLITS:
        photo_dir, labels_dir = (output / name / split for name in ('images', 'labels'))
        for path in (photo_dir, labels_dir):
            path.mkdir(parents=True)
        photos = sorted((image_source / 'images' / split).glob('*.jpg'))
        if len(photos) != info['images'][split]:
            raise ValueError(f'Inconsistent source image count in {split}')
        counts[split] = Counter()
        for photo in photos:
            if photo.stem in excluded[split]:
                continue
            digest = hashlib.sha256(photo.read_bytes()).digest()
            if digest in observed:
                raise ValueError(f'Duplicate image after split cleaning: {photo.name}')
            observed.add(digest)
            annotation = source / 'labels' / split / (photo.stem + '.txt')
            rows = annotation.read_text(encoding='utf-8').splitlines()
            counts[split].update(classes[int(row.split()[0])] for row in rows if row.strip())
            shutil.copyfile(photo, photo_dir / photo.name)
            shutil.copyfile(annotation, labels_dir / annotation.name)
        images[split] = len(photos) - len(excluded[split])
    report = {**info, 'images': images, 'counts': counts, 'excludedNearDuplicates': DUPLICATES,
              'missingTrainLabels': [label for label in classes if not counts['train'][label]],
              'missingValLabels': [label for label in classes if not counts['val'][label]]}
    (output / 'coverage.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--image-source', type=Path, help='Original images for a labels-only export')
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.output, args.image_source or args.source)))
