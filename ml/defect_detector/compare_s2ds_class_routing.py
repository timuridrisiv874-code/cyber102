"""Choose S2DS detector classes and thresholds on validation, then audit the frozen choice."""
import argparse
import hashlib
import json
from pathlib import Path
from statistics import median

from calibrate_thresholds import apply, fit, labeled_frames
from evaluate import class_negative_frames, infer, measurements


def routing_plan(calibrations, labels):
    routes = {}
    for label in labels:
        eligible = [(entry['f0.5'], entry['validation']['precision'], key, entry)
                    for key, classes in calibrations.items()
                    if (entry := classes[label])['threshold'] is not None]
        if eligible:
            _, _, key, entry = max(eligible, key=lambda item: item[:3])
            routes[label] = {'model': key, **entry}
        else:
            routes[label] = {'model': None, 'threshold': None}
    return routes


def route(predictions, plan):
    frames = next(iter(predictions.values()))
    if any(other.keys() != frames.keys() for other in predictions.values()):
        raise ValueError('Candidate predictions cover different frames')
    return {name: [box for label, entry in plan.items() if entry['model'] is not None
                   for box in predictions[entry['model']][name]
                   if box['class'] == label and box['score'] >= entry['threshold']]
            for name in frames}


def report_metrics(rows, predictions, labels):
    return {'at0.50': measurements(rows, predictions, labels, 0, .50),
            'negativeFrames': class_negative_frames(rows, predictions, labels, 0)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('prior', type=Path)
    parser.add_argument('clean', type=Path)
    parser.add_argument('reference', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.prior.resolve(strict=True) == args.clean.resolve(strict=True):
        parser.error('Supply two different checkpoints')
    import torch
    from ultralytics import YOLO, settings
    import yaml

    settings.update({'sync': False})
    torch.set_num_threads(2)
    reference = yaml.safe_load(args.reference.read_text(encoding='utf-8'))
    root, names = Path(reference['path']), reference['names']
    if tuple(names.values()) != ('efflorescence', 'masonry_damage'):
        raise ValueError('Expected the two S2DS classes in checkpoint order')
    labels = tuple(names.values())
    validation, validation_images, hashes = labeled_frames(root, 'val', names)
    test, test_images, _ = labeled_frames(root, 'test', names, excluded=hashes)
    if not validation or not test:
        raise ValueError('Both evaluation splits are required')
    for row in test:
        row['scene'] = row['file']
    all_validation, all_test, model_info, timing = {}, {}, {}, {}
    for key, weights in (('prior', args.prior), ('clean', args.clean)):
        model = YOLO(str(weights))
        if model.names != names or model.task != 'segment':
            raise ValueError(f'Unexpected model or class map: {key}')
        with weights.open('rb') as stream:
            model_info[key] = {'sha256': hashlib.file_digest(stream, 'sha256').hexdigest()}
        all_validation[key], _ = infer(model, validation_images, 'cpu', 768)
        print(f'{key}: validation complete', flush=True)
        all_test[key], timing[key] = infer(model, test_images, 'cpu', 768)
        model_info[key]['medianMs'] = median(timing[key].values())
        print(f'{key}: test inference complete', flush=True)

    strategies = {}
    for name, precision in (('f0.5', 0), ('precision90', .90)):
        candidates = {key: fit(validation, predictions, labels, 10, .50, min_precision=precision)
                      for key, predictions in all_validation.items()}
        plan = routing_plan(candidates, labels)
        active_models = {entry['model'] for entry in plan.values()} - {None}
        result = route(all_test, plan)
        singles = {}
        for key, thresholds in candidates.items():
            single_plan = {label: {'model': key if entry['threshold'] is not None else None, **entry}
                           for label, entry in thresholds.items()}
            boxes = (route({key: all_test[key]}, single_plan) if precision
                     else apply(all_test[key], thresholds, .25))
            singles[key] = report_metrics(test, boxes, labels)
        strategies[name] = {
            'minValidationPrecision': precision, 'validation': candidates, 'route': plan,
            'medianSequentialInferenceMs': median(sum(timing[key][row['file']] for key in active_models)
                                                  for row in test),
            'test': report_metrics(test, result, labels),
            'singleModelTest': singles,
        }
    report = {'models': model_info, 'validationImages': len(validation), 'testImages': len(test),
              'strategies': strategies}
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
