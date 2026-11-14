"""Deterministic pump telemetry for a local demonstration, with SQLite history."""
from datetime import datetime, timezone
import json
import math
import time

PERIOD = 5
CHANNELS = {
    'flow': ('Подача', 'м³/ч'), 'discharge_pressure': ('Давление нагнетания', 'бар'),
    'inlet_pressure': ('Давление всасывания', 'бар'), 'rpm': ('Частота вращения', 'об/мин'),
    'vibration': ('Вибрация RMS', 'мм/с'), 'bearing_temperature': ('Температура подшипника', '°C'),
    'current': ('Ток двигателя', 'А'),
}


def initialize(connection):
    connection.execute('''CREATE TABLE IF NOT EXISTS simulated_telemetry (
        serial TEXT NOT NULL REFERENCES pumps(serial), slot INTEGER NOT NULL, payload TEXT NOT NULL,
        PRIMARY KEY (serial, slot)
    )''')


def readings(group, slot, reference=None):
    step = (slot + 17 * group) % 120
    wave = math.sin(step / 7)
    fluctuation = max(0, math.sin(step * .9)) if 42 <= step < 78 else 0
    load = (.49 + .08 * group) + .07 * wave
    values = (5 + 15 * load, 2 + 5 * load, 1.3 + .13 * wave - .45 * fluctuation,
              100 + 140 * load, 1.2 + .25 * wave + 1.25 * fluctuation,
              37 + 12 * load + .65 * wave, 8 + 7 * load)
    result = {name: round(value, 2) for name, value in zip(CHANNELS, values, strict=True)}
    if reference:
        row = reference[(slot // 3 + 73 * group) % len(reference)]
        result['vibration'] = round(.70 * result['vibration'] + .30 * row[0], 2)
        result['bearing_temperature'] = round(.80 * result['bearing_temperature'] + .20 * row[1], 2)
    return result


def pump_groups(connection):
    for serial, payload in connection.execute('SELECT serial, passport_json FROM pumps'):
        passport = json.loads(payload)
        group = passport.get('groupNumber')
        if passport.get('kind') == 'pump' and type(group) is int:
            yield serial, group


def record(connection, *, now=None, reference=None):
    slot = int(time.time() if now is None else now) // PERIOD
    for serial, group in pump_groups(connection):
        connection.executemany('INSERT OR IGNORE INTO simulated_telemetry VALUES (?, ?, ?)',
                               ((serial, i, json.dumps(readings(group, i, reference))) for i in range(slot - 11, slot + 1)))
    connection.execute('DELETE FROM simulated_telemetry WHERE slot<?', (slot - 719,))


def snapshot(connection, serial):
    if serial not in dict(pump_groups(connection)):
        return None
    records = connection.execute('SELECT slot, payload FROM simulated_telemetry WHERE serial=? '
                                 'ORDER BY slot DESC LIMIT 12', (serial,)).fetchall()
    samples = [{'timestamp': datetime.fromtimestamp(i * PERIOD, timezone.utc).isoformat(),
                'values': json.loads(payload)} for i, payload in reversed(records)]
    return {'source': 'simulated', 'equipmentSerial': serial, 'periodSeconds': PERIOD,
            'channels': [{'id': key, 'label': label, 'unit': unit} for key, (label, unit) in CHANNELS.items()],
            'samples': samples, 'binding': 'demo_asset', 'latest': samples[-1] if samples else None}


def replay_template(root):
    """Use archive curves as a shape reference for two channels, without binding its rows to assets."""
    from .telemetry import _replay_data
    _, rows = _replay_data(root)
    template = []
    for row in rows:
        vibration = row['vibration']['rms_mm_s']
        temperature = row['temperature']['bearing_c']
        if all(type(value) in (float, int) and math.isfinite(value) for value in (vibration, temperature)):
            template.append((vibration, temperature))
    return tuple(template)
