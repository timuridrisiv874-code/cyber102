"""Convert SteelTube VOC annotations to grouped YOLO radiograph data."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import xml.etree.ElementTree as ET
from zipfile import ZipFile


LABELS = {
    'air-hole': 'weld_porosity',
    'hollow-bead': 'weld_porosity',
    'bite-edge': 'weld_undercut',
    'overlap': 'weld_overlap',
    'crack': 'weld_crack',
    'slag-inclusion': 'weld_slag',
    'unfused': 'weld_lack_of_fusion',
    'broken-arc': 'weld_broken_arc',
}
CLASSES = tuple(dict.fromkeys(LABELS.values()))


def read_voc(archive):
    groups = defaultdict(list)
    for path in sorted(archive.namelist()):
        if '/Annotations/' not in path or not path.endswith('.xml'):
            continue
        xml = ET.fromstring(archive.read(path))
        name = Path(path).stem
        width, height = (int(xml.findtext(f'size/{axis}')) for axis in ('width', 'height'))
        boxes = []
        for item in xml.findall('object'):
            label = LABELS[item.findtext('name')]
            x, y, right, bottom = (int(item.findtext(f'bndbox/{key}'))
                                   for key in ('xmin', 'ymin', 'xmax', 'ymax'))
            x, y, right, bottom = max(0, x), max(0, y), min(width, right), min(height, bottom)
            if x < right and y < bottom:
                coords = ((x + right) / (2 * width), (y + bottom) / (2 * height),
                          (right - x) / width, (bottom - y) / height)
                boxes.append((label, *coords))
        image = str(PurePosixPath(path).parent.parent / 'JPEGImages' / f'{name}.jpg')
        # The release uses suffixes -000, -001, ... for variants from one source.
        source = re.sub(r'-[0-9]+$', '', name)
        groups[source].append((image, name, boxes))
    return groups


def partition(groups):
    def stable_order(source):
        return hashlib.sha256(f'cyber102/{source}'.encode()).hexdigest()

    strata = defaultdict(list)
    for source, frames in groups.items():
        counts = Counter(box[0] for _, _, boxes in frames for box in boxes)
        strata[counts.most_common(1)[0][0] if counts else None].append(source)
    for items in strata.values():
        ordered = sorted(items, key=stable_order)
        size = len(ordered)
        holdout = max(1, round(size * .15))
        for index, source in enumerate(ordered):
            split = 'test' if size >= 3 and index < holdout else 'val' if (
                size >= 3 and index < 2 * holdout or size == 2 and index == 0) else 'train'
            yield split, source, groups[source]


def merge_duplicate_sources(archive, groups):
    """Join source groups when the release shares exact image bytes."""
    parents = {source: source for source in groups}

    def representative(source):
        while source != parents[source]:
            source = parents[source]
        return source

    seen = {}
    for source, frames in groups.items():
        for image, _, _ in frames:
            digest = hashlib.sha256(archive.read(image)).digest()
            previous = seen.setdefault(digest, source)
            left, right = sorted((representative(source), representative(previous)))
            parents[right] = left
    merged = defaultdict(list)
    for source, frames in groups.items():
        merged[representative(source)].extend(frames)
    return merged


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--max-variants', type=int, default=48)
    args = parser.parse_args()
    if args.max_variants < 1:
        parser.error('--max-variants must be positive')
    args.output.mkdir(parents=True, exist_ok=False)
    counts = {split: Counter() for split in ('train', 'val', 'test')}
    sources = {split: [] for split in counts}
    seen = {}
    with ZipFile(args.archive) as archive:
        groups = merge_duplicate_sources(archive, read_voc(archive))
        for split, source, frames in partition(groups):
            sources[split].append(source)
            sample = min(len(frames), args.max_variants)
            for index in range(sample):
                image, name, boxes = frames[index * len(frames) // sample]
                data = archive.read(image)
                digest = hashlib.sha256(data).hexdigest()
                if digest in seen:
                    if seen[digest] != split:
                        raise ValueError(f'Duplicate image spans splits: {name}')
                    continue
                seen[digest] = split
                image_folder = args.output / 'images' / split
                label_folder = args.output / 'labels' / split
                image_folder.mkdir(parents=True, exist_ok=True)
                label_folder.mkdir(parents=True, exist_ok=True)
                (image_folder / f'{name}.jpg').write_bytes(data)
                (label_folder / f'{name}.txt').write_text(''.join(
                    f'{CLASSES.index(label)} {x:.6f} {y:.6f} {w:.6f} {h:.6f}\n'
                    for label, x, y, w, h in boxes), encoding='utf-8')
                counts[split].update(box[0] for box in boxes)
    coverage = {'classes': list(CLASSES), 'modality': 'xray',
                'counts': {split: {name: counter[name] for name in CLASSES} for split, counter in counts.items()},
                'sourceGroups': sources,
                'missingTrainLabels': [name for name in CLASSES if not counts['train'][name]],
                'missingValLabels': [name for name in CLASSES if not counts['val'][name]]}
    (args.output / 'coverage.json').write_text(json.dumps(coverage, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(coverage['counts']))


if __name__ == '__main__':
    main()
