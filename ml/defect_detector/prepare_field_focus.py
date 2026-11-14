"""Keep field photos while limiting the share of flat-steel crack training images."""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import shutil

from prepare_combined_rgb import inventory
from prepare_core_rgb import CLASSES, SPLITS


def prepare(base, output, sheet_limit):
    if sheet_limit < 1:
        raise ValueError('Keep at least one original sheet-steel image')
    coverage = json.loads((base / 'coverage.json').read_bytes())
    items = list(inventory(base))
    sheet_sources = {digest for digest, split, path, _ in items
                     if split == 'train' and path.name.startswith(('steelcrack-', 'replay-steelcrack-'))}
    sheet_crops = {row['file']: row['sourceSha256'] for row in coverage.get('detailCrops', [])
                   if row['sourceSha256'] in sheet_sources}
    kept_sources = set(sorted(sheet_sources, key=lambda digest: sha256(digest.encode()).digest())[:sheet_limit])
    removed_sources = sheet_sources - kept_sources
    sizes = Counter()
    counts = {split: Counter() for split in SPLITS}
    removed = []
    output.mkdir(parents=True, exist_ok=False)
    for split in SPLITS:
        for kind in ('images', 'labels'):
            (output / kind / split).mkdir(parents=True)
    for digest, split, photo, boxes in items:
        source = sheet_crops.get(photo.name, digest)
        if split == 'train' and source in removed_sources:
            removed.append(photo.name)
            continue
        label_file = photo.with_suffix('.txt').name
        shutil.copyfile(photo, output / 'images' / split / photo.name)
        shutil.copyfile(base / 'labels' / split / label_file, output / 'labels' / split / label_file)
        sizes[split] += 1
        counts[split].update(CLASSES[box[0]] for box in boxes)

    coverage['images'] = sizes
    coverage['counts'] = counts
    coverage['fieldFocus'] = {'keptSheetSources': len(kept_sources), 'excludedTrainImages': removed}
    (output / 'coverage.json').write_text(json.dumps(coverage, indent=2) + '\n', encoding='utf-8')
    return {'images': sizes, 'counts': counts, 'keptSheetSources': len(kept_sources),
            'excludedTrainImages': len(removed)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--sheet-limit', type=int, default=24)
    print(json.dumps(prepare(**vars(parser.parse_args()))))
