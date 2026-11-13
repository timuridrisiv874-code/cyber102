"""Combine normalized pump observations into explainable advisory findings."""

import math
from collections.abc import Mapping
from typing import Any


DEFAULT_THRESHOLDS = {
    "efficiency_drop_pct": 15,
    "bearing_temperature_delta_c": 10,
    "phase_imbalance_pct": 5,
    "pressure_mismatch_pct": 15,
    "service_intervals_hours": (4000, 8000, 16000),
}

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


def number(value):
    """Accept finite measurements, while keeping missing values and flags distinct."""
    if type(value) not in (int, float):
        return None
    try:
        return value if math.isfinite(value) else None
    except OverflowError:
        return None


def maintenance_intervals(telemetry, intervals):
    hours = number(telemetry.get("hours"))
    previous = number(telemetry.get("previous_hours"))
    if hours is None or hours < 0:
        return []
    # An absent or reset previous counter does not prove a crossing.
    if previous is None or not 0 <= previous <= hours:
        return [interval for interval in intervals if hours == interval]
    return [interval for interval in intervals if previous < interval <= hours]


def evaluate(
    telemetry: Mapping[str, Any],
    vision: Mapping[str, Any],
    thresholds: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Evaluate sensor and visual signals. Missing values cannot satisfy a condition."""
    limits = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    findings = []

    def add(rule_id, title, severity, diagnosis, recommendation, evidence, zones):
        findings.append({
            "id": rule_id,
            "title": title,
            "severity": severity,
            "diagnosis": diagnosis,
            "recommendation": recommendation,
            "evidence": evidence,
            "zones": zones,
        })

    vibration = telemetry.get("vibration")
    temperature = telemetry.get("temperature")
    current = telemetry.get("current")
    sound_available = telemetry.get("sound_sensor_present") is True
    gravel_noise = sound_available and telemetry.get("gravel_noise") is True
    inlet_falling = telemetry.get("inlet_pressure") == "falling"

    if vibration == "rising" and temperature == "rising" and current == "normal":
        add(
            "bearing_lubrication", "Смазка или износ подшипника", "warning",
            "Возможны нарушение смазки и механический износ подшипников.",
            "Проверить систему смазки по регламенту. По результатам осмотра пополнить или заменить смазку и запланировать замену подшипника при подтверждении износа.",
            ["Вибрация растёт", "Температура растёт", "Ток в норме"], ["bearing"],
        )

    flow_unstable = vision.get("flow_unstable") is True
    if inlet_falling and telemetry.get("high_frequency_vibration") == "rising" and (flow_unstable or gravel_noise):
        evidence = ["Давление на входе падает", "Высокочастотная вибрация растёт"]
        if flow_unstable:
            evidence.append("Камера зафиксировала нестабильность потока")
        if gravel_noise:
            evidence.append("Акустический датчик зафиксировал шум, похожий на гравий")
        add(
            "cavitation_confirmed", "Кавитация с дополнительным подтверждением", "critical",
            "Совпадение независимых признаков указывает на активную кавитацию и риск эрозии рабочего колеса.",
            "Немедленно сообщить оператору и действовать по регламенту при кавитации. Проверить уровень в приёмной ёмкости, состояние фильтров и положение задвижки на всасывании.",
            evidence, ["inlet"],
        )
    elif inlet_falling and telemetry.get("broadband_vibration") == "rising" and (not sound_available or gravel_noise):
        evidence = ["Широкополосная вибрация растёт", "Давление на входе падает"]
        if gravel_noise:
            evidence.append("Акустический датчик зафиксировал характерный шум")
        add(
            "cavitation", "Вероятная кавитация", "warning",
            "Падение давления на всасывании и характер вибрации указывают на кавитацию. Возможна эрозия рабочего колеса.",
            "Оперативно проверить условия всасывания и сообщить оператору.",
            evidence, ["inlet"],
        )

    axial_high = telemetry.get("axial_vibration") == "high"
    coupling_high = telemetry.get("coupling_vibration") == "high"
    if (axial_high or coupling_high) and temperature == "normal" and current == "slightly_high":
        add(
            "shaft_misalignment", "Вероятная расцентровка валов", "warning",
            "Повышенная вибрация на муфте указывает на возможное нарушение соосности валов насоса и двигателя.",
            "Запланировать лазерную проверку соосности и коррекцию по результатам замера.",
            ["Осевая вибрация на муфте высокая" if axial_high else "Вибрация на муфте высокая",
             "Температура в норме", "Ток слегка повышен"], ["coupling"],
        )

    efficiency_drop = number(telemetry.get("efficiency_drop_pct"))
    if efficiency_drop is not None and efficiency_drop >= limits["efficiency_drop_pct"] and vibration == temperature == "normal":
        add(
            "hydraulic_wear", "Вероятный износ проточной части", "warning",
            "Снижение КПД при нормальных вибрации и температуре указывает на возможный внутренний износ.",
            "Запланировать осмотр проточной части на следующем останове. По результатам уточнить потребность в рабочем колесе и уплотнительных кольцах.",
            [f"КПД снизился на {efficiency_drop:g}% относительно базового значения",
             "Вибрация в норме", "Температура в норме"], ["pump"],
        )

    intervals = sorted(set(limits["service_intervals_hours"]))
    reached = maintenance_intervals(telemetry, intervals)
    if reached:
        add(
            "scheduled_maintenance", "Регламентный интервал ТО", "info",
            "Наработка достигла интервала, заданного в профиле насоса.",
            "Сверить наряд ТО-2 с регламентом изготовителя. В него могут входить замена масла, проверка центровки и протяжка фланцевых соединений.",
            [f"Достигнуты пороги наработки {', '.join(f'{interval:g}' for interval in reached)} ч"], ["pump"],
        )

    if vision.get("seal_leak") is True and vibration == "rising" and temperature == "rising":
        add(
            "seal_leak", "Утечка в зоне торцевого уплотнения", "critical",
            "Вероятна деградация уплотнения с риском попадания жидкости в подшипниковый узел и его заклинивания.",
            "Немедленно сообщить оператору для оценки перевода насоса в резерв по регламенту. Проверить уплотнение и уровень масла по карте осмотра.",
            ["В зоне вала замечены признаки жидкости", "Вибрация растёт", "Температура растёт"], ["seal", "oil"],
        )

    if (vision.get("foundation_issue") is True and telemetry.get("low_frequency_vibration") == "rising"
            and telemetry.get("one_x_dominant") is True):
        add(
            "foundation_looseness", "Вероятное ослабление крепления", "warning",
            "Состояние рамы или крепежа и характер вибрации указывают на возможную плохую посадку агрегата на фундамент.",
            "Осмотреть отмеченную зону крепления. Допустимый момент затяжки взять из паспорта. После работ проверить соосность.",
            ["Камера отметила раму или анкерные болты", "Низкочастотная вибрация растёт",
             "Преобладает первая гармоника частоты вращения"], ["anchors"],
        )

    bearing_delta = number(telemetry.get("bearing_temperature_delta_c"))
    if (vision.get("oil_issue") is True and telemetry.get("bearing_overheat_sustained") is True
            and bearing_delta is not None and bearing_delta >= limits["bearing_temperature_delta_c"]):
        add(
            "oil_degradation", "Деградация или нехватка масла", "warning",
            "Состояние масла и устойчивый перегрев указывают на ухудшение смазки подшипника.",
            "Передать в обслуживание на осмотр масла и узла. Согласовать срок, тип и объём масла с регламентом и паспортом насоса.",
            ["Масло потемнело, помутнело или уровень ниже MIN",
             f"Температура подшипника устойчиво выше базовой на {bearing_delta:g} °C"], ["oil"],
        )

    phase_imbalance = number(telemetry.get("phase_imbalance_pct"))
    imbalance_high = phase_imbalance is not None and phase_imbalance > limits["phase_imbalance_pct"]
    terminal_heating = telemetry.get("terminal_temperature_rising") is True
    if vision.get("terminal_damage") is True and (imbalance_high or terminal_heating):
        evidence = ["Камера обнаружила повреждение или следы перегрева в зоне клеммной коробки"]
        if imbalance_high:
            evidence.append(f"Дисбаланс фазных токов {phase_imbalance:g}%")
        if terminal_heating:
            evidence.append("Зафиксирован локальный рост температуры")
        add(
            "terminal_contact", "Вероятная неисправность силового контакта", "critical",
            "Возможны высокое переходное сопротивление контакта или повреждение обмотки.",
            "Немедленно передать сигнал оператору и электротехническому персоналу. Осмотр клемм выполняет уполномоченный персонал после изоляции питания по регламенту.",
            evidence, ["terminal"],
        )

    gauge = number(vision.get("gauge_pressure"))
    scada = number(telemetry.get("discharge_pressure"))
    if gauge is not None and gauge >= 0 and scada is not None and scada > 0:
        mismatch = abs(gauge - scada) / scada * 100
        limit = limits["pressure_mismatch_pct"]
        if math.isfinite(mismatch) and mismatch > limit and not math.isclose(mismatch, limit, rel_tol=1e-12):
            add(
                "gauge_mismatch", "Расхождение показаний давления", "warning",
                "Возможны дрейф калибровки электронного датчика, засорение импульсной линии или неисправность манометра.",
                "Сверить показания на месте и передать приборы и импульсную линию на проверку по регламенту.",
                [f"Камера считала {gauge:g} МПа", f"Датчик SCADA показывает {scada:g} МПа",
                 f"Расхождение {mismatch:.1f}% от показания SCADA"], ["gauge"],
            )

    if vision.get("guard_issue") is True:
        add(
            "coupling_guard", "Нарушение защиты муфты", "critical",
            "Кожух отсутствует, повреждён или критически смещён. Возможен контакт с вращающимся валом.",
            "Немедленно сообщить оператору. Эксплуатация запрещена до восстановления ограждения по регламенту.",
            ["Камера зафиксировала нарушение защитного кожуха"], ["guard"],
        )

    return sorted(findings, key=lambda finding: SEVERITY_ORDER[finding["severity"]])
