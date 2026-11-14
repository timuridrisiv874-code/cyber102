"""Compare the native S2DS checkpoint and its static ONNX export on identical inputs."""
import argparse
import hashlib
import json
from pathlib import Path
from statistics import median

from calibrate_thresholds import labeled_frames
from evaluate import measurements, overlap


def prediction_agreement(native, exported, threshold):
    counts = {'native': 0, 'exported': 0, 'matchedAt0.99': 0}
    differences = []
    for name, boxes in native.items():
        reference = [box for box in boxes if box['score'] >= threshold]
        remaining = [box for box in exported[name] if box['score'] >= threshold]
        counts['native'] += len(reference)
        counts['exported'] += len(remaining)
        for box in sorted(reference, key=lambda item: -item['score']):
            matches = [(overlap(box['xyxy'], other['xyxy']), index) for index, other in enumerate(remaining)
                       if other['class'] == box['class']]
            similarity, index = max(matches, default=(0., -1))
            if similarity >= .99:
                counts['matchedAt0.99'] += 1
                differences.append(abs(box['score'] - remaining.pop(index)['score']))
    return {**counts, 'maxMatchedScoreDifference': max(differences, default=None)}


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('weights', type=Path)
    parser.add_argument('export', type=Path)
    parser.add_argument('reference', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.weights.suffix != '.pt' or args.export.suffix != '.onnx':
        parser.error('Expected .pt and .onnx files')
    for path in (args.weights, args.export, args.reference):
        path.resolve(strict=True)

    import torch
    from ultralytics import YOLO, settings
    import yaml

    from evaluate import infer

    torch.set_num_threads(4)
    settings.update({'sync': False})
    config = yaml.safe_load(args.reference.read_text(encoding='utf-8'))
    if config['test'] != 'images/test':
        raise ValueError('Expected the original S2DS test configuration')
    rows, images, _ = labeled_frames(Path(config['path']), 'test', config['names'])
    if not images:
        raise ValueError('No test images')
    artifacts, predictions = {}, {}
    labels = tuple(config['names'].values())
    for name, path in (('pytorch', args.weights), ('onnx', args.export)):
        model = YOLO(str(path), task='segment')
        predictions[name], timings = infer(model, images, 'cpu', 768, rect=False)
        if model.names != config['names'] or model.task != 'segment':
            raise ValueError('Artifact class map or task differs from the test configuration')
        artifacts[name] = {'sha256': sha256(path), 'medianMs': median(timings.values()),
                           'at0.25/0.50': measurements(rows, predictions[name], labels, .25, .50)}
    report = {'images': len(rows), 'imageSize': 768, 'device': 'cpu', 'rectangularPadding': False,
              'artifacts': artifacts, 'agreement': prediction_agreement(predictions['pytorch'],
                                                                      predictions['onnx'], .25)}
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
