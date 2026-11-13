"""Strict loopback adapter for the team's separately supplied detector weights."""
import base64
import json
import math
import re
import threading
import time
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from .vision import COMPONENTS, CONDITIONS, NoRedirects, VisionBusy, VisionUnavailable

MODEL = 'team-detector'
ENDPOINT = 'http://127.0.0.1:11437/analyze'
CLASSES = frozenset(('corrosion', 'wet_patch', 'visible_damage'))
MAX_DETECTIONS = 64
MAX_IMAGE_BYTES = 3 * 1024 * 1024
MAX_RESPONSE_BYTES = 128 * 1024
TIMEOUT_SECONDS = 30
VERSION_PATTERN = re.compile(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,79}\Z')
_slot = threading.BoundedSemaphore(1)


def finite_number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def strict_json(body):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Repeated JSON field')
            result[key] = value
        return result

    def constant(_):
        raise ValueError('Nonfinite JSON number')

    return json.loads(body, object_pairs_hook=pairs, parse_constant=constant)


def validate_envelope(data):
    """Validate a complete v1 response; unsupported detections never become normal."""
    if (not isinstance(data, dict) or set(data) != {'schemaVersion', 'model', 'modelVersion', 'detections'}
            or type(data['schemaVersion']) is not int or data['schemaVersion'] != 1
            or data['model'] != MODEL or not isinstance(data['modelVersion'], str)
            or not VERSION_PATTERN.fullmatch(data['modelVersion'])):
        raise ValueError('Unsupported detector envelope')
    detections = data['detections']
    if not isinstance(detections, list) or len(detections) > MAX_DETECTIONS:
        raise ValueError('Invalid detections list')
    cleaned = []
    for detection in detections:
        if not isinstance(detection, dict) or set(detection) != {'class', 'score', 'box', 'component'}:
            raise ValueError('Invalid detection object')
        label, component, score = detection['class'], detection['component'], detection['score']
        if (not isinstance(label, str) or label not in CLASSES or not isinstance(component, str)
                or component not in (*COMPONENTS, 'unknown') or not finite_number(score) or not 0 <= score <= 1):
            raise ValueError('Invalid detector classification or score')
        box = detection['box']
        if (not isinstance(box, dict) or set(box) != {'x', 'y', 'width', 'height'}
                or any(not finite_number(item) for item in box.values())):
            raise ValueError('Invalid detector coordinates')
        x, y, width, height = (box[key] for key in ('x', 'y', 'width', 'height'))
        if not (0 <= x < 1 and 0 <= y < 1 and 0 < width <= 1 and 0 < height <= 1
                and x + width <= 1 and y + height <= 1):
            raise ValueError('Detector box outside image')
        cleaned.append({'class': label, 'score': score, 'component': component, 'box': dict(box)})
    return {'schemaVersion': 1, 'model': MODEL, 'modelVersion': data['modelVersion'], 'detections': cleaned}


def result_from_envelope(data):
    envelope = validate_envelope(data)
    detections = sorted(envelope['detections'], key=lambda row: -row['score'])
    result = {'source': 'local_vision', 'model': MODEL, 'modelVersion': envelope['modelVersion'],
              'preliminary': True, 'detections': detections, 'scoreMeaning': 'uncalibrated_detection_score'}
    if not detections:
        return {**result, 'status': 'unusable', 'reason': 'no_supported_detections'}
    primary = detections[0]
    finding, recommendation = CONDITIONS[primary['class']]
    component = COMPONENTS.get(primary['component'], 'Узел не определён')
    return {**result, 'status': 'defect', 'component': component, 'box': dict(primary['box']),
            'finding': finding, 'recommendation': recommendation,
            'observation': f'{component}: {finding.lower()}. Признак требует проверки на месте.',
            'equipment': {'id': 'Серийный номер не подтверждён', 'name': 'Объект на снимке'},
            'serviceLife': 'Нужны паспорт и история узла', 'nextService': 'По регламенту оборудования'}


def request_envelope(image):
    payload = {'image': base64.b64encode(image).decode('ascii')}
    request = Request(ENDPOINT, json.dumps(payload).encode('ascii'), {'Content-Type': 'application/json'})
    # Fixed loopback destination; neither a proxy nor a redirect may receive images.
    with build_opener(ProxyHandler({}), NoRedirects()).open(request, timeout=TIMEOUT_SECONDS) as response:
        body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError('Detector response too large')
    return strict_json(body)


def analyze(image):
    """Analyze already sanitized JPEG bytes using the same typed errors as vision."""
    if not isinstance(image, bytes) or not image or len(image) > MAX_IMAGE_BYTES:
        raise ValueError('Expected a bounded sanitized JPEG')
    if not _slot.acquire(blocking=False):
        raise VisionBusy('Детектор занят другим кадром. Повторите через несколько секунд.')
    started = time.monotonic()
    try:
        try:
            envelope = request_envelope(image)
        except HTTPError as error:
            try:
                if error.code == 503:
                    try:
                        body = error.read(MAX_RESPONSE_BYTES + 1)
                        failure = strict_json(body) if len(body) <= MAX_RESPONSE_BYTES else None
                    except (ValueError, OSError, RecursionError):
                        failure = None
                    if isinstance(failure, dict) and failure.get('code') == 'vision_busy':
                        raise VisionBusy('Детектор занят другим кадром. Повторите через несколько секунд.') from error
            finally:
                error.close()
            raise
        result = result_from_envelope(envelope)
        result['durationSeconds'] = round(time.monotonic() - started, 3)
        return result
    except (OSError, HTTPException) as error:
        raise VisionUnavailable('Локальный детектор не ответил. Повторите кадр позже.') from error
    except (ValueError, KeyError, TypeError, RecursionError) as error:
        raise VisionUnavailable('Детектор вернул некорректный ответ. Результат не использован.') from error
    finally:
        _slot.release()
