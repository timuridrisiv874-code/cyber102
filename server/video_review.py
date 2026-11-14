"""Joint review of locally sampled video frames by the loopback VLM worker."""
import base64
import binascii
import json
import math
import time
from http.client import HTTPException
from io import BytesIO
from urllib.error import HTTPError

from . import vision


ENDPOINT = 'http://127.0.0.1:11436/analyze-video'
MAX_FRAMES = 8
MAX_IMAGE_BYTES = 3 * 1024 * 1024
MAX_BATCH_BYTES = 8 * 1024 * 1024
MAX_REQUEST_BYTES = 12 * 1024 * 1024
MAX_DURATION_SECONDS = 120
TIMEOUT_SECONDS = 150


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
        for name, value in items:
            if name in result:
                raise ValueError('Повторяющиеся поля JSON недопустимы.')
            result[name] = value
        return result

    def constant(_):
        raise ValueError('Некорректное число JSON.')

    value = json.loads(body, object_pairs_hook=pairs, parse_constant=constant)
    json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
    return value


def normalize_frame(encoded):
    from PIL import Image, ImageOps, UnidentifiedImageError

    if (not isinstance(encoded, str) or not encoded
            or len(encoded) > 4 * ((MAX_IMAGE_BYTES + 2) // 3)):
        raise ValueError('Один кадр должен занимать не более 3 МБ.')
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError('Кадр должен содержать изображение в Base64, без ссылки или префикса data:.') from error
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError('Один кадр должен занимать не более 3 МБ.')
    try:
        with Image.open(BytesIO(raw)) as image:
            if (image.format not in ('JPEG', 'PNG') or image.width * image.height > 8_000_000
                    or getattr(image, 'n_frames', 1) != 1):
                raise ValueError('Кадры должны быть JPEG/PNG до 8 мегапикселей.')
            image.load()
            normalized = ImageOps.exif_transpose(image).convert('RGB')
            normalized.thumbnail((1280, 1280))
            output = BytesIO()
            normalized.save(output, format='JPEG', quality=85)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError('Не удалось прочитать кадр видео.') from error
    return base64.b64encode(output.getvalue()).decode('ascii'), len(raw), output.tell()


def prepare_request(data):
    """Validate client metadata and strip image metadata before local inference."""
    if not isinstance(data, dict) or set(data) != {'frames', 'durationSeconds'}:
        raise ValueError('Передайте frames и durationSeconds.')
    duration = data['durationSeconds']
    if not finite_number(duration) or not 0 < duration <= MAX_DURATION_SECONDS:
        raise ValueError('Выберите видео длительностью до 2 минут.')
    frames = data['frames']
    if not isinstance(frames, list) or not 1 <= len(frames) <= MAX_FRAMES:
        raise ValueError('Передайте от 1 до 8 кадров одного видео.')
    prepared, previous, raw_total, normalized_total = [], -1, 0, 0
    for frame in frames:
        if not isinstance(frame, dict) or set(frame) != {'image', 'timestampSeconds'}:
            raise ValueError('У каждого кадра нужны image и timestampSeconds.')
        timestamp = frame['timestampSeconds']
        if not finite_number(timestamp) or not previous < timestamp <= duration or timestamp < 0:
            raise ValueError('Позиции кадров должны идти по порядку и находиться внутри видео.')
        encoded, raw_size, normalized_size = normalize_frame(frame['image'])
        raw_total += raw_size
        normalized_total += normalized_size
        if raw_total > MAX_BATCH_BYTES or normalized_total > MAX_BATCH_BYTES:
            raise ValueError('Суммарный размер кадров не должен превышать 8 МБ.')
        prepared.append({'image': encoded, 'timestampSeconds': timestamp})
        previous = timestamp
    return {'frames': prepared, 'durationSeconds': duration}


def validated_defect_details(details, frames):
    if not isinstance(details, list) or len(details) != len(frames):
        raise ValueError('Defect details must cover the submitted frames')
    for index, (item, frame) in enumerate(zip(details, frames, strict=True)):
        if (not isinstance(item, dict) or set(item) != {'frameIndex', 'timestampSeconds', 'status', 'boxes'}
                or type(item['frameIndex']) is not int or item['frameIndex'] != index
                or not finite_number(item['timestampSeconds'])
                or item['timestampSeconds'] != frame['timestampSeconds']
                or item['status'] not in ('checked', 'partial', 'unavailable', 'skipped')
                or not isinstance(item['boxes'], list) or len(item['boxes']) > 4
                or (item['status'] in ('unavailable', 'skipped') and item['boxes'])):
            raise ValueError('Defect details do not match their frame')
        for detail in item['boxes']:
            if (not isinstance(detail, dict) or set(detail) != {'kind', 'box'}
                    or detail['kind'] not in ('crack', 'terminal_damage')):
                raise ValueError('Unexpected verified defect class')
            box = detail['box']
            if (not isinstance(box, list) or len(box) != 4
                    or any(type(value) is not int or not 0 <= value <= 1000 for value in box)
                    or not (box[0] < box[2] and box[1] < box[3])):
                raise ValueError('Invalid verified defect coordinates')
    return details


def result_from_envelope(envelope, payload):
    expected = vision.model_spec()
    required = {'model', 'revision', 'observations', 'summary', 'durationSeconds'}
    if (not isinstance(envelope, dict) or not required <= set(envelope)
            or set(envelope) - required - {'clipDurationSeconds', 'metrics', 'defectDetails'}
            or envelope['model'] != expected.model_id
            or envelope['revision'] != expected.revision):
        raise ValueError('Unexpected video model response')
    summary, duration = envelope['summary'], envelope['durationSeconds']
    if (not isinstance(summary, str) or not summary.strip() or len(summary) > 1500
            or not finite_number(duration) or duration < 0):
        raise ValueError('Invalid video summary or timing')
    summary.encode('utf-8')
    if ('clipDurationSeconds' in envelope
            and (not finite_number(envelope['clipDurationSeconds'])
                 or envelope['clipDurationSeconds'] != payload['durationSeconds'])):
        raise ValueError('Video duration does not match input')
    if 'metrics' in envelope:
        metrics = envelope['metrics']
        names = ('preprocessMs', 'generateMs', 'decodeMs', 'totalMs', 'inputTokens', 'outputTokens', 'patches', 'frames')
        if (not isinstance(metrics, dict) or set(metrics) != set(names)
                or any(not finite_number(metrics[name]) or metrics[name] < 0 for name in names)
                or type(metrics['frames']) is not int or metrics['frames'] != len(payload['frames'])):
            raise ValueError('Invalid video metrics')
    observations = envelope['observations']
    if not isinstance(observations, list) or len(observations) != len(payload['frames']):
        raise ValueError('Video response must cover every sampled frame')
    cleaned, statuses = [], []
    for index, item in enumerate(observations):
        if (not isinstance(item, dict)
                or set(item) != {'frameIndex', 'timestampSeconds', *vision.SCHEMA['required']}
                or type(item['frameIndex']) is not int or item['frameIndex'] != index
                or not finite_number(item['timestampSeconds'])
                or item['timestampSeconds'] != payload['frames'][index]['timestampSeconds']):
            raise ValueError('Video observation does not match its frame')
        observation = {name: item[name] for name in vision.SCHEMA['required']}
        result = vision.result_from_observation(observation, model=envelope['model'])
        statuses.append(result['status'])
        cleaned.append({**observation, 'observation': observation['observation'][:600],
                        'frameIndex': index, 'timestampSeconds': payload['frames'][index]['timestampSeconds'],
                        'status': result['status']})
    status = 'defect' if 'defect' in statuses else 'clear' if all(item == 'clear' for item in statuses) else 'unusable'
    result = {'source': 'local_vision', 'mode': 'video_review', 'preliminary': True,
            'status': status, 'model': envelope['model'], 'revision': envelope['revision'],
            'summary': summary.strip(), 'observations': cleaned,
            'sampledFrameCount': len(cleaned), 'clipDurationSeconds': payload['durationSeconds'],
            'inferenceSeconds': duration, 'saved': False}
    if 'defectDetails' in envelope:
        result['defectDetails'] = validated_defect_details(envelope['defectDetails'], payload['frames'])
    return result


def _analyze_prepared(payload):
    # Share the single-frame gate: a clip and a camera frame cannot contend here.
    if not vision._slot.acquire(blocking=False):
        raise vision.VisionBusy('Модель занята другим осмотром. Повторите через несколько секунд.')
    started = time.monotonic()
    try:
        try:
            envelope = vision.request_json(ENDPOINT, payload, timeout=TIMEOUT_SECONDS)
        except HTTPError as error:
            try:
                if error.code == 503:
                    try:
                        body = error.read(vision.MAX_RESPONSE_BYTES + 1)
                        failure = strict_json(body) if len(body) <= vision.MAX_RESPONSE_BYTES else None
                    except (ValueError, OSError, RecursionError):
                        failure = None
                    if isinstance(failure, dict) and failure.get('code') == 'vision_busy':
                        raise vision.VisionBusy('Модель занята другим осмотром. Повторите через несколько секунд.') from error
            finally:
                error.close()
            raise
        result = result_from_envelope(envelope, payload)
        result['durationSeconds'] = round(time.monotonic() - started, 3)
        return result
    except (OSError, HTTPException) as error:
        raise vision.VisionUnavailable('Локальная модель не ответила на видео. Повторите разбор позже.') from error
    except (ValueError, KeyError, TypeError, RecursionError) as error:
        raise vision.VisionUnavailable('Модель не смогла согласованно разобрать видео. Попробуйте короткий ролик с одним узлом.') from error
    finally:
        vision._slot.release()


def analyze(data):
    return _analyze_prepared(prepare_request(data))


def analyze_request(content_type, body):
    if not isinstance(content_type, str) or content_type.split(';', 1)[0].strip().lower() != 'application/json':
        raise ValueError('Ожидается application/json с кадрами видео.')
    if not isinstance(body, bytes) or not 0 < len(body) <= MAX_REQUEST_BYTES:
        raise ValueError('Недопустимый размер запроса видео.')
    try:
        data = strict_json(body)
    except (ValueError, RecursionError) as error:
        raise ValueError('Некорректный JSON кадров видео.') from error
    return analyze(data)
