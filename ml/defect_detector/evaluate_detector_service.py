"""Score a staging detector's HTTP detections against a local, annotated box split."""
import argparse
import base64
import json
from pathlib import Path
from statistics import median
from time import perf_counter
from urllib.request import ProxyHandler, Request, build_opener

from calibrate_thresholds import labeled_frames
from evaluate import class_negative_frames, measurements
from evaluate_service import NoRedirects


def predictions(detections):
    result = []
    for row in detections:
        box = row['box']
        x, y, w, h = (box[key] for key in ('x', 'y', 'width', 'height'))
        result.append({'class': row['class'], 'score': row['score'], 'xyxy': [x, y, x + w, y + h]})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reference', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--weights-sha256', required=True)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('--port must be in [1, 65535]')
    import yaml

    config = yaml.safe_load(args.reference.read_text(encoding='utf-8'))
    if config['test'] != 'images/test':
        raise ValueError('Expected an explicitly configured test split')
    labels = tuple(config['names'].values())
    rows, images, _ = labeled_frames(Path(config['path']), 'test', config['names'])
    opener = build_opener(ProxyHandler({}), NoRedirects())
    base = f'http://127.0.0.1:{args.port}'
    with opener.open(base + '/health', timeout=5) as reply:
        health = json.load(reply)
    if not health['ready'] or health['weightsSha256'] != args.weights_sha256 or not health['offline']:
        raise ValueError('Staging worker failed readiness or checkpoint provenance check')
    from server.detector import validate_envelope

    detected, times = {}, {}
    for row, image in zip(rows, images, strict=True):
        row['scene'] = row['file']
        payload = json.dumps({'image': base64.b64encode(image.read_bytes()).decode('ascii')}).encode('ascii')
        request = Request(base + '/analyze', payload, {'Content-Type': 'application/json'})
        started = perf_counter()
        with opener.open(request, timeout=30) as reply:
            result = validate_envelope(json.load(reply))
        if result['modelVersion'] != health['modelVersion']:
            raise ValueError('Staging checkpoint changed during evaluation')
        times[row['file']] = round((perf_counter() - started) * 1000, 3)
        detected[row['file']] = predictions(result['detections'])
        if any(box['class'] not in labels for box in detected[row['file']]):
            raise ValueError('Unexpected class in staging worker response')
        print(row['file'], len(detected[row['file']]), times[row['file']], flush=True)
    report = {'worker': health, 'images': len(rows), 'medianHttpMs': median(times.values()),
              'at0.50': measurements(rows, detected, labels, 0, .50),
              'negativeFrames': class_negative_frames(rows, detected, labels, 0),
              'timingsMs': times, 'predictions': detected}
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
