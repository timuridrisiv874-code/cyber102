"""Server-owned camera evidence and explicitly confirmed inspection snapshots."""

import base64
import hashlib
import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from uuid import uuid4

from . import history, customer, assessment, vision
from .diagnostics import number


ORPHAN_TTL = timedelta(hours=2)
MAX_ORPHANS = 64
BOOLEAN_VISION = ('seal_leak', 'foundation_issue', 'oil_issue', 'terminal_damage', 'guard_issue', 'flow_unstable')
ALLOWED_VISION = frozenset((*BOOLEAN_VISION, 'gauge_pressure'))


class MissingEvidence(LookupError):
    pass


class MissingScenario(LookupError):
    pass


def initialize(connection):
    connection.execute('''
        CREATE TABLE IF NOT EXISTS analysis_evidence (
            id TEXT PRIMARY KEY NOT NULL,
            captured_at TEXT NOT NULL,
            image_sha256 TEXT NOT NULL,
            image_jpeg BLOB NOT NULL,
            result_json TEXT NOT NULL,
            retained INTEGER NOT NULL DEFAULT 0 CHECK(retained IN (0, 1))
        )
    ''')
    connection.execute('CREATE INDEX IF NOT EXISTS evidence_expiry ON analysis_evidence(retained, captured_at)')


def _timestamp(now):
    return now.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def cleanup(connection, now=None):
    now = now or datetime.now(timezone.utc)
    connection.execute('DELETE FROM analysis_evidence WHERE retained = 0 AND captured_at < ?',
                       (_timestamp(now - ORPHAN_TTL),))
    connection.execute('''
        DELETE FROM analysis_evidence WHERE id IN (
            SELECT id FROM analysis_evidence WHERE retained = 0
            ORDER BY captured_at DESC, rowid DESC LIMIT -1 OFFSET ?
        )
    ''', (MAX_ORPHANS,))


def store_analysis(connection, jpeg, result, *, now=None):
    """Store the normalized image actually sent to the model, not a client claim."""
    if not isinstance(jpeg, bytes) or not jpeg or len(jpeg) > 3 * 1024 * 1024:
        raise ValueError('Недопустимый кадр для сохранения.')
    if not isinstance(result, dict) or result.get('status') not in ('defect', 'clear') or result.get('source') != 'local_vision':
        raise ValueError('Сохранить можно только пригодный результат локального анализа.')
    now = now or datetime.now(timezone.utc)
    identifier = str(uuid4())
    captured_at = _timestamp(now)
    connection.execute('''
        INSERT INTO analysis_evidence(id, captured_at, image_sha256, image_jpeg, result_json)
        VALUES (?, ?, ?, ?, ?)
    ''', (identifier, captured_at, hashlib.sha256(jpeg).hexdigest(), jpeg, history._encode(result)))
    cleanup(connection, now)
    return {'analysisId': identifier, 'capturedAt': captured_at, 'imageUrl': f'/api/analyses/{identifier}/image'}


def get_analysis(connection, identifier, *, include_image=False, now=None):
    history._identifier(identifier, 'Идентификатор анализа')
    image_column = ', image_jpeg' if include_image else ''
    row = connection.execute(f'''
        SELECT id, captured_at, image_sha256, result_json, retained{image_column}
        FROM analysis_evidence WHERE id = ?
    ''', (identifier,)).fetchone()
    now = now or datetime.now(timezone.utc)
    if row is None or (not row[4] and row[1] < _timestamp(now - ORPHAN_TTL)):
        raise MissingEvidence('Кадр не найден или срок хранения истёк. Выполните анализ ещё раз.')
    if include_image:
        return row[5]
    return {'analysisId': row[0], 'capturedAt': row[1], 'imageSha256': row[2],
            'imageUrl': f'/api/analyses/{row[0]}/image', 'result': json.loads(row[3])}


def _attachment_image(encoded):
    max_chars = 4 * ((vision.MAX_IMAGE_BYTES + 2) // 3)
    if not isinstance(encoded, str) or not 0 < len(encoded) <= max_chars:
        raise ValueError('Приложите JPEG-фото до 3 МБ.')
    try:
        raw = base64.b64decode(encoded, validate=True)
    except ValueError as error:
        raise ValueError('Не удалось прочитать содержимое фото.') from error
    return hashlib.sha256(raw).hexdigest(), vision.normalize_image(raw, formats=('JPEG',), max_side=1920)


def _point(value):
    if (not isinstance(value, dict) or set(value) != {'x', 'y'}
            or any(type(v) not in (int, float) or not 0 <= v <= 1 for v in value.values())):
        raise ValueError('Укажите точку внутри фотографии.')
    return {'x': float(value['x']), 'y': float(value['y'])}


def _measurement(value, jpeg):
    from PIL import Image
    if not isinstance(value, dict) or set(value) != {'referenceMm', 'reference', 'target'}:
        raise ValueError('Для измерения нужны длина эталона и два отрезка.')
    reference_mm = value['referenceMm']
    if type(reference_mm) not in (float, int) or not 0 < reference_mm <= 10000:
        raise ValueError('Укажите длину эталона в миллиметрах (до 10000).')
    with Image.open(BytesIO(jpeg)) as image:
        width, height = image.size
    result = {'referenceMm': reference_mm}
    lengths = {}
    for name, min_pixels in (('reference', 12), ('target', 2)):
        ends = value[name]
        if not isinstance(ends, list) or len(ends) != 2:
            raise ValueError('Каждый отрезок должен содержать две точки.')
        a, b = result[name] = [_point(p) for p in ends]
        lengths[name] = math.hypot((a['x'] - b['x']) * width, (a['y'] - b['y']) * height)
        if lengths[name] + 1e-9 < min_pixels:
            raise ValueError(f'Слишком короткий отрезок на фото. Минимум — {min_pixels} пикс.')
    result['lengthMm'] = reference_mm * lengths['target'] / lengths['reference']
    return result


def _closeup(data):
    required = {'point', 'image', 'componentLabel'}
    if (not isinstance(data, dict) or not required <= set(data)
            or set(data) - required - {'defectType', 'measurement'}):
        raise ValueError('Выберите узел и добавьте фотографию крупного плана.')
    label = data['componentLabel']
    if not isinstance(label, str) or not 0 < len(label.strip()) <= 120 or not label.strip().isprintable():
        raise ValueError('Название узла должно содержать от 1 до 120 символов.')
    point = _point(data['point'])
    if 'defectType' in data and data['defectType'] not in ('corrosion', 'liquid', 'crack', 'wiring', 'coating', 'other'):
        raise ValueError('Неизвестный тип дефекта.')
    sha, image = _attachment_image(data['image'])
    result = {'point': point, 'componentLabel': label.strip(), 'imageSha256': sha}
    if 'defectType' in data:
        result['defectType'] = data['defectType']
    if 'measurement' in data:
        result['measurement'] = _measurement(data['measurement'], image)
    return result, image


def validate_request(data, *, saving=False):
    allowed = {'serial', 'analysisId', 'telemetrySource', 'confirmedVision', 'note', 'requestId', 'selectedComponent'}
    if saving:
        allowed.add('closeup')
    if not isinstance(data, dict) or set(data) - allowed:
        raise ValueError('Недопустимые поля осмотра.')
    serial = history._identifier(data.get('serial'), 'Серийный номер')
    analysis_id = history._identifier(data.get('analysisId'), 'Идентификатор анализа')
    source = data.get('telemetrySource')
    if not isinstance(source, dict):
        raise ValueError('Явно укажите источник телеметрии.')
    if source.get('kind') == 'none' and set(source) == {'kind'}:
        source = {'kind': 'none'}
    elif source.get('kind') == 'demo_scenario' and set(source) == {'kind', 'scenarioId'}:
        source = {'kind': 'demo_scenario', 'scenarioId': history._identifier(source['scenarioId'], 'Сценарий')}
    elif source.get('kind') == 'manual' and set(source) == {'kind', 'values'}:
        source = {'kind': 'manual', 'values': validate_telemetry(source['values'])}
    else:
        raise ValueError('Укажите отсутствие телеметрии, ручные показания или демонстрационный сценарий.')
    submitted = data.get('confirmedVision')
    if not isinstance(submitted, dict) or set(submitted) - ALLOWED_VISION:
        raise ValueError('Укажите подтверждённые признаки из карты осмотра.')
    confirmed = {key: submitted.get(key) for key in BOOLEAN_VISION}
    if any(value is not None and type(value) is not bool for value in confirmed.values()):
        raise ValueError('Визуальный признак должен быть true, false или null (не оценён).')
    gauge = submitted.get('gauge_pressure')
    if gauge is not None and (number(gauge) is None or gauge < 0):
        raise ValueError('Показание манометра должно быть неотрицательным числом в МПа или null.')
    confirmed['gauge_pressure'] = gauge
    note = data.get('note', '')
    if not isinstance(note, str) or len(note) > history.MAX_NOTE_LENGTH or '\0' in note:
        raise ValueError('Заметка должна быть строкой длиной до 1000 символов.')
    request_id = data.get('requestId')
    if saving or request_id is not None:
        history._identifier(request_id, 'Идентификатор запроса')
    normalized = {'serial': serial, 'analysisId': analysis_id, 'telemetrySource': source,
                  'confirmedVision': confirmed, 'note': note}
    component = data.get('selectedComponent')
    if component is not None:
        if component not in ('pump', 'seal', 'foundation', 'shaft_joints', 'bearing', 'gearbox', 'motor', 'temperature_transmitter'):
            raise ValueError('Неизвестный узел оборудования.')
        normalized['selectedComponent'] = component
    if 'closeup' in data:
        normalized['closeup'], closeup_image = _closeup(data['closeup'])
    # The request fingerprint includes image content and the location on the
    # particular analysis frame. Previously saved request hashes stay valid.
    encoded = json.dumps(normalized, allow_nan=False, ensure_ascii=False, sort_keys=True).encode('utf-8')
    if 'closeup' in data:
        normalized['closeup']['jpeg'] = closeup_image
    return normalized, request_id, hashlib.sha256(encoded).hexdigest()


def validate_telemetry(values):
    fields = {field['key']: field for field in customer.rules()['fields']['telemetry']}
    if not isinstance(values, dict) or set(values) - fields.keys():
        raise ValueError('Неизвестные поля ручной телеметрии.')
    result = {}
    for key, value in values.items():
        if value is None:
            continue
        field = fields[key]
        if field['type'] == 'enum':
            valid = isinstance(value, str) and value in [option['value'] for option in field['options']]
        elif field['type'] == 'boolean':
            valid = type(value) is bool
        else:
            valid = number(value) is not None and value >= 0
        if not valid:
            raise ValueError(f'Некорректное показание: {field["label"]}.')
        result[key] = value
    if 'previous_hours' in result and 'hours' in result and result['previous_hours'] > result['hours']:
        raise ValueError('Предыдущая наработка не может превышать текущую.')
    return result


def _preview(connection, normalized, customer_root=customer.ROOT):
    serial = normalized['serial']
    row = connection.execute('SELECT passport_json FROM pumps WHERE serial = ?', (serial,)).fetchone()
    if row is None:
        raise history.UnknownPump('Насос с таким серийным номером не найден.')
    passport = json.loads(row[0])
    analysis = get_analysis(connection, normalized['analysisId'])
    if analysis['result'].get('status') not in ('defect', 'clear'):
        raise ValueError('Для осмотра нужен пригодный результат анализа.')
    source = normalized['telemetrySource']
    telemetry = {}
    provenance = {'source': 'camera_inspection', 'vision': 'engineer_confirmed',
                  'telemetry': source['kind'], 'equipmentBinding': 'operator_selected',
                  'telemetryEquipmentSerial': None}
    if source['kind'] == 'manual':
        telemetry = source['values']
        provenance['telemetryEquipmentSerial'] = serial
    provenance['captureSource'] = analysis['result'].get('media', {}).get('captureSource', 'camera')
    if source['kind'] == 'demo_scenario':
        row = connection.execute('''
            SELECT telemetry_json, source FROM inspections WHERE serial = ? AND id = ?
        ''', (serial, source['scenarioId'])).fetchone()
        if row is None or row[1] != 'demo':
            raise MissingScenario('Демонстрационный сценарий для этого насоса не найден.')
        telemetry = json.loads(row[0])
        provenance['scenarioId'] = source['scenarioId']
        provenance['telemetryEquipmentSerial'] = serial
    confirmed = normalized['confirmedVision']
    assessed = assessment.diagnose(passport, telemetry, confirmed)
    manufacturer = assessment.references(serial, normalized.get('selectedComponent'),
        [item['id'] for item in assessed['findings']], root=customer_root)
    return {'source': 'camera_inspection', 'passport': passport, 'analysis': analysis,
            'telemetry': telemetry, 'vision': confirmed, 'provenance': provenance,
            'manufacturer': manufacturer, **assessed}


def preview(connection, data, *, customer_root=customer.ROOT):
    normalized, _, _ = validate_request(data)
    return _preview(connection, normalized, customer_root)


def save(connection, data, *, customer_root=customer.ROOT):
    """Caller owns transaction: retaining evidence and history insertion are atomic."""
    normalized, request_id, fingerprint = validate_request(data, saving=True)
    if not connection.in_transaction:
        connection.execute('BEGIN IMMEDIATE')
    previous = history.by_request_id(connection, normalized['serial'], request_id)
    if previous:
        if previous['provenance'].get('requestFingerprint') != fingerprint:
            raise history.IdempotencyConflict('Идентификатор запроса уже использован для другого осмотра.')
        return previous, False
    inspected = _preview(connection, normalized, customer_root)
    provenance = {**inspected['provenance'], 'requestFingerprint': fingerprint,
                  'guidance': inspected['guidance'], 'manufacturer': inspected['manufacturer']}
    record, created = history.save(connection, normalized['serial'], provenance.get('scenarioId'),
        inspected['telemetry'], inspected['vision'], inspected['findings'], note=normalized['note'],
        request_id=request_id, evidence=inspected['analysis'], provenance=provenance)
    connection.execute('UPDATE analysis_evidence SET retained = 1 WHERE id = ?', (normalized['analysisId'],))
    if 'closeup' in normalized:
        closeup = normalized['closeup']
        connection.execute('''
            INSERT INTO inspection_closeups(record_id, component_label, point_x, point_y, image_jpeg,
                                           defect_type, measurement_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)''', (record['id'], closeup['componentLabel'],
            closeup['point']['x'], closeup['point']['y'], closeup['jpeg'], closeup.get('defectType'),
            history._encode(closeup['measurement']) if 'measurement' in closeup else None))
        record = history.get_record(connection, record['id'])
    return record, created


def add_work(connection, record_id, data):
    if not isinstance(data, dict) or set(data) - {'requestId', 'action', 'date', 'image'}:
        raise ValueError('Недопустимые поля выполненной работы.')
    key = history._identifier(data.get('requestId'), 'Идентификатор запроса')
    action = data.get('action')
    if action not in ('checked', 'serviced', 'replaced'):
        raise ValueError('Выберите вид выполненной работы.')
    day = data.get('date')
    try:
        if not isinstance(day, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', day):
            raise ValueError()
        date.fromisoformat(day)
    except ValueError as error:
        raise ValueError('Укажите календарную дату выполненной работы.') from error
    sha, image = _attachment_image(data['image']) if 'image' in data else (None, None)
    if not connection.in_transaction:
        connection.execute('BEGIN IMMEDIATE')
    if not connection.execute('SELECT 1 FROM inspection_records WHERE id = ?', (record_id,)).fetchone():
        raise MissingEvidence('Осмотр не найден.')
    cursor = connection.execute('''
        INSERT INTO inspection_work (id, record_id, request_id, action, work_date, image_sha256, image_jpeg)
        VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(record_id, request_id) DO NOTHING
    ''', (str(uuid4()), record_id, key, action, day, sha, image))
    created = cursor.rowcount == 1
    if not created:
        previous = connection.execute('''
            SELECT action, work_date, image_sha256 FROM inspection_work
            WHERE record_id = ? AND request_id = ?
        ''', (record_id, key)).fetchone()
        if previous != (action, day, sha):
            raise history.IdempotencyConflict('Идентификатор запроса уже использован для другой работы.')
    return history.get_record(connection, record_id), created


def attachment_image(connection, record_id, work_id=None):
    if work_id is None:
        result = connection.execute('SELECT image_jpeg FROM inspection_closeups WHERE record_id = ?',
                                    (record_id,)).fetchone()
    else:
        result = connection.execute('SELECT image_jpeg FROM inspection_work WHERE record_id = ? AND id = ?',
                                    (record_id, work_id)).fetchone()
    if not result or result[0] is None:
        raise MissingEvidence('Фотография не найдена.')
    return result[0]
