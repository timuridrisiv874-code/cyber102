"""Add original training photos of cracks to a four-class dataset."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil

import yaml

from prepare_combined_rgb import inventory
from prepare_core_rgb import CLASSES, SPLITS, read_labels


def prepare(base, archive, holdouts, output, limit):
    if limit < 1:
        raise ValueError('Specify a positive Steelcrack image limit')
    old_names = yaml.safe_load((archive / 'config.yaml').read_text(encoding='utf-8'))['names']
    known = {digest for digest, *_ in inventory(base)}
    protected_names = {path.stem for split in SPLITS[1:] for path in (base / 'images' / split).iterdir()}
    for manifest in holdouts:
        for row in json.loads(manifest.read_bytes()):
            known.update(row[key] for key in ('sha256', 'sourceSha256') if key in row)
    weld_names = {'pwdid-11', 'pwdid-14', 'pwdid-23'}
    archived_train = archive / 'images' / 'train'
    candidates = sorted(archived_train.glob('steelcrack-*.jpg'),
                        key=lambda path: sha256(path.name.encode()).digest())
    welds = [archived_train / (name + '.jpg') for name in sorted(weld_names)]
    selected, steel_count = [], 0
    for image in welds + candidates:
        is_weld = image.stem in weld_names
        if not is_weld and steel_count >= limit:
            break
        digest = sha256(image.read_bytes()).hexdigest()
        if digest in known or image.stem in protected_names:
            continue
        labels = read_labels(archive / 'labels' / 'train' / (image.stem + '.txt'), old_names)
        expected = 'weld_crack' if is_weld else 'crack'
        if not labels or any(name != expected for name, _ in labels):
            continue
        if any(not (0 <= x <= 1 and 0 <= y <= 1 and 0 < w <= 1 and 0 < h <= 1)
               for _, (x, y, w, h) in labels):
            raise ValueError(f'Invalid archived box in {image.name}')
        selected.append((image, digest, labels))
        known.add(digest)
        steel_count += not is_weld
    if not selected:
        raise ValueError('No new annotated training photos')

    report = json.loads((base / 'coverage.json').read_bytes())
    report['crackReplay'] = []
    output.mkdir(parents=True, exist_ok=False)
    for directory in ('images', 'labels'):
        shutil.copytree(base / directory, output / directory, ignore=shutil.ignore_patterns('*.cache', '._*'))
    crack = CLASSES.index('crack')
    for source, digest, labels in selected:
        name = f'replay-{source.stem}'
        shutil.copyfile(source, output / 'images' / 'train' / (name + '.jpg'))
        with (output / 'labels' / 'train' / (name + '.txt')).open('x', encoding='utf-8') as file:
            file.writelines(f'{crack} ' + ' '.join(f'{value:.8f}' for value in box) + '\n'
                            for _, box in labels)
        report['crackReplay'].append({'source': source.name, 'sha256': digest})
        report['counts']['train']['crack'] += len(labels)
    report['images']['train'] += len(selected)
    (output / 'coverage.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return {'images': report['images'], 'counts': report['counts'],
            'steelcrack': steel_count, 'pwdid': len(selected) - steel_count}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('base', 'archive', 'output'):
        parser.add_argument(name, type=Path)
    parser.add_argument('--holdouts', nargs='+', type=Path, required=True)
    parser.add_argument('--limit', type=int, default=75)
    print(json.dumps(prepare(**vars(parser.parse_args()))))
