"""SQLite snapshots of saved equipment inspections."""

import json
from datetime import datetime, timezone
from uuid import uuid4


MAX_NOTE_LENGTH = 1000
MAX_RECORD_LIMIT = 100
_RECORD_COLUMNS = '''
    id, serial, scenario_id, captured_at, telemetry_json, vision_json,
    findings_json, note, request_id, evidence_json, provenance_json
'''


class IdempotencyConflict(ValueError):
    """The same request ID was used with different inspection inputs."""


class UnknownPump(LookupError):
    """No pump has the supplied serial number."""


def initialize(connection):
    connection.execute('''
        CREATE TABLE IF NOT EXISTS inspection_records (
            id TEXT PRIMARY KEY NOT NULL,
            serial TEXT NOT NULL REFERENCES pumps(serial),
            scenario_id TEXT,
            captured_at TEXT NOT NULL,
            telemetry_json TEXT NOT NULL,
            vision_json TEXT NOT NULL,
            findings_json TEXT NOT NULL,
            note TEXT NOT NULL CHECK (length(note) <= 1000),
            request_id TEXT,
            UNIQUE (serial, request_id)
        )
    ''')
    # Additive migration: existing demo records retain their original content.
    columns = {row[1] for row in connection.execute('PRAGMA table_info(inspection_records)')}
    for name in ('evidence_json', 'provenance_json'):
        if name not in columns:
            connection.execute(f"ALTER TABLE inspection_records ADD COLUMN {name} TEXT NOT NULL DEFAULT '{{}}'")
    connection.execute('''
        CREATE INDEX IF NOT EXISTS inspection_records_by_serial
        ON inspection_records (serial, captured_at DESC)
    ''')
    connection.execute('''
        CREATE TABLE IF NOT EXISTS inspection_closeups (
            record_id TEXT PRIMARY KEY REFERENCES inspection_records(id) ON DELETE CASCADE,
            component_label TEXT NOT NULL,
            point_x REAL NOT NULL CHECK(point_x BETWEEN 0 AND 1),
            point_y REAL NOT NULL CHECK(point_y BETWEEN 0 AND 1),
            image_jpeg BLOB NOT NULL,
            defect_type TEXT,
            measurement_json TEXT
        )
    ''')
    connection.execute('''
        CREATE TABLE IF NOT EXISTS inspection_work (
            id TEXT PRIMARY KEY,
            record_id TEXT NOT NULL REFERENCES inspection_records(id) ON DELETE CASCADE,
            request_id TEXT NOT NULL,
            action TEXT NOT NULL CHECK(action IN ('checked', 'serviced', 'replaced')),
            work_date TEXT NOT NULL,
            image_sha256 TEXT,
            image_jpeg BLOB,
            UNIQUE(record_id, request_id)
        )
    ''')


def _identifier(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 128 or '\0' in value:
        raise ValueError(f'{label} должен быть непустой строкой до 128 символов.')
    return value


def _encode(value):
    try:
        # ASCII escaping also handles Unicode escapes in external JSON input.
        return json.dumps(value, allow_nan=False, sort_keys=True, separators=(',', ':'))
    except (TypeError, ValueError, RecursionError) as error:
        raise ValueError('Показания и выводы должны быть корректными данными JSON.') from error


def _record(row):
    id_, serial, scenario, captured_at, telemetry, vision, findings, note, request_id, evidence, provenance = row
    return {
        'id': id_, 'serial': serial, 'scenarioId': scenario, 'capturedAt': captured_at,
        'telemetry': json.loads(telemetry), 'vision': json.loads(vision), 'findings': json.loads(findings),
        'note': note, 'requestId': request_id,
        'evidence': json.loads(evidence), 'provenance': json.loads(provenance),
    }


def with_attachments(connection, records):
    """Load only photo metadata for the selected records, including historical rows."""
    grouped = {record['id']: record for record in records}
    if not grouped:
        return records
    placeholders = ','.join('?' for _ in grouped)
    for record_id, label, x, y, kind, measure in connection.execute(f'''
        SELECT record_id, component_label, point_x, point_y, defect_type, measurement_json FROM inspection_closeups
        WHERE record_id IN ({placeholders})''', tuple(grouped)):
        grouped[record_id]['closeup'] = {'componentLabel': label, 'point': {'x': x, 'y': y},
                                        'imageUrl': f'/api/history/{record_id}/closeup/image'}
        if kind:
            grouped[record_id]['closeup']['defectType'] = kind
        if measure:
            grouped[record_id]['closeup']['measurement'] = json.loads(measure)
    for record_id, work_id, action, day, has_image in connection.execute(f'''
        SELECT record_id, id, action, work_date, image_sha256 IS NOT NULL FROM inspection_work
        WHERE record_id IN ({placeholders}) ORDER BY work_date DESC, rowid DESC''', tuple(grouped)):
        work = {'id': work_id, 'action': action, 'date': day}
        if has_image:
            work['imageUrl'] = f'/api/history/{record_id}/work/{work_id}/image'
        grouped[record_id].setdefault('work', []).append(work)
    return records


def save(connection, serial, scenario_id, telemetry, vision, findings, note='', request_id=None, *, evidence=None, provenance=None):
    """Return (record, created). The caller commits the transaction."""
    _identifier(serial, 'Серийный номер')
    if scenario_id is not None:
        _identifier(scenario_id, 'Идентификатор сценария')
    if request_id is not None:
        _identifier(request_id, 'Идентификатор запроса')
    if not isinstance(note, str) or len(note) > MAX_NOTE_LENGTH or '\0' in note:
        raise ValueError('Заметка должна быть строкой длиной до 1000 символов.')
    if not isinstance(telemetry, dict) or not isinstance(vision, dict):
        raise ValueError('Ожидаются объекты telemetry и vision.')
    if not isinstance(findings, list) or not all(isinstance(finding, dict) for finding in findings):
        raise ValueError('Ожидается список выводов диагностики.')
    evidence = {} if evidence is None else evidence
    provenance = {} if provenance is None else provenance
    if not isinstance(evidence, dict) or not isinstance(provenance, dict):
        raise ValueError('Ожидаются объекты свидетельства и источников.')

    telemetry_json = _encode(telemetry)
    vision_json = _encode(vision)
    findings_json = _encode(findings)
    evidence_json, provenance_json = _encode(evidence), _encode(provenance)
    id_ = str(uuid4())
    captured_at = datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')
    params = (id_, serial, scenario_id, captured_at, telemetry_json, vision_json, findings_json, note, request_id,
              evidence_json, provenance_json, serial)
    cursor = connection.execute(f'''
        INSERT INTO inspection_records ({_RECORD_COLUMNS})
        SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ? FROM pumps WHERE serial = ?
        ON CONFLICT (serial, request_id) DO NOTHING
    ''', params)
    if cursor.rowcount:
        return _record(params[:-1]), True

    row = connection.execute(f'''
        SELECT {_RECORD_COLUMNS} FROM inspection_records WHERE serial = ? AND request_id = ?
    ''', (serial, request_id)).fetchone()
    if row is None:
        raise UnknownPump(serial)
    if (row[2], row[4], row[5], row[7], row[9], row[10]) != (
            scenario_id, telemetry_json, vision_json, note, evidence_json, provenance_json):
        raise IdempotencyConflict('Идентификатор запроса уже использован для другого осмотра.')
    return with_attachments(connection, [_record(row)])[0], False


def by_request_id(connection, serial, request_id):
    row = connection.execute(f'''
        SELECT {_RECORD_COLUMNS} FROM inspection_records WHERE serial = ? AND request_id = ?
    ''', (serial, request_id)).fetchone()
    return with_attachments(connection, [_record(row)])[0] if row else None


def get_record(connection, record_id):
    _identifier(record_id, 'Идентификатор осмотра')
    row = connection.execute(f'SELECT {_RECORD_COLUMNS} FROM inspection_records WHERE id = ?', (record_id,)).fetchone()
    return with_attachments(connection, [_record(row)])[0] if row else None


def list_records(connection, serial, limit=50):
    _identifier(serial, 'Серийный номер')
    if type(limit) is not int or not 1 <= limit <= MAX_RECORD_LIMIT:
        raise ValueError('Лимит должен быть целым числом от 1 до 100.')
    if connection.execute('SELECT 1 FROM pumps WHERE serial = ?', (serial,)).fetchone() is None:
        raise UnknownPump(serial)
    rows = connection.execute(f'''
        SELECT {_RECORD_COLUMNS} FROM inspection_records
        WHERE serial = ? ORDER BY captured_at DESC, rowid DESC LIMIT ?
    ''', (serial, limit))
    return with_attachments(connection, [_record(row) for row in rows])
