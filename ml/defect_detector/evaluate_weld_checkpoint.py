"""Compare radiograph inference sizes on one representative image per source."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import statistics

from calibrate_thresholds import apply, fit
from evaluate import infer, measurements, merge_boxes, scene_coverage


def representative_frames(dataset, split, views_per_source=1):
    coverage = json.loads((dataset / 'coverage.json').read_text(encoding='utf-8'))
    if coverage.get('modality') != 'xray':
        raise ValueError('Expected an X-ray dataset')
    names = coverage['classes']
    by_source = defaultdict(list)
    for image in sorted((dataset / 'images' / split).glob('*.jpg')):
        source = re.sub(r'-[0-9]+$', '', image.stem)
        by_source[source].append(image)
    if set(by_source) != set(coverage['sourceGroups'][split]):
        raise ValueError(f'Image filenames do not match the declared source groups in {split}')
    rows, images = [], []
    for source, views in sorted(by_source.items()):
        count = min(views_per_source, len(views))
        for index in range(count):
            image = views[index * len(views) // count]
            path = dataset / 'labels' / split / image.with_suffix('.txt').name
            objects = []
            for line in path.read_text(encoding='utf-8').splitlines():
                if line.strip():
                    label, x, y, w, h = line.split()
                    x, y, w, h = map(float, (x, y, w, h))
                    objects.append({'class': names[int(label)],
                                    'xyxy': [x - w / 2, y - h / 2, x + w / 2, y + h / 2]})
            rows.append({'file': image.name, 'scene': source, 'objects': objects})
            images.append(image)
    if not images:
        raise ValueError(f'No radiographs in {split}')
    return rows, images, names


def infer_weld(model, images, device, size, preprocessing):
    if preprocessing == 'original':
        return infer(model, images, device, size)
    from PIL import ImageOps

    def contrast(photo):
        return ImageOps.autocontrast(photo.convert('L'), cutoff=1).convert('RGB')

    predictions, timings = infer(model, images, device, size, preprocess=contrast)
    if preprocessing == 'combined':
        original, original_times = infer(model, images, device, size)
        predictions = {name: merge_boxes(original[name] + boxes) for name, boxes in predictions.items()}
        timings = {name: original_times[name] + elapsed for name, elapsed in timings.items()}
    return predictions, timings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('weights', type=Path)
    parser.add_argument('dataset', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--split', choices=('val', 'test'), default='val')
    parser.add_argument('--sizes', nargs='+', type=int, default=(768, 1024))
    parser.add_argument('--device', default='cpu')
    parser.add_argument('--views-per-source', type=int, default=1)
    parser.add_argument('--preprocessing', choices=('original', 'contrast', 'combined'), default='original')
    parser.add_argument('--calibrate', action='store_true', help='Fit thresholds on validation and evaluate on test')
    args = parser.parse_args()
    if any(size < 32 or size % 32 for size in args.sizes):
        parser.error('Inference sizes must be positive multiples of 32')
    if args.calibrate and args.split != 'test':
        parser.error('--calibrate requires --split test')
    if args.views_per_source < 1:
        parser.error('--views-per-source must be positive')
    from ultralytics import YOLO, settings
    import torch

    torch.set_num_threads(4)
    settings.update({'sync': False})
    with args.weights.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    rows, images, labels = representative_frames(args.dataset, args.split, args.views_per_source)
    model = YOLO(str(args.weights), task='detect')
    if tuple(model.names.values()) != tuple(labels):
        raise ValueError('Checkpoint and dataset class maps differ')
    if args.calibrate:
        val_rows, val_images, _ = representative_frames(args.dataset, 'val', args.views_per_source)
        if {row['scene'] for row in val_rows} & {row['scene'] for row in rows}:
            raise ValueError('Source groups overlap between validation and test')
    report = {'weightsSha256': digest, 'split': args.split, 'images': len(images),
              'sourceGroups': len({row['scene'] for row in rows}), 'preprocessing': args.preprocessing,
              'imageNames': [image.name for image in images],
              'classBoxes': dict(Counter(obj['class'] for row in rows for obj in row['objects'])),
              'results': {}}
    for size in dict.fromkeys(args.sizes):
        predictions, times = infer_weld(model, images, args.device, size, args.preprocessing)
        result = {
            'medianMs': statistics.median(times.values()),
            'metrics': {f'{threshold:.2f}/0.50': measurements(rows, predictions, labels, threshold, .5)
                        for threshold in (.15, .25, .40)},
            'sceneCoverageAt0.25': scene_coverage(rows, predictions, labels, .25, .5),
        }
        if args.calibrate:
            val_predictions, _ = infer_weld(model, val_images, args.device, size, args.preprocessing)
            support = {label: {row['scene'] for row in val_rows if any(
                obj['class'] == label for obj in row['objects'])} for label in labels}
            thresholds = fit(val_rows, val_predictions, labels, min_instances=10, iou=.5)
            for label in labels:
                thresholds[label]['validationSources'] = len(support[label])
                if len(support[label]) < 5:
                    thresholds[label] = {'validationSources': len(support[label]),
                                         'validation_instances': thresholds[label]['validation_instances'],
                                         'threshold': None}
            calibrated = apply(predictions, thresholds, fallback=.25)
            result['calibration'] = {'fallback': .25, 'thresholds': thresholds, 'validationImages': len(val_rows),
                                     'test': measurements(rows, calibrated, labels, 0, .5)}
        report['results'][size] = result
        print(f'{size}px: {len(images)} radiographs in {sum(times.values()) / 1000:.1f} s', flush=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
