"""Replay imported customer measurements without deriving diagnostic inputs."""

from datetime import datetime, timezone
import math

from . import customer


SOURCE = 'customer_replay'
CHANNELS = {
    'rpm': 'об/мин',
    'vibration.rms_mm_s': 'мм/с',
    'vibration.one_x_mm_s': 'мм/с',
    'vibration.two_x_mm_s': 'мм/с',
    'vibration.high_frequency_g': 'g',
    'temperature.bearing_c': '°C',
    'temperature.winding_c': '°C',
}
UNAVAILABLE_TELEMETRY = (
    'current', 'phase_imbalance_pct', 'inlet_pressure', 'discharge_pressure',
    'efficiency_drop_pct', 'hours', 'previous_hours', 'sound_sensor_present',
    'gravel_noise', 'terminal_temperature_rising',
)


def _replay_data(root):
    data = customer.load('sensors', root)
    dataset, rows = data.get('dataset'), data.get('rows')
    summary, sources, thresholds = data.get('summary'), data.get('sources'), data.get('thresholds')
    if (data.get('schemaVersion') != 1 or not isinstance(dataset, dict)
            or dataset.get('source') != 'customer_example' or not isinstance(dataset.get('id'), str)
            or not dataset['id'] or dataset.get('equipmentSerial') is not None or dataset.get('timezone') is not None
            or not isinstance(rows, list) or not rows or not isinstance(summary, dict)
            or summary.get('rowCount') != len(rows) or not isinstance(sources, list) or not sources
            or not isinstance(thresholds, dict)):
        raise ValueError('Invalid customer replay dataset')
    return data, rows


def _replay_quality():
    return {
        'status': 'replay',
        'measurementQuality': 'unknown',
        'unavailableTelemetry': list(UNAVAILABLE_TELEMETRY),
    }


def catalog(root=customer.ROOT):
    """Return the index range, provenance, and sample-only thresholds."""
    data, rows = _replay_data(root)
    return {
        'schemaVersion': 1, 'source': SOURCE, 'equipmentSerial': None,
        'dataset': data['dataset'], 'rowCount': len(rows), 'firstIndex': 0, 'lastIndex': len(rows) - 1,
        'summary': data['summary'], 'sources': data['sources'], 'thresholds': data['thresholds'],
        'channels': CHANNELS.copy(), 'quality': _replay_quality(),
    }


def snapshot(index, *, root=customer.ROOT):
    """Return one imported row at a zero-based index without advancing shared state."""
    if type(index) is not int or index < 0:
        raise ValueError('Replay index must be a non-negative integer')
    data, rows = _replay_data(root)
    if index >= len(rows):
        raise IndexError('Replay index is out of range')
    row = rows[index]
    if not isinstance(row, dict) or any(not isinstance(row.get(key), dict) for key in (
        'vibration', 'temperature', 'assessments', 'provenance',
    )):
        raise ValueError('Invalid customer replay row')
    source_timestamp = row.get('timestamp')
    if (not isinstance(source_timestamp, str) or 'T' not in source_timestamp
            or datetime.fromisoformat(source_timestamp).tzinfo is not None):
        raise ValueError('Expected source timestamp without an offset')
    missing = []
    for channel in CHANNELS:
        group, separator, key = channel.partition('.')
        value = row[group].get(key) if separator else row.get(group)
        if value is None:
            missing.append(channel)
        elif type(value) not in (int, float) or (isinstance(value, float) and not math.isfinite(value)):
            raise ValueError(f'Invalid replay measurement: {channel}')
    return {
        'schemaVersion': 1, 'source': SOURCE, 'equipmentSerial': None,
        'datasetId': data['dataset']['id'], 'index': index, 'rowCount': len(rows),
        'sourceTimestamp': source_timestamp, 'sourceTimezone': None,
        'emittedAt': datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
        'quality': {**_replay_quality(), 'missingMeasurements': missing},
        'row': row,
    }
