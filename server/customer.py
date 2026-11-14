"""Read the supplied examples; expose only indexed, normalized image previews."""
import json
from hashlib import sha256
from pathlib import Path
import re

from server.diagnostics import DEFAULT_THRESHOLDS, RULE_METADATA

ROOT = Path(__file__).resolve().parents[1] / 'data/customer'


def load(name, root=ROOT):
    if name not in ('materials', 'sensors', 'sensor-import-report', 'documents', 'equipment-passports',
                   'equipment-knowledge', 'inspection-knowledge'):
        raise ValueError('Unknown dataset')
    root = Path(root).resolve()
    path = (root / f'{name}.json').resolve()
    if not path.is_relative_to(root):
        raise FileNotFoundError('Dataset unavailable')
    result = json.loads(path.read_text(encoding='utf-8'),
                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite dataset value')))
    if not isinstance(result, dict):
        raise ValueError('Invalid dataset')
    json.dumps(result, allow_nan=False)
    return result


def document(document_id, root=ROOT):
    """Serve only catalogued, hash-verified PDF originals, never arbitrary paths."""
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}', document_id):
        raise FileNotFoundError('Unknown document')
    rows = load('documents', root).get('documents')
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('Invalid document index')
    entries = [row for row in rows if row.get('id') == document_id]
    if len(entries) != 1:
        raise FileNotFoundError('Unknown document')
    digest = entries[0].get('sha256', '')
    if not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest):
        raise ValueError('Invalid document digest')
    root = Path(root).resolve()
    directory = (root / 'documents').resolve()
    path = (directory / f'{digest}.pdf').resolve()
    if (not directory.is_relative_to(root) or not path.is_relative_to(directory)
            or not path.is_file() or path.stat().st_size > 64 * 1024 * 1024):
        raise FileNotFoundError('Document unavailable')
    content = path.read_bytes()
    if not content.startswith(b'%PDF-') or sha256(content).hexdigest() != digest:
        raise ValueError('Document checksum mismatch')
    return content


def image(image_id, root=ROOT):
    if not re.fullmatch(r'[a-f0-9]{64}', image_id):
        raise FileNotFoundError('Unknown image')
    rows = load('materials', root).get('images')
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('Invalid image index')
    if not any(row.get('id') == image_id for row in rows):
        raise FileNotFoundError('Unknown image')
    root = Path(root).resolve()
    directory = (root / 'previews').resolve()
    path = (directory / f'{image_id}.jpg').resolve()
    if not directory.is_relative_to(root) or not path.is_relative_to(directory) or not path.is_file() or path.stat().st_size > 3 * 1024 * 1024:
        raise FileNotFoundError('Preview unavailable')
    return path.read_bytes()


def enum(key, label, values):
    return {'key': key, 'label': label, 'type': 'enum',
            'options': [{'value': value, 'label': title} for value, title in values]}


def numeric(key, label, unit):
    return {'key': key, 'label': label, 'type': 'number', 'unit': unit}


def flag(key, label):
    return {'key': key, 'label': label, 'type': 'boolean'}


def rules():
    trend = [('normal', 'В норме'), ('rising', 'Растёт')]
    vibration = [('normal', 'В норме'), ('high', 'Высокая')]
    return {'rules': RULE_METADATA, 'thresholds': DEFAULT_THRESHOLDS,
            'profile': {'serial': 'P-102', 'source': 'demo', 'title': 'Демонстрационный профиль P-102'},
            'fields': {
                'telemetry': [enum('vibration', 'Вибрация подшипникового узла', trend),
                              enum('temperature', 'Температура подшипникового узла', trend),
                              enum('current', 'Ток двигателя', [('normal', 'В норме'), ('slightly_high', 'Слегка повышен')]),
                              enum('broadband_vibration', 'Широкополосная вибрация', trend),
                              enum('high_frequency_vibration', 'Высокочастотная вибрация', trend),
                              enum('low_frequency_vibration', 'Низкочастотная вибрация', trend),
                              enum('inlet_pressure', 'Давление на входе', [('normal', 'В норме'), ('falling', 'Падает')]),
                              enum('axial_vibration', 'Осевая вибрация на муфте', vibration),
                              enum('coupling_vibration', 'Вибрация на муфте', vibration),
                              flag('sound_sensor_present', 'Акустический датчик доступен'),
                              flag('gravel_noise', 'Подтверждён характерный шум гравия'),
                              flag('one_x_dominant', 'Преобладает первая оборотная гармоника'),
                              flag('bearing_overheat_sustained', 'Перегрев подшипника устойчивый'),
                              flag('terminal_temperature_rising', 'Локальная температура клемм растёт'),
                              numeric('efficiency_drop_pct', 'Относительное снижение КПД', '%'),
                              numeric('bearing_temperature_delta_c', 'Температура подшипника выше базы на', '°C'),
                              numeric('phase_imbalance_pct', 'Дисбаланс токов фаз', '%'),
                              numeric('discharge_pressure', 'Давление нагнетания SCADA', 'МПа'),
                              numeric('previous_hours', 'Предыдущая наработка', 'ч'),
                              numeric('hours', 'Текущая наработка', 'ч')],
                'vision': [flag('seal_leak', 'Следы жидкости у уплотнения вала'),
                           flag('foundation_issue', 'Дефект рамы или анкерных болтов'),
                           flag('oil_issue', 'Масло мутное, тёмное или ниже MIN'),
                           flag('terminal_damage', 'Повреждение или следы перегрева клеммной коробки'),
                           flag('guard_issue', 'Кожух муфты отсутствует, повреждён или смещён'),
                           flag('flow_unstable', 'Подтверждена нестабильность потока'),
                           numeric('gauge_pressure', 'Показание аналогового манометра', 'МПа')],
            }}
