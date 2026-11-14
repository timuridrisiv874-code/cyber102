"""Score a staging detector's HTTP detections against a local, annotated box split."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
from statistics import median
from time import perf_counter
from urllib.request import ProxyHandler, Request, build_opener
from uuid import UUID, uuid4

from calibrate_thresholds import labeled_frames
from evaluate import class_negative_frames, match_indices, measurements
from evaluate_service import NoRedirects


def predictions(detections):
    result = []
    for row in detections:
        box = row['box']
        x, y, w, h = (box[key] for key in ('x', 'y', 'width', 'height'))
        result.append({'class': row['class'], 'score': row['score'], 'xyxy': [x, y, x + w, y + h]})
    return result


def analyze_upload(opener, base, image, version):
    """Use the browser's multipart route and check that its evidence is the analyzed JPEG."""
    from server import vision
    from server.detector import validate_envelope

    boundary = f'cyber102-{uuid4().hex}'
    content_type = f'multipart/form-data; boundary={boundary}'
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="captureSource"\r\n\r\n'
            f'uploaded_photo\r\n--{boundary}\r\n'
            'Content-Disposition: form-data; name="file"; filename="frame.jpg"\r\n'
            'Content-Type: image/jpeg\r\n\r\n').encode('ascii') + image + f'\r\n--{boundary}--\r\n'.encode('ascii')
    started = perf_counter()
    request = Request(base + '/analyze', body, {'Content-Type': content_type})
    with opener.open(request, timeout=35) as reply:
        result = json.load(reply)
    response_ms = round((perf_counter() - started) * 1000, 3)
    if (result['modelVersion'] != version or result['profile'] != 'standard'
            or result['source'] != 'local_vision' or result['media']['captureSource'] != 'uploaded_photo'):
        raise ValueError('App route returned incompatible model provenance or media')
    envelope = validate_envelope({'schemaVersion': 1, 'model': result['model'], 'modelVersion': version,
                                  'detections': result['detections']})
    boxes = envelope['detections']
    normalized = vision.extract_image(content_type, body)
    if boxes:
        evidence_path = f"/api/analyses/{result['analysisId']}/image"
        if result['status'] != 'defect' or result['box'] != boxes[0]['box'] or result['imageUrl'] != evidence_path:
            raise ValueError('App route returned an invalid primary box or evidence path')
        identifier = UUID(result['analysisId'])
        if identifier.version != 4 or str(identifier) != result['analysisId']:
            raise ValueError('App route returned an invalid evidence ID')
        expected = hashlib.sha256(normalized).digest()
        with opener.open(base + evidence_path, timeout=5) as reply:
            content = reply.read(vision.MAX_IMAGE_BYTES + 1)
            if reply.headers.get_content_type() != 'image/jpeg' or hashlib.sha256(content).digest() != expected:
                raise ValueError('App evidence differs from the normalized upload')
    elif result['status'] != 'unusable' or 'analysisId' in result:
        raise ValueError('Empty detection must stay unusable without evidence')
    return predictions(boxes), response_ms, normalized


def analyze_worker(opener, base, image, version):
    from server.detector import validate_envelope

    payload = json.dumps({'image': base64.b64encode(image).decode('ascii')}).encode('ascii')
    request = Request(base + '/analyze', payload, {'Content-Type': 'application/json'})
    started = perf_counter()
    with opener.open(request, timeout=30) as reply:
        result = validate_envelope(json.load(reply))
    if result['modelVersion'] != version:
        raise ValueError('Staging checkpoint changed during evaluation')
    return predictions(result['detections']), round((perf_counter() - started) * 1000, 3)


def evaluate_findings(rows, detected, times, labels):
    return {'images': len(rows), 'medianHttpMs': median(times.values()),
            'at0.50': measurements(rows, detected, labels, 0, .50),
            'negativeFrames': class_negative_frames(rows, detected, labels, 0),
            'timingsMs': times, 'predictions': detected}


def agreement(worker, app):
    counts = {'sameCount': 0, 'sameBoxesAt0.99': 0, 'differentBoxFrames': []}
    for name, boxes in worker.items():
        other = app[name]
        if len(boxes) == len(other):
            counts['sameCount'] += 1
        equivalent = len(boxes) == len(other) and all(
            len(match_indices([box for box in other if box['class'] == label],
                              [box['xyxy'] for box in boxes if box['class'] == label], .99))
            == sum(box['class'] == label for box in boxes) for label in {box['class'] for box in boxes})
        if equivalent:
            counts['sameBoxesAt0.99'] += 1
        else:
            counts['differentBoxFrames'].append(name)
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reference', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--app-port', type=int, help='Also compare a separate Cyber102 app using this detector')
    parser.add_argument('--weights-sha256', required=True)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or (args.app_port is not None
                                       and not 1 <= args.app_port <= 65535):
        parser.error('Ports must be in [1, 65535]')
    if args.app_port == args.port:
        parser.error('Worker and app require different ports')
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
    detected, times = {}, {}
    app_detected, app_times, normalized_detected, evidence = {}, {}, {}, 0
    app_base = f'http://127.0.0.1:{args.app_port}' if args.app_port else None
    if app_base:
        with opener.open(app_base + '/api/capabilities', timeout=5) as reply:
            capabilities = json.load(reply)['vision']
        if capabilities['enabled'] is not True or capabilities['backend'] != 'detector':
            raise ValueError('Expected an isolated Cyber102 app using the detector')
    for row, image in zip(rows, images, strict=True):
        name = row['file']
        row['scene'] = row['file']
        image_bytes = image.read_bytes()
        version = health['modelVersion']
        detected[name], times[name] = analyze_worker(opener, base, image_bytes, version)
        if any(box['class'] not in labels for box in detected[name]):
            raise ValueError('Unexpected class in staging worker response')
        if app_base:
            app_boxes, app_ms, normalized = analyze_upload(opener, app_base, image_bytes, version)
            if any(box['class'] not in labels for box in app_boxes):
                raise ValueError('Unexpected class in staging app response')
            app_detected[name], app_times[name] = app_boxes, app_ms
            # The direct upload uses the source bytes. Repeat with the actual UI input
            # only when the normalization changed detections.
            source_boxes = sorted(detected[name], key=lambda box: -box['score'])
            normalized_detected[name] = source_boxes
            if source_boxes != app_boxes:
                boxes, _ = analyze_worker(opener, base, normalized, version)
                normalized_detected[name] = sorted(boxes, key=lambda box: -box['score'])
            evidence += bool(app_boxes)
        print(name, len(detected[name]), times[name], flush=True)
    report = {'worker': health, **evaluate_findings(rows, detected, times, labels)}
    if app_base:
        report['app'] = {**evaluate_findings(rows, app_detected, app_times, labels),
                         'verifiedEvidenceImages': evidence, 'agreement': agreement(detected, app_detected),
                         'normalizedInputDisagreements': [name for name, boxes in app_detected.items()
                                                          if boxes != normalized_detected[name]]}
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
