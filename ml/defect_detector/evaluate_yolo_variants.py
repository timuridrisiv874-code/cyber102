"""Measure YOLO checkpoints on reviewed images at several input sizes and thresholds."""
import argparse
from collections import Counter
from hashlib import file_digest, sha256
import json
from pathlib import Path
from statistics import median

from calibrate_thresholds import fit, labeled_frames
from evaluate import class_negative_frames, infer, localization_errors, match_indices, measurements, scene_coverage

THRESHOLDS = (.01, .05, .15, .25)


def file_digest_rows(rows):
    return sha256(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def load_datasets(specifications, exclusions):
    datasets = {}
    for name, folder, *manifests in specifications:
        if name in datasets:
            raise ValueError(f'Duplicate dataset name: {name}')
        annotations = [row for path in manifests for row in json.loads(Path(path).read_bytes())]
        rows, paths, names = [], [], set()
        for row in annotations:
            filename = row['file']
            if not filename or Path(filename).name != filename or filename in names:
                raise ValueError(f'Expected unique image basenames: {filename}')
            names.add(filename)
            if filename in exclusions.get(name, ()):
                continue
            path = Path(folder) / filename
            with path.open('rb') as image:
                if file_digest(image, 'sha256').hexdigest() != row['sha256']:
                    raise ValueError(f'Image contents differ from annotations: {path}')
            rows.append(row)
            paths.append(path)
        if not set(exclusions.get(name, ())) <= names or not rows:
            raise ValueError(f'Unknown exclusion or empty dataset: {name}')
        datasets[name] = (rows, paths)
    if not set(exclusions) <= datasets.keys():
        raise ValueError('Exclusions refer to an unknown dataset')
    return datasets


def summarize(rows, predictions, labels, calibrated=None):
    report = {}
    for label in labels:
        scoped = [{**r, 'objects': [box for box in r['objects'] if box['class'] == label]}
                  for r in rows
                  if label in r.get('reviewedLabels', labels)]
        if not scoped:
            continue
        errors = localization_errors(scoped, predictions, .01, .5)
        matches = {}
        for floor in THRESHOLDS:
            hits = {}
            for row in scoped:
                filename = row['file']
                boxes = [obj['xyxy'] for obj in row['objects']]
                proposed = [box for box in predictions[filename]
                            if box['class'] == label and box['score'] >= floor]
                matched = match_indices(proposed, boxes, .5)
                if matched:
                    hits[filename] = sorted(matched)
            matches[f'{floor:.2f}'] = hits
        report[label] = {
            'matchedObjects': matches,
            'thresholds': {f'{floor:.2f}': measurements(scoped, predictions, (label,), floor, .5)['classes'][label]
                           for floor in THRESHOLDS},
            'negativeFrames': {f'{floor:.2f}': class_negative_frames(scoped, predictions, (label,), floor)[label]
                               for floor in THRESHOLDS},
            'missesAtFloor': dict(Counter(error['reason'] for error in errors)),
        }
        if calibrated is not None:
            threshold = calibrated[label]['threshold']
            report[label]['calibration'] = calibrated[label]
            if threshold is not None:
                report[label]['calibrated'] = {
                    'metrics': measurements(scoped, predictions, (label,), threshold, .5)['classes'][label],
                    'negativeFrames': class_negative_frames(scoped, predictions, (label,), threshold)[label],
                    'scenes': scene_coverage(scoped, predictions, (label,), threshold, .5)[label],
                }
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--dataset', action='append', nargs='+', required=True,
                        help='Name, image directory and one or more annotation manifests')
    parser.add_argument('--exclude', action='append', nargs=2, metavar=('DATASET', 'BASENAME'), default=[])
    parser.add_argument('--model', action='append', nargs=2, dest='models', metavar=('NAME', 'WEIGHTS'),
                        required=True)
    parser.add_argument('--size', type=int, action='append', required=True)
    parser.add_argument('--tiles', action='store_true', help='Also test tiled inference at the first size')
    parser.add_argument('--square', action='store_true', help='Use identical square padding for native and ONNX models')
    parser.add_argument('--nms', action='store_true', help='Use the native YOLO26 one-to-many head with NMS')
    parser.add_argument('--calibration-data', type=Path, help='Fit per-class thresholds on the YOLO validation split')
    parser.add_argument('--calibration-min-instances', type=int, default=4)
    parser.add_argument('--device', default='0')
    args = parser.parse_args()
    if any(size < 32 or size % 32 for size in args.size):
        parser.error('--size must be a positive multiple of 32')
    if any(len(item) < 3 for item in args.dataset):
        parser.error('--dataset needs a name, images directory and annotation manifest')
    if len(dict(args.models)) != len(args.models):
        parser.error('Model names must be unique')
    if args.calibration_min_instances < 1:
        parser.error('--calibration-min-instances needs to be positive')
    excluded = {}
    for name, file in args.exclude:
        excluded.setdefault(name, []).append(file)
    datasets = load_datasets(args.dataset, excluded)
    holdout_hashes = {bytes.fromhex(row[key]) for rows, _ in datasets.values() for row in rows
                      for key in ('sha256', 'sourceSha256') if key in row}

    import torch
    from ultralytics import YOLO, settings
    from ultralytics.utils.torch_utils import select_device

    settings.update({'sync': False})
    device = select_device(args.device, verbose=False)
    if device.type == 'cpu':
        torch.set_num_threads(min(4, torch.get_num_threads()))
    report = {'device': args.device, 'excluded': excluded, 'squarePadding': args.square, 'nms': args.nms,
              'imageHashes': {name: {row['file']: row['sha256'] for row in rows}
                              for name, (rows, _) in datasets.items()},
              'annotationHashes': {name: file_digest_rows(rows) for name, (rows, _) in datasets.items()},
              'runs': {}}
    variants = [(str(size), size, False) for size in dict.fromkeys(args.size)]
    if args.tiles:
        variants.append((f'{args.size[0]}-tiles', args.size[0], True))
    inference_options = {'rect': not args.square, 'end2end': False if args.nms else None}
    for name, filename in args.models:
        path = Path(filename).resolve(strict=True)
        model = YOLO(str(path), task='detect')
        if args.nms and (path.suffix.lower() != '.pt' or not getattr(model.model, 'end2end', False)
                         or getattr(model.model.model[-1], 'cv3', None) is None):
            raise ValueError('--nms requires a native checkpoint with an unfused one-to-many head')
        with path.open('rb') as weights:
            result = {'artifact': str(path), 'sha256': file_digest(weights, 'sha256').hexdigest(), 'variants': {}}
        report['runs'][name] = result
        for mode, size, tiles in variants:
            variant = result['variants'][mode] = {}
            calibrated = None
            if args.calibration_data is not None:
                validation, images, _ = labeled_frames(args.calibration_data, 'val', model.names,
                                                       excluded=holdout_hashes)
                val_predictions, _ = infer(model, images, args.device, size, tiles=tiles, **inference_options)
                calibrated = fit(validation, val_predictions, model.names.values(),
                                 args.calibration_min_instances, .5)
            for key, (rows, paths) in datasets.items():
                if device.type == 'cuda':
                    torch.cuda.reset_peak_memory_stats(device)
                predictions, timings = infer(model, paths, args.device, size, tiles=tiles, **inference_options)
                labels = sorted({obj['class'] for row in rows for obj in row['objects']} & set(model.names.values()))
                if not labels:
                    continue
                if device.type == 'cuda':
                    torch.cuda.synchronize(device)
                    peak_gpu_bytes = torch.cuda.max_memory_allocated(device)
                else:
                    peak_gpu_bytes = None
                variant[key] = {'summary': summarize(rows, predictions, labels, calibrated), 'timing': timings,
                                'medianMs': median(timings.values()), 'predictions': predictions,
                                'peakGpuAllocatedBytes': peak_gpu_bytes}
                print(name, mode, key, round(variant[key]['medianMs']), flush=True)
                temporary = args.output.with_suffix(args.output.suffix + '.tmp')
                temporary.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
                temporary.replace(args.output)
        del model


if __name__ == '__main__':
    main()
