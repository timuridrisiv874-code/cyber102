"""Loopback detector service. Requires explicit, trusted local weights and manifest."""
import argparse
import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path

from server.detector import (CLASSES, COMPONENTS, MAX_DETECTIONS, MAX_IMAGE_BYTES,
                             MODEL, VERSION_PATTERN, finite_number, strict_json, validate_envelope)

MAX_BODY = 4 * ((MAX_IMAGE_BYTES + 2) // 3) + 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_WEIGHTS_BYTES = 2 * 1024 * 1024 * 1024
_offline_enabled = False


def deny_external_connections(event, arguments):
    if event == 'socket.connect':
        address = arguments[1]
        try:
            local = isinstance(address, tuple) and ipaddress.ip_address(address[0]).is_loopback
        except ValueError:
            local = False
        if not local:
            raise OSError('External network is disabled in the detector worker')
    elif event == 'socket.getaddrinfo' and arguments[0] not in ('localhost', '127.0.0.1', '::1', None):
        raise OSError('External DNS is disabled in the detector worker')


def enable_offline():
    global _offline_enabled
    os.environ.update({'YOLO_OFFLINE': 'true', 'YOLO_AUTOINSTALL': 'false',
                       'HF_HUB_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1', 'DO_NOT_TRACK': '1'})
    if not _offline_enabled:
        sys.addaudithook(deny_external_connections)
        _offline_enabled = True


def load_manifest(path):
    path = Path(path).resolve(strict=True)
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_MANIFEST_BYTES:
        raise ValueError('Invalid manifest size')
    with path.open('rb') as handle:
        body = handle.read(MAX_MANIFEST_BYTES + 1)
    if len(body) > MAX_MANIFEST_BYTES:
        raise ValueError('Manifest grew beyond limit')
    manifest = strict_json(body)
    required = {'schemaVersion', 'model', 'modelVersion', 'weightsSha256', 'classMap'}
    optional = {'minimumScore', 'classMinimumScores', 'imageSize', 'task'}
    if (not isinstance(manifest, dict) or not required <= set(manifest) or set(manifest) - required - optional
            or type(manifest['schemaVersion']) is not int or manifest['schemaVersion'] != 1
            or manifest['model'] != MODEL or not isinstance(manifest['modelVersion'], str)
            or not VERSION_PATTERN.fullmatch(manifest['modelVersion'])
            or not isinstance(manifest['weightsSha256'], str)
            or not re.fullmatch(r'[0-9a-f]{64}', manifest['weightsSha256'])):
        raise ValueError('Invalid detector manifest')
    class_map = manifest['classMap']
    if not isinstance(class_map, dict) or not 1 <= len(class_map) <= 256:
        raise ValueError('Invalid detector class map')
    for class_id, entry in class_map.items():
        if (not isinstance(class_id, str) or not re.fullmatch(r'0|[1-9][0-9]{0,2}', class_id)
                or int(class_id) > 255 or not isinstance(entry, dict)
                or set(entry) != {'sourceName', 'class', 'component'}):
            raise ValueError('Invalid class map entry')
        if (not isinstance(entry['sourceName'], str) or not 1 <= len(entry['sourceName']) <= 80
                or not entry['sourceName'].isprintable() or not entry['sourceName'].strip()
                or not isinstance(entry['class'], str) or entry['class'] not in CLASSES
                or not isinstance(entry['component'], str) or entry['component'] not in (*COMPONENTS, 'unknown')):
            raise ValueError('Unsupported class map labels')
    minimum_score, image_size = manifest.get('minimumScore', .25), manifest.get('imageSize', 640)
    if not finite_number(minimum_score) or not 0 < minimum_score <= 1:
        raise ValueError('minimumScore must be in (0, 1]')
    class_minimum_scores = manifest.get('classMinimumScores', {})
    if (not isinstance(class_minimum_scores, dict) or not class_minimum_scores.keys() <= class_map.keys()
            or any(not finite_number(score) or not 0 < score <= 1 for score in class_minimum_scores.values())):
        raise ValueError('classMinimumScores must map known class IDs to scores in (0, 1]')
    if type(image_size) is not int or not 320 <= image_size <= 1280 or image_size % 32:
        raise ValueError('imageSize must be a multiple of 32 in [320, 1280]')
    if manifest.get('task', 'detect') not in ('detect', 'segment'):
        raise ValueError('Detector task must be detect or segment')
    return {**manifest, 'minimumScore': minimum_score, 'imageSize': image_size}


def verify_weights(path, expected_sha256):
    candidate = os.fspath(path)
    if '://' in candidate or candidate.startswith(('\\\\', '//')):
        raise ValueError('Weights must be a local filesystem file, not a URL or network share')
    path = Path(path).resolve(strict=True)
    if path.suffix.lower() != '.pt' or not path.is_file() or not 0 < path.stat().st_size <= MAX_WEIGHTS_BYTES:
        raise ValueError('Supply an existing bounded local .pt file')
    digest, size = hashlib.sha256(), 0
    with path.open('rb') as handle:
        while chunk := handle.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_WEIGHTS_BYTES:
                raise ValueError('Weights grew beyond limit')
            digest.update(chunk)
    if not hmac.compare_digest(digest.hexdigest(), expected_sha256):
        raise ValueError('Weights SHA-256 does not match manifest')
    return path


def validate_model_names(names, class_map):
    if isinstance(names, list):
        names = dict(enumerate(names))
    if (not isinstance(names, dict) or any(type(key) is not int for key in names)
            or set(names) != {int(key) for key in class_map}
            or any(names[int(key)] != entry['sourceName'] for key, entry in class_map.items())):
        raise ValueError('Weight class names/IDs do not match manifest')


def envelope_from_rows(coordinates, scores, class_ids, manifest):
    """Convert normalized xyxy, not center-based xywhn, to the browser contract."""
    if not len(coordinates) == len(scores) == len(class_ids) or len(scores) > MAX_DETECTIONS:
        raise ValueError('Invalid detector result lengths')
    detections = []
    for box, score, class_id in zip(coordinates, scores, class_ids):
        if (not finite_number(class_id) or class_id < 0 or class_id != int(class_id)
                or str(int(class_id)) not in manifest['classMap'] or not isinstance(box, (list, tuple))
                or len(box) != 4 or any(not finite_number(item) for item in box)):
            raise ValueError('Invalid model class or xyxy box')
        left, top, right, bottom = box
        entry = manifest['classMap'][str(int(class_id))]
        detections.append({'class': entry['class'], 'component': entry['component'], 'score': score,
                           'box': {'x': left, 'y': top, 'width': right - left, 'height': bottom - top}})
    envelope = validate_envelope({'schemaVersion': 1, 'model': MODEL,
                                  'modelVersion': manifest['modelVersion'], 'detections': detections})
    minimum_score = manifest['minimumScore']
    by_class = manifest.get('classMinimumScores', {})
    envelope['detections'] = [
        row for row, class_id in zip(envelope['detections'], class_ids, strict=True)
        if row['score'] >= by_class.get(str(int(class_id)), minimum_score)
    ]
    return envelope


def read_image(payload):
    from PIL import Image, ImageOps
    if not isinstance(payload, dict) or set(payload) != {'image'} or not isinstance(payload['image'], str):
        raise ValueError('Expected exactly one base64 JPEG')
    if len(payload['image']) > MAX_BODY:
        raise ValueError('Encoded image too large')
    data = base64.b64decode(payload['image'], validate=True)
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError('Invalid image size')
    try:
        with Image.open(BytesIO(data)) as original:
            if original.format != 'JPEG' or original.width * original.height > 8_000_000:
                raise ValueError('Expected JPEG up to 8 megapixels')
            image = ImageOps.exif_transpose(original).convert('RGB')
            image.thumbnail((1280, 1280))
            return image
    except (OSError, Image.DecompressionBombError) as error:
        raise ValueError('Invalid JPEG') from error


def load_model(weights, device):
    # Imports happen only after explicit trust, file/hash validation and offline guard.
    enable_offline()
    import torch
    from ultralytics import YOLO, settings
    settings.update({'sync': False})
    if device.startswith('cuda:') and not torch.cuda.is_available():
        raise RuntimeError('CUDA requested but unavailable; refusing silent CPU fallback')
    return YOLO(str(weights))


class Engine:
    def __init__(self, weights, manifest_path, *, trusted_local_weights=False, device='cuda:0', loader=None):
        if trusted_local_weights is not True:
            raise ValueError('Explicit --trusted-local-weights is required for a trusted teammate .pt file')
        if not isinstance(device, str) or not re.fullmatch(r'cpu|cuda:[0-9]{1,2}', device):
            raise ValueError('Device must be cpu or cuda:N')
        self.manifest = load_manifest(manifest_path)
        verified = verify_weights(weights, self.manifest['weightsSha256'])
        minimum_score = self.manifest['minimumScore']
        by_class = self.manifest.get('classMinimumScores', {})
        self.inference_score = min(by_class.get(class_id, minimum_score) for class_id in self.manifest['classMap'])
        self.device, self.ready, self.lock = device, False, threading.Lock()
        self.model = (loader or load_model)(verified, device)
        if self.model.task != self.manifest.get('task', 'detect'):
            raise ValueError('Weight task does not match manifest')
        validate_model_names(self.model.names, self.manifest['classMap'])

    def infer(self, image):
        # Segmentation results also contain bounding boxes in the original frame.
        results = self.model.predict(source=image, device=self.device, imgsz=self.manifest['imageSize'],
                                     conf=self.inference_score, max_det=MAX_DETECTIONS,
                                     verbose=False, save=False, stream=False, half=self.device.startswith('cuda:'), batch=1)
        if not isinstance(results, list) or len(results) != 1 or results[0].boxes is None:
            raise ValueError('Expected one detection result')
        boxes = results[0].boxes
        if len(boxes) > MAX_DETECTIONS:
            raise ValueError('Too many model detections')
        boxes = boxes.cpu()
        coordinates = boxes.xyxyn.tolist()
        scores = boxes.conf.tolist()
        class_ids = boxes.cls.tolist()
        # Clipping a predicted box to the input image can collapse it to an edge.
        usable = [index for index, (left, top, right, bottom) in enumerate(coordinates)
                  if left != right and top != bottom]
        return envelope_from_rows([coordinates[i] for i in usable], [scores[i] for i in usable],
                                  [class_ids[i] for i in usable], self.manifest)

    def warmup(self):
        from PIL import Image
        with Image.new('RGB', (640, 480), 'white') as image:
            self.infer(image)
        self.ready = True

    def health(self):
        health = {'status': 'ok' if self.ready else 'loading', 'ready': self.ready, 'model': MODEL,
                  'modelVersion': self.manifest['modelVersion'], 'weightsSha256': self.manifest['weightsSha256'],
                  'device': self.device, 'busy': self.lock.locked(), 'offline': True,
                  'minimumScore': self.manifest['minimumScore'], 'imageSize': self.manifest['imageSize']}
        if self.manifest.get('classMinimumScores'):
            health['classMinimumScores'] = dict(self.manifest['classMinimumScores'])
        return health


class Handler(BaseHTTPRequestHandler):
    server_version = 'Cyber102Detector/1'

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *args):
        pass

    def reply(self, status, data):
        body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode('utf-8')
        try:
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'close')
            if status == 503:
                self.send_header('Retry-After', '2')
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        self.close_connection = True

    def do_GET(self):
        if self.path != '/health':
            self.reply(404, {'error': 'Not found'})
            return
        self.reply(200 if self.server.engine.ready else 503, self.server.engine.health())

    def do_POST(self):
        if self.path != '/analyze':
            self.reply(404, {'error': 'Not found'})
            return
        if self.headers.get('Origin') is not None or self.headers.get('Transfer-Encoding'):
            self.reply(400, {'error': 'Only bounded requests from the local API are accepted'})
            return
        lengths = self.headers.get_all('Content-Length', [])
        if (len(lengths) != 1 or len(lengths[0]) > 8 or not lengths[0].isascii()
                or not lengths[0].isdecimal() or not 0 < int(lengths[0]) <= MAX_BODY):
            self.reply(413, {'error': 'Invalid request size'})
            return
        if self.headers.get_content_type() != 'application/json':
            self.reply(400, {'error': 'Expected application/json'})
            return
        if not self.server.engine.ready:
            self.reply(503, {'error': 'Detector not ready', 'code': 'detector_not_ready'})
            return
        if not self.server.engine.lock.acquire(blocking=False):
            self.reply(503, {'error': 'Local GPU is busy', 'code': 'vision_busy', 'retryAfterSeconds': 2})
            return
        try:
            size = int(lengths[0])
            body = self.rfile.read(size)
            if len(body) != size:
                raise ValueError('Incomplete body')
            with read_image(strict_json(body)) as image:
                try:
                    result = self.server.engine.infer(image)
                    validate_envelope(result)
                except (ValueError, KeyError, TypeError):
                    self.reply(502, {'error': 'Invalid detector output'})
                    return
            self.reply(200, result)
        except (ValueError, OSError, TypeError, RecursionError):
            self.reply(400, {'error': 'Invalid image request'})
        except Exception as error:
            self.reply(503, {'error': 'Detector inference unavailable', 'code': 'detector_unavailable'})
            print(json.dumps({'event': 'detector_error', 'type': type(error).__name__}), flush=True)
        finally:
            self.server.engine.lock.release()


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 4

    def __init__(self, address, engine):
        if address[0] != '127.0.0.1':
            raise ValueError('Detector may bind only to 127.0.0.1')
        self.engine, self.slots = engine, threading.BoundedSemaphore(4)
        super().__init__(address, Handler)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--weights', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--trusted-local-weights', action='store_true')
    parser.add_argument('--device', default='cuda:0')
    parser.add_argument('--port', type=int, default=11437)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('--port must be in [1, 65535]')
    enable_offline()
    engine = Engine(args.weights, args.manifest, trusted_local_weights=args.trusted_local_weights, device=args.device)
    engine.warmup()
    print(json.dumps({'event': 'ready', **engine.health()}), flush=True)
    with Server(('127.0.0.1', args.port), engine) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
