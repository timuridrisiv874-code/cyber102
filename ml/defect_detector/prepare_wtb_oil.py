"""Export WTBs2025 oil annotations, keeping views of the same blade together."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

from prepare_dfire import clip_box


def group_for(path):
    identity = path.name.split('_jpg.rf.', 1)[0].split('_', 1)[0]
    if path.parts[0] == 'oil leakage':
        return 'blade-' + identity
    return 'stain-' + str(int(identity) // 25)


def partition_groups(groups):
    strata = defaultdict(list)
    for group in groups:
        strata[group.split('-', 1)[0]].append(group)
    for items in strata.values():
        items.sort(key=lambda value: hashlib.sha256(value.encode()).digest())
        reserve = max(1, round(len(items) * .17))
        for index, group in enumerate(items):
            split = 'test' if index < reserve else 'val' if index < 2 * reserve else 'train'
            yield split, group


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--views-per-photo', type=int, default=6)
    args = parser.parse_args()
    if args.views_per_photo < 1:
        parser.error('--views-per-photo must be positive')
    args.output.mkdir(parents=True)
    splits = ('train', 'val', 'test')
    for split in splits:
        for category in ('images', 'labels'):
            (args.output / category / split).mkdir(parents=True)
    counts, groups_used = defaultdict(Counter), defaultdict(list)
    seen = {}
    with ZipFile(args.archive) as archive:
        groups = defaultdict(lambda: defaultdict(list))
        for item in sorted(archive.namelist()):
            path = PurePosixPath(item)
            if (len(path.parts) == 3 and path.parts[:2] in (('oil leakage', 'images'), ('surface stains', 'images'))
                    and path.suffix == '.jpg'):
                source = path.name.split('_jpg.rf.', 1)[0]
                groups[group_for(path)][source].append(path)
        for split, group in partition_groups(groups):
            groups_used[split].append(group)
            for pictures in groups[group].values():
                limit = args.views_per_photo if split == 'train' else 1
                for path in pictures[:limit]:
                    labels = archive.read(str(path.parent.parent / 'labels' / path.with_suffix('.txt').name))
                    boxes = []
                    for line in labels.decode().splitlines():
                        if not line.strip():
                            continue
                        label, *coords = line.split()
                        if label == '0':
                            box = clip_box(*map(float, coords))
                            if box:
                                boxes.append(box)
                    if group.startswith('blade-') and not boxes:
                        continue
                    raw = archive.read(str(path))
                    digest = hashlib.sha256(raw).hexdigest()
                    if digest in seen:
                        if seen[digest] != split:
                            raise ValueError(f'Duplicate image spans groups in different splits: {path}')
                        continue
                    seen[digest] = split
                    stem = f'wtb-{digest[:20]}'
                    (args.output / 'images' / split / (stem + '.jpg')).write_bytes(raw)
                    (args.output / 'labels' / split / (stem + '.txt')).write_text(''.join(
                        '0 ' + ' '.join(f'{value:.8f}' for value in b) + '\n' for b in boxes), encoding='utf-8')
                    counts[split]['positive' if boxes else 'hard_negative'] += 1
                    counts[split]['boxes'] += len(boxes)
    report = {'classes': ['oil_leak'], 'modality': 'rgb', 'counts': counts, 'groups': groups_used,
              'missingTrainLabels': [] if counts['train']['positive'] else ['oil_leak'],
              'missingValLabels': [] if counts['val']['positive'] else ['oil_leak']}
    (args.output / 'coverage.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
