"""Measure box and optional mask AP on the original S2DS test split."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics

import torch
from ultralytics import YOLO, settings
import yaml

from evaluate import class_negative_frames, infer, measurements
from prepare_core_rgb import read_labels


def metrics(metric, names):
    classes = {name: {'ap50': None, 'ap50to95': None} for name in names.values()}
    for index, class_id in enumerate(metric.ap_class_index):
        classes[names[int(class_id)]] = {'ap50': float(metric.ap50[index]),
                                       'ap50to95': float(metric.ap[index])}
    return {'mAP50': float(metric.map50), 'mAP50to95': float(metric.map), 'classes': classes}


def box_reference(model, reference):
    config = yaml.safe_load(reference.read_text(encoding='utf-8'))
    if config['names'] != model.names or config['test'] != 'images/test':
        raise ValueError('Expected the original S2DS test configuration')
    root = Path(config['path'])
    images = sorted((root / config['test']).glob('*.jpg'))
    rows = []
    for image in images:
        objects = []
        for name, (x, y, w, h) in read_labels(root / 'labels' / 'test' / image.with_suffix('.txt').name,
                                              model.names):
            objects.append({'class': name, 'xyxy': [x - w / 2, y - h / 2, x + w / 2, y + h / 2]})
        rows.append({'file': image.name, 'scene': image.name, 'objects': objects})
    predictions, timings = infer(model, images, 'cpu', 768)
    labels = tuple(model.names.values())
    return {'images': len(images), 'medianMs': statistics.median(timings.values()),
            'at0.25/0.50': measurements(rows, predictions, labels, .25, .5),
            'negativeFramesAt0.25': class_negative_frames(rows, predictions, labels, .25)}


def measure(weights, dataset, output, reference=None):
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
        report['commonBoxReference'] = box_reference(model, reference)
    output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('weights', type=Path)
    parser.add_argument('config', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--box-reference', type=Path)
    args = parser.parse_args()
    measure(args.weights, args.config, args.output, args.box_reference)
