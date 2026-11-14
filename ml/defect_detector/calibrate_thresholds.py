"""Choose per-class thresholds on a labeled validation split and test them unchanged."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from evaluate import infer, measurements


IMAGE_SUFFIXES = {'.jpg', '.jpeg', '.png', '.webp'}


def labeled_frames(dataset, split, names, excluded=None):
    images, rows, hashes = [], [], set()
    for image in sorted((dataset / 'images' / split).iterdir()):
        if image.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        digest = hashlib.sha256(image.read_bytes()).digest()
        if digest in hashes or (excluded and digest in excluded):
            raise ValueError(f'Duplicate image in evaluation splits: {image}')
        hashes.add(digest)
        associated = dataset / 'labels' / split / image.with_suffix('.txt').name
        objects = []
        for line in associated.read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            category, x, y, w, h = line.split()
            x, y, w, h = map(float, (x, y, w, h))
            objects.append({'class': names[int(category)], 'xyxy': [x - w / 2, y - h / 2, x + w / 2, y + h / 2]})
        rows.append({'file': image.name, 'objects': objects})
        images.append(image)
    return rows, images, hashes


def fit(rows, predictions, labels, min_instances, iou, *, min_precision=0):
    support = Counter(item['class'] for row in rows for item in row['objects'])
    report = {label: {'validation_instances': support[label], 'threshold': None} for label in labels}
    for step in range(1, 20):
        threshold = step / 20
        summary = measurements(rows, predictions, labels, threshold, iou)['classes']
        for label, counts in summary.items():
            if support[label] < min_instances or not counts['tp'] or counts['precision'] < min_precision:
                continue
            tp, fp, fn = (counts[k] for k in ('tp', 'fp', 'fn'))
            f05 = 5 * tp / (5 * tp + 4 * fp + fn)
            old = report[label]
            if f05 >= old.get('f0.5', 0):
                old.update(threshold=threshold, **{'f0.5': f05, 'validation': counts})
    return report


def apply(predictions, thresholds, fallback):
    cutoff = {label: row['threshold'] if row['threshold'] is not None else fallback
              for label, row in thresholds.items()}
    return {name: [item for item in boxes if item['score'] >= cutoff[item['class']]]
            for name, boxes in predictions.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('weights', type=Path)
    parser.add_argument('dataset', type=Path)
    parser.add_argument('test_annotations', type=Path)
    parser.add_argument('test_images', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--size', type=int, default=768)
    parser.add_argument('--device', default='cpu')
    parser.add_argument('--min-instances', type=int, default=10)
    args = parser.parse_args()
    if args.min_instances < 1:
        parser.error('--min-instances must be positive')
    from ultralytics import YOLO, settings
    settings.update({'sync': False})
    model = YOLO(str(args.weights.resolve(strict=True)))
    names = model.names
    dataset = args.dataset.resolve(strict=True)
    labels = tuple(names.values())
    rows, images, validation_hashes = labeled_frames(dataset, 'val', names)
    val_predictions, _ = infer(model, images, args.device, args.size)
    thresholds = fit(rows, val_predictions, labels, args.min_instances, .5)
    test = json.loads(args.test_annotations.read_text(encoding='utf-8'))
    images = [args.test_images / row['file'] for row in test]
    for image in images:
        if hashlib.sha256(image.read_bytes()).digest() in validation_hashes:
            raise ValueError(f'Holdout image is also in validation: {image.name}')
    test_predictions, times = infer(model, images, args.device, args.size)
    labels = sorted(set(labels) | {obj['class'] for row in test for obj in row['objects']})
    base = .25
    calibrated = apply(test_predictions, thresholds, base)
    report = {'weights': str(args.weights), 'fallback_threshold': base, 'iou': .5,
              'validation_images': len(rows), 'holdout_images': len(test), 'classes': thresholds,
              'baseline': measurements(test, test_predictions, labels, base, .5),
              'calibrated': measurements(test, calibrated, labels, 0, .5), 'times_ms': times}
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(args.output)


if __name__ == '__main__':
    main()
