"""Create a balanced YOLO subset from the original D-Fire train and test archives."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
from tarfile import open as open_tar


CLASSES = ('smoke', 'flame')
STRATA = ('clear', 'smoke', 'flame', 'both')


def clip_box(x, y, width, height):
    if width <= 0 or height <= 0 or not all(map(math.isfinite, (x, y, width, height))):
        return None
    left, top = max(0, x - width / 2), max(0, y - height / 2)
    right, bottom = min(1, x + width / 2), min(1, y + height / 2)
    if left >= right or top >= bottom:
        return None
    return (left + right) / 2, (top + bottom) / 2, right - left, bottom - top


def read_index(archive):
    images, labels = {}, {}
    for member in archive.getmembers():
        path = PurePosixPath(member.name)
        # Use the web portion, excluding related video captures and third-party
        # public datasets (see D-Fire's source attribution).
        if not member.isfile() or not path.stem.upper().startswith('WEB'):
            continue
        if path.parent.name == 'images' and path.suffix.lower() in ('.jpeg', '.jpg', '.png'):
            images[path.stem] = member
        if path.parent.name == 'labels' and path.suffix.lower() == '.txt':
            labels[path.stem] = member

    strata = defaultdict(list)
    rejected = 0
    for name, member in labels.items():
        if name not in images:
            continue
        raw = archive.extractfile(member).read().decode('utf-8').strip()
        ids, boxes = set(), []
        for row in raw.splitlines():
            if not row.strip():
                continue
            label, *coordinates = row.split()
            box = clip_box(*map(float, coordinates))
            if label not in ('0', '1') or box is None:
                rejected += 1
                break
            ids.add(int(label))
            boxes.append(label + ' ' + ' '.join(f'{value:.8f}' for value in box))
        else:
            stratum = STRATA[sum(1 << idx for idx in ids)]
            strata[stratum].append((name, images[name], '\n'.join(boxes)))
    return strata, rejected


def sample(archive, output, splits, seen):
    strata, rejected = read_index(archive)
    counts = {split: Counter() for split in splits}
    for stratum in STRATA:
        ordered = sorted(strata[stratum], key=lambda item: hashlib.sha256(item[0].encode()).digest())
        used = 0
        for split, target in splits.items():
            for name, member, labels in ordered[used:]:
                used += 1
                raw = archive.extractfile(member).read()
                digest = hashlib.sha256(raw).digest()
                if digest in seen:
                    continue
                seen.add(digest)
                suffix = PurePosixPath(member.name).suffix.lower()
                (output / 'images' / split / f'dfire-{name}{suffix}').write_bytes(raw)
                (output / 'labels' / split / f'dfire-{name}.txt').write_text(labels + '\n' if labels else '',
                                                                            encoding='utf-8')
                counts[split][stratum] += 1
                if counts[split][stratum] >= target:
                    break
            if counts[split][stratum] < target:
                raise ValueError(f'{split}/{stratum}: found {counts[split][stratum]}, expected {target}')
    return counts, rejected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('train_archive', type=Path)
    parser.add_argument('test_archive', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--train-per-stratum', type=int, default=300)
    parser.add_argument('--holdout-per-stratum', type=int, default=50)
    args = parser.parse_args()
    if min(args.train_per_stratum, args.holdout_per_stratum) < 1:
        parser.error('sample sizes must be positive')
    args.output.mkdir(parents=True, exist_ok=False)
    for split in ('train', 'val', 'test'):
        for kind in ('images', 'labels'):
            (args.output / kind / split).mkdir(parents=True)

    seen = set()
    with open_tar(args.test_archive) as archive:
        counts, rejected_test = sample(archive, args.output, {'test': args.holdout_per_stratum}, seen)
    with open_tar(args.train_archive) as archive:
        training, rejected_train = sample(archive, args.output, {'val': args.holdout_per_stratum,
                                                                'train': args.train_per_stratum}, seen)
        counts.update(training)
    coverage = {'classes': list(CLASSES), 'modality': 'rgb',
                'rejectedSourceFrames': {'train': rejected_train, 'test': rejected_test},
                'imageCountsByStratum': counts, 'missingTrainLabels': [], 'missingValLabels': []}
    (args.output / 'coverage.json').write_text(json.dumps(coverage, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(coverage))


if __name__ == '__main__':
    main()
