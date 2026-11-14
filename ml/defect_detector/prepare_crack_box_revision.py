"""Replace audited crack boxes in existing YOLO splits without changing images."""
import argparse
import json
from pathlib import Path
import shutil

from evaluate import overlap
from prepare_core_rgb import SPLITS
from prepare_corrosion_field import file_hash


def normalized_box(box):
    left, top, right, bottom = box
    if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
        raise ValueError(f'Invalid crack box: {box}')
    return ((left + right) / 2, (top + bottom) / 2, right - left, bottom - top)


def corrected_labels(lines, crack_index, prior, replacement):
    matches = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        label, x, y, width, height = map(float, line.split())
        box = (x - width / 2, y - height / 2, x + width / 2, y + height / 2)
        if label == crack_index and all(abs(a - b) <= .0006 for a, b in zip(box, prior, strict=True)):
            matches.append(index)
    if len(matches) != 1:
        raise ValueError('The baseline split does not contain exactly one matching box')
    lines[matches[0]] = f'{crack_index} ' + ' '.join(f'{coordinate:.8f}' for coordinate in replacement)
    return '\n'.join(lines) + '\n'


def prepare(base, review, output):
    coverage = json.loads((base / 'coverage.json').read_bytes())
    classes = coverage['classes']
    crack_index = classes.index('crack')
    labels_by_path = {}
    revisions = []
    for row in json.loads(review.read_bytes()):
        source = row['knownDatasetCopies']
        if base.name not in source['datasets']:
            raise ValueError(f'Unreviewed source dataset: {base.name}')
        prior = source.get('previousCrackBox')
        if prior is None:
            continue
        filename, split = source['file'], source['split']
        if Path(filename).name != filename or split not in SPLITS[:2]:
            raise ValueError(f'Invalid correction target: {filename}')
        relative = Path('labels') / split / Path(filename).with_suffix('.txt')
        if relative in labels_by_path or not (base / 'images' / split / filename).is_file():
            raise ValueError(f'Duplicate or absent correction target: {filename}')
        (obj,) = row['objects']
        if obj['class'] != 'crack':
            raise ValueError(f'Invalid annotation class: {obj["class"]}')
        box = obj['xyxy']
        replacement = normalized_box(box)
        original = (base / relative).read_text(encoding='utf-8')
        revised = corrected_labels(original.splitlines(), crack_index, prior, replacement)
        labels_by_path[relative] = revised
        revisions.append({'split': split, 'file': filename, 'oldNewIoU': round(overlap(prior, box), 4)})
    if not revisions:
        raise ValueError('No audited crack boxes to revise')

    output.mkdir(parents=True, exist_ok=False)
    for kind in ('images', 'labels'):
        for split in SPLITS:
            shutil.copytree(base / kind / split, output / kind / split, ignore=shutil.ignore_patterns('.*'))
    for relative, updated in labels_by_path.items():
        (output / relative).write_text(updated, encoding='utf-8')
    result = {**coverage, 'boxReviewSha256': file_hash(review), 'boxCorrections': revisions}
    (output / 'coverage.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base', type=Path)
    parser.add_argument('review', type=Path)
    parser.add_argument('output', type=Path)
    print(json.dumps(prepare(**vars(parser.parse_args()))))
