"""Explain rule coverage and request the next useful observation.

This is a three-valued companion to diagnostics.evaluate, not another diagnosis
model. Missing or invalid observations never become a confirmed negative.
"""
from collections.abc import Mapping
from decimal import Decimal

from .diagnostics import DEFAULT_THRESHOLDS, RULE_METADATA, evaluate, maintenance_intervals, number


FIELD_LABELS = {
    'telemetry.vibration': 'Вибрация подшипникового узла',
    'telemetry.temperature': 'Температура подшипникового узла',
    'telemetry.current': 'Ток двигателя',
    'telemetry.broadband_vibration': 'Широкополосная вибрация',
    'telemetry.high_frequency_vibration': 'Высокочастотная вибрация',
    'telemetry.low_frequency_vibration': 'Низкочастотная вибрация',
    'telemetry.inlet_pressure': 'Давление на всасывании',
    'telemetry.axial_vibration': 'Осевая вибрация на муфте',
    'telemetry.coupling_vibration': 'Вибрация на муфте',
    'telemetry.sound_sensor_present': 'Доступность акустического датчика',
    'telemetry.gravel_noise': 'Характерный шум гравия',
    'telemetry.one_x_dominant': 'Преобладание первой оборотной гармоники',
    'telemetry.efficiency_drop_pct': 'Относительное снижение КПД, %',
    'telemetry.hours': 'Текущая наработка, ч',
    'telemetry.previous_hours': 'Предыдущая наработка, ч',
    'telemetry.bearing_overheat_sustained': 'Устойчивость перегрева подшипника',
    'telemetry.bearing_temperature_delta_c': 'Превышение температуры подшипника над базой, °C',
    'telemetry.phase_imbalance_pct': 'Дисбаланс токов фаз, %',
    'telemetry.terminal_temperature_rising': 'Локальный рост температуры клемм',
    'telemetry.discharge_pressure': 'Давление нагнетания электронного датчика, МПа',
    'vision.seal_leak': 'Следы жидкости именно у уплотнения вала',
    'vision.foundation_issue': 'Состояние рамы и анкерных болтов',
    'vision.oil_issue': 'Цвет и уровень масла в смотровом стекле',
    'vision.terminal_damage': 'Видимые следы повреждения клеммной коробки',
    'vision.guard_issue': 'Состояние защитного кожуха муфты',
    'vision.flow_unstable': 'Видимая нестабильность потока',
    'vision.gauge_pressure': 'Показание манометра нагнетания, МПа',
    'thresholds.efficiency_drop_pct': 'Порог снижения КПД из профиля',
    'thresholds.bearing_temperature_delta_c': 'Порог перегрева из профиля',
    'thresholds.phase_imbalance_pct': 'Порог дисбаланса фаз из профиля',
    'thresholds.pressure_mismatch_pct': 'Порог расхождения давления из профиля',
    'thresholds.service_intervals_hours': 'Регламентные интервалы ТО из профиля',
    'comparison.pressure_mismatch_pct': 'Сопоставление показаний давления',
    'comparison.maintenance_interval': 'Достижение или пересечение интервала ТО',
}

INSTRUCTIONS = {
    'telemetry.current': 'Получить состояние тока двигателя из источника измерений. По фото ток не определяется.',
    'telemetry.vibration': 'Проверить тренд вибрации подшипникового узла в сопоставимом режиме работы.',
    'telemetry.temperature': 'Проверить тренд температуры подшипникового узла в сопоставимом режиме работы.',
    'telemetry.sound_sensor_present': 'Указать, доступен ли акустический датчик. Неизвестная доступность не означает его отсутствие.',
    'telemetry.gravel_noise': 'Проверить наличие характерного шума по доступному акустическому датчику.',
    'telemetry.previous_hours': 'Найти предыдущий отсчёт наработки и проверить отсутствие сброса счётчика. Одного текущего значения недостаточно для подтверждения пересечения интервала.',
    'telemetry.discharge_pressure': 'Получить положительное показание электронного датчика нагнетания в МПа в том же режиме и времени, что манометр. При нуле относительное расхождение не рассчитывается.',
    'vision.gauge_pressure': 'Считать манометр нагнетания и подтвердить значение в МПа. Для стрелочного прибора нужны положение стрелки и шкала, одного OCR цифр недостаточно.',
    'vision.guard_issue': 'Проверить видимую зону муфты с безопасной позиции. Закрытый обзор оставить как «не оценено»; не считать его отсутствием кожуха.',
    'vision.seal_leak': 'Осмотреть именно зону уплотнения вала и подтвердить расположение следов жидкости. Влага на другом узле не подтверждает этот признак.',
    'vision.foundation_issue': 'Осмотреть раму и анкеры с безопасной позиции: коррозия, отсутствие крепежа или видимое ослабление.',
    'vision.oil_issue': 'Осмотреть смотровое стекло: уровень относительно MIN, цвет и мутность масла.',
    'vision.terminal_damage': 'Проверить доступные внешние признаки клеммной коробки. Вскрытие и электрические измерения выполняет уполномоченный персонал по регламенту.',
    'vision.flow_unstable': 'Подтвердить видимую нестабильность потока там, где поток действительно доступен наблюдению. Фото закрытого корпуса этого не доказывает.',
}


def _json_value(value):
    """Keep the explanatory API serializable even for malformed direct inputs."""
    if value is None or type(value) in (str, bool):
        return value
    if type(value) in (int, float):
        return value if number(value) is not None else None
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value[:20]]
    return None


def _check(field, value, valid, matches, expected, *, missing=None, signal=True):
    state = 'unknown' if not valid else 'met' if matches else 'not_met'
    result = {'field': field, 'label': FIELD_LABELS[field], 'status': state,
              'value': _json_value(value), 'expected': expected}
    if state == 'unknown':
        result['missingFields'] = [field] if missing is None else missing
        result['reason'] = 'Нет пригодного наблюдения или параметра профиля.'
    # Kept internal while ordering next checks, removed from public check rows.
    result['_signal'] = signal
    return result


def _branch_state(checks):
    if any(check['status'] == 'not_met' for check in checks):
        return 'not_met'
    return 'met' if all(check['status'] == 'met' for check in checks) else 'unknown'


def _missing(checks):
    return list(dict.fromkeys(field for check in checks if check['status'] == 'unknown'
                              for field in check.get('missingFields', [check['field']])))


def _step(field, rule_id):
    label = FIELD_LABELS[field]
    instruction = INSTRUCTIONS.get(field)
    if instruction is None:
        instruction = (f'Сверить параметр «{label}» с документацией именно выбранного оборудования.'
                       if field.startswith('thresholds.') else
                       f'Получить и подтвердить показатель «{label}» из доступного источника в сопоставимом режиме работы.')
    return {'field': field, 'label': label, 'instruction': instruction,
            'source': field.split('.')[0], 'ruleIds': [rule_id]}


def assess(telemetry, vision, thresholds=None):
    """Return all 12 rule states plus deterministic, evidence-driven next checks."""
    telemetry = telemetry if isinstance(telemetry, Mapping) else {}
    vision = vision if isinstance(vision, Mapping) else {}
    limits = {**DEFAULT_THRESHOLDS, **thresholds} if isinstance(thresholds, Mapping) else dict(DEFAULT_THRESHOLDS)
    sources = {'telemetry': telemetry, 'vision': vision, 'thresholds': limits}

    def value(field):
        group, key = field.split('.')
        return sources[group].get(key)

    def enum(field, expected, choices=('normal', 'rising')):
        observed = value(field)
        return _check(field, observed, type(observed) is str and observed in choices,
                      observed == expected, expected)

    def flag(field, expected=True):
        observed = value(field)
        return _check(field, observed, type(observed) is bool, observed is expected, expected)

    def valid_number(field, minimum=None, maximum=None, strict_min=False):
        observed = number(value(field))
        valid = observed is not None
        if valid and minimum is not None:
            valid = observed > minimum if strict_min else observed >= minimum
        if valid and maximum is not None:
            valid = observed <= maximum
        return observed, valid

    def compared(field, threshold, *, minimum=None, maximum=None, threshold_min=0, strict=False):
        observed, valid = valid_number(field, minimum, maximum)
        limit, limit_valid = valid_number(threshold, threshold_min, strict_min=not strict)
        missing = ([] if valid else [field]) + ([] if limit_valid else [threshold])
        condition = valid and limit_valid and (observed > limit if strict else observed >= limit)
        measured = _check(field, observed, valid and limit_valid, condition,
                          {'operator': '>' if strict else '>=', 'thresholdField': threshold,
                           'thresholdValue': _json_value(limit)}, missing=missing)
        profile = _check(threshold, limit, limit_valid, True,
                         'Неотрицательный порог' if strict else 'Положительный порог', signal=False)
        return [measured, profile]

    tv = lambda key, expected='rising': enum('telemetry.' + key, expected)
    current = lambda expected: enum('telemetry.current', expected, ('normal', 'slightly_high'))
    pressure = lambda: enum('telemetry.inlet_pressure', 'falling', ('normal', 'falling'))
    high = lambda key: enum('telemetry.' + key, 'high', ('normal', 'high'))

    branches = {
        'bearing_lubrication': [[tv('vibration'), tv('temperature'), current('normal')]],
        'cavitation': [
            [tv('broadband_vibration'), pressure(), flag('telemetry.sound_sensor_present', False)],
            [tv('broadband_vibration'), pressure(), flag('telemetry.sound_sensor_present'), flag('telemetry.gravel_noise')],
        ],
        'shaft_misalignment': [
            [high('axial_vibration'), tv('temperature', 'normal'), current('slightly_high')],
            [high('coupling_vibration'), tv('temperature', 'normal'), current('slightly_high')],
        ],
        'hydraulic_wear': [[*compared('telemetry.efficiency_drop_pct', 'thresholds.efficiency_drop_pct', minimum=0, maximum=100),
                            tv('vibration', 'normal'), tv('temperature', 'normal')]],
        'seal_leak': [[flag('vision.seal_leak'), tv('vibration'), tv('temperature')]],
        'foundation_looseness': [[flag('vision.foundation_issue'), tv('low_frequency_vibration'), flag('telemetry.one_x_dominant')]],
        'oil_degradation': [[flag('vision.oil_issue'), flag('telemetry.bearing_overheat_sustained'),
                             *compared('telemetry.bearing_temperature_delta_c', 'thresholds.bearing_temperature_delta_c')]],
        'terminal_contact': [
            [flag('vision.terminal_damage'), *compared('telemetry.phase_imbalance_pct', 'thresholds.phase_imbalance_pct', minimum=0, strict=True)],
            [flag('vision.terminal_damage'), flag('telemetry.terminal_temperature_rising')],
        ],
        'coupling_guard': [[flag('vision.guard_issue')]],
        'cavitation_confirmed': [
            [tv('high_frequency_vibration'), pressure(), flag('vision.flow_unstable')],
            [tv('high_frequency_vibration'), pressure(), flag('telemetry.sound_sensor_present'), flag('telemetry.gravel_noise')],
        ],
    }

    # A comparison needs valid, contemporaneous inputs in the same units. Zero
    # electronic pressure is unknown for this relative formula, never a match.
    gauge, gauge_valid = valid_number('vision.gauge_pressure', minimum=0)
    reference, reference_valid = valid_number('telemetry.discharge_pressure', minimum=0, strict_min=True)
    pressure_limit, pressure_limit_valid = valid_number('thresholds.pressure_mismatch_pct', minimum=0)
    available = gauge_valid and reference_valid and pressure_limit_valid
    pressure_missing = [field for field, valid in (
        ('vision.gauge_pressure', gauge_valid), ('telemetry.discharge_pressure', reference_valid),
        ('thresholds.pressure_mismatch_pct', pressure_limit_valid)) if not valid]
    mismatch = None
    mismatch_high = False
    if available:
        reference_decimal = Decimal(str(reference))
        difference = abs(Decimal(str(gauge)) - reference_decimal) * 100
        mismatch = difference / reference_decimal
        # Compare before dividing, exactly as the authoritative rule does.
        mismatch_high = difference > Decimal(str(pressure_limit)) * reference_decimal
    branches['gauge_mismatch'] = [[
        _check('vision.gauge_pressure', gauge, gauge_valid, True, 'Число ≥ 0 МПа', signal=False),
        _check('telemetry.discharge_pressure', reference, reference_valid, True, 'Число > 0 МПа', signal=False),
        _check('thresholds.pressure_mismatch_pct', pressure_limit, pressure_limit_valid, True, 'Порог ≥ 0 %', signal=False),
        _check('comparison.pressure_mismatch_pct', float(mismatch) if mismatch is not None and mismatch < Decimal('1e300') else None,
               available, mismatch_high,
               {'operator': '>', 'thresholdField': 'thresholds.pressure_mismatch_pct', 'thresholdValue': pressure_limit},
               missing=pressure_missing),
    ]]

    hours, hours_valid = valid_number('telemetry.hours', minimum=0)
    previous, previous_valid = valid_number('telemetry.previous_hours', minimum=0)
    previous_valid = previous_valid and hours_valid and previous <= hours
    configured = limits.get('service_intervals_hours')
    intervals = sorted({n for item in configured if (n := number(item)) is not None and n > 0}) if isinstance(configured, (list, tuple)) else []
    reached = maintenance_intervals(telemetry, configured)
    # An exact match works without history; below the first threshold there is
    # no possible crossing. Otherwise require a usable earlier counter value.
    needs_previous = bool(hours_valid and intervals and hours >= intervals[0] and not reached and not previous_valid)
    maintenance_missing = ([] if hours_valid else ['telemetry.hours']) + ([] if intervals else ['thresholds.service_intervals_hours'])
    if needs_previous:
        maintenance_missing.append('telemetry.previous_hours')
    maintenance_checks = [
        _check('telemetry.hours', hours, hours_valid, True, 'Наработка ≥ 0 ч', signal=False),
        _check('thresholds.service_intervals_hours', intervals, bool(intervals), True, 'Хотя бы один положительный интервал', signal=False),
    ]
    if needs_previous or previous_valid:
        maintenance_checks.append(_check('telemetry.previous_hours', previous, previous_valid, True,
                                         '0 ≤ предыдущая наработка ≤ текущая; счётчик не сброшен', signal=False))
    maintenance_checks.append(_check('comparison.maintenance_interval', reached,
                                    not maintenance_missing, bool(reached),
                                    'Точное достижение или подтверждённое пересечение интервала', missing=maintenance_missing))
    branches['scheduled_maintenance'] = [maintenance_checks]

    triggered = {finding['id'] for finding in evaluate(telemetry, vision, limits)}
    rules, pending = [], []
    for position, metadata in enumerate(RULE_METADATA):
        rule_id = metadata['id']
        candidates = branches[rule_id]
        states = [_branch_state(candidate) for candidate in candidates]
        if 'met' in states:
            selected = states.index('met')
        else:
            viable = [index for index, state in enumerate(states) if state == 'unknown']
            selected = min(viable, key=lambda i: (len(_missing(candidates[i])),
                -sum(c['status'] == 'met' and c['_signal'] for c in candidates[i]), i)) if viable else 0
        checks = candidates[selected]
        missing = _missing(checks)
        status = ('triggered' if rule_id in triggered else
                  'not_triggered' if all(state == 'not_met' for state in states) else 'insufficient_data')
        superseded = rule_id == 'cavitation' and 'cavitation_confirmed' in triggered
        if superseded:
            status, missing = 'not_triggered', []
        steps = [_step(field, rule_id) for field in missing] if status == 'insufficient_data' else []
        row = {'id': rule_id, 'title': metadata['title'], 'status': status,
               'checks': [{k: v for k, v in check.items() if not k.startswith('_')} for check in checks],
               'missing': missing if status == 'insufficient_data' else [], 'nextSteps': steps,
               'note': metadata['note']}
        if len(candidates) > 1:
            row['alternatives'] = [{'fields': [c['field'] for c in candidate], 'status': state}
                                   for candidate, state in zip(candidates, states)]
            row['selectedAlternative'] = selected
        if superseded:
            row['supersededBy'] = 'cavitation_confirmed'
            row['note'] = 'Предварительный вывод не дублируется: сработало правило с подтверждающим признаком.'
        if status == 'insufficient_data':
            matched = sum(c['status'] == 'met' and c['_signal'] for c in checks)
            pending.append((-matched, len(missing), position, steps))
        rules.append(row)

    next_checks, by_field = [], {}
    for _, _, _, steps in sorted(pending, key=lambda item: item[:3]):
        for step in steps:
            if step['field'] in by_field:
                by_field[step['field']]['ruleIds'].extend(step['ruleIds'])
            else:
                step['order'] = len(next_checks) + 1
                by_field[step['field']] = step
                next_checks.append(step)
    counts = {state: sum(row['status'] == state for row in rules)
              for state in ('triggered', 'not_triggered', 'insufficient_data')}
    return {'rules': rules, 'nextChecks': next_checks,
            'summary': {'total': len(rules), 'triggered': counts['triggered'],
                        'notTriggered': counts['not_triggered'], 'insufficientData': counts['insufficient_data'],
                        'assessed': counts['triggered'] + counts['not_triggered']},
            'notice': 'Проверка условий правил не является подтверждением исправности. Неизвестные данные не считаются нормой.'}
