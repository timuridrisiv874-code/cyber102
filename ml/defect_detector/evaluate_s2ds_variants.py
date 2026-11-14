"""Measure box and optional mask AP against a held-out dataset and common box reference."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics

import torch
from ultralytics import YOLO, settings
import yaml

from calibrate_thresholds import apply, fit, labeled_frames
from evaluate import class_negative_frames, infer, measurements


def metrics(metric, names):
    classes = {name: {'ap50': None, 'ap50to95': None} for name in names.values()}
    for index, class_id in enumerate(metric.ap_class_index):
        classes[names[int(class_id)]] = {'ap50': float(metric.ap50[index]),
                                       'ap50to95': float(metric.ap[index])}
    return {'mAP50': float(metric.map50), 'mAP50to95': float(metric.map), 'classes': classes}


def box_reference(model, reference, calibrate=False):
    config = yaml.safe_load(reference.read_text(encoding='utf-8'))
    if config['names'] != model.names or config['test'] != 'images/test':
        raise ValueError('Expected a common box test split with the same checkpoint class map')
    root = Path(config['path'])
    rows, images, test_hashes = labeled_frames(root, 'test', model.names)
    for row in rows:
        row['scene'] = row['file']
    labels = tuple(model.names.values())
    baseline_threshold = .25
    thresholds = None
    if calibrate:
        validation, validation_images, _ = labeled_frames(root, 'val', model.names, excluded=test_hashes)
        validation_predictions, _ = infer(model, validation_images, 'cpu', 768)
        thresholds = fit(validation, validation_predictions, labels, min_instances=10, iou=.50)
    predictions, timings = infer(model, images, 'cpu', 768)
    report = {'images': len(images), 'medianMs': statistics.median(timings.values()),
              'at0.25/0.50': measurements(rows, predictions, labels, baseline_threshold, .50),
              'negativeFramesAt0.25': class_negative_frames(rows, predictions, labels, baseline_threshold)}
    if thresholds is not None:
        calibrated = apply(predictions, thresholds, baseline_threshold)
        report['calibration'] = {
            'validationImages': len(validation_images), 'thresholds': thresholds,
            'at0.50': measurements(rows, calibrated, labels, 0, .50),
            'negativeFrames': class_negative_frames(rows, calibrated, labels, 0),
        }
    return report


def measure(weights, dataset, output, reference=None, calibrate=False):
    settings.update({'sync': False})
    torch.set_num_threads(4)
    model = YOLO(str(weights))
    config = yaml.safe_load(dataset.read_text(encoding='utf-8'))
    if not config.get('test') or config['names'] != model.names:
        raise ValueError('Test split or checkpoint class map is inconsistent')
    result = model.val(data=str(dataset), split='test', imgsz=768, device='cpu', batch=2, workers=0,
                       plots=False, project=str(output.parent / 's2ds-evaluation'), name=output.stem, verbose=False)
    with weights.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    report = {'checkpointSha256': digest, 'task': model.task, 'split': 'test', 'size': 768,
              'box': metrics(result.box, model.names),
              'images': sum(1 for _ in (Path(config['path']) / config['test']).glob('*.jpg')),
              'inferenceMsPerImage': result.speed['inference']}
    if model.task == 'segment':
        report['mask'] = metrics(result.seg, model.names)
    if reference:
        report['commonBoxReference'] = box_reference(model, reference, calibrate)
    output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('weights', type=Path)
    parser.add_argument('config', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--box-reference', type=Path)
    parser.add_argument('--calibrate-reference', action='store_true',
                        help='Fit confidence thresholds on reference validation images and evaluate on the test split')
    args = parser.parse_args()
    if args.calibrate_reference and not args.box_reference:
        parser.error('--calibrate-reference requires --box-reference')
    measure(args.weights, args.config, args.output, args.box_reference, args.calibrate_reference)
