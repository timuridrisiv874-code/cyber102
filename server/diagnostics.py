"""Combine normalized pump observations into explainable advisory findings."""

import math
from collections.abc import Mapping
from decimal import Decimal
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


def maintenance_intervals(telemetry, configured_intervals):
    hours = number(telemetry.get("hours"))
    previous = number(telemetry.get("previous_hours"))
    if hours is None or hours < 0 or not isinstance(configured_intervals, (list, tuple)):
        return []
    intervals = sorted({
        interval for value in configured_intervals
        if (interval := number(value)) is not None and interval > 0
    })
    # An absent or reset previous counter does not prove a crossing.
    if previous is None or not 0 <= previous <= hours:
        return [interval for interval in intervals if hours == interval]
    return [interval for interval in intervals if previous < interval <= hours]


def evaluate(
    telemetry: Mapping[str, Any],
    vision: Mapping[str, Any],
    thresholds: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Evaluate supplied observations. Missing or invalid required signals cannot satisfy a rule."""
    telemetry = telemetry if isinstance(telemetry, Mapping) else {}
    vision = vision if isinstance(vision, Mapping) else {}
    limits = {**DEFAULT_THRESHOLDS, **thresholds} if isinstance(thresholds, Mapping) else DEFAULT_THRESHOLDS
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
    sound_available = telemetry.get("sound_sensor_present", False)
    gravel_noise = sound_available is True and telemetry.get("gravel_noise") is True
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
            evidence.append("Визуальный признак нестабильности потока")
        if gravel_noise:
            evidence.append("Акустический признак шума, похожего на гравий")
        add(
            "cavitation_confirmed", "Признаки кавитации по нескольким источникам", "critical",
            "Совпадение независимых признаков указывает на активную кавитацию и риск эрозии рабочего колеса.",
            "Немедленно сообщить оператору и действовать по регламенту при кавитации. Проверить уровень в приёмной ёмкости, состояние фильтров и положение задвижки на всасывании.",
            evidence, ["inlet"],
        )
    elif (inlet_falling and telemetry.get("broadband_vibration") == "rising"
          and (sound_available is False or gravel_noise)):
        evidence = ["Широкополосная вибрация растёт", "Давление на входе падает"]
        if gravel_noise:
            evidence.append("Акустический признак характерного шума")
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
    efficiency_limit = number(limits["efficiency_drop_pct"])
    if (efficiency_drop is not None and efficiency_limit is not None
            and 0 < efficiency_limit <= efficiency_drop <= 100 and vibration == temperature == "normal"):
        add(
            "hydraulic_wear", "Вероятный износ проточной части", "warning",
            "Снижение КПД при нормальных вибрации и температуре указывает на возможный внутренний износ.",
            "Запланировать осмотр проточной части на следующем останове. По результатам уточнить потребность в рабочем колесе и уплотнительных кольцах.",
            [f"КПД снизился на {efficiency_drop:g}% относительно базового значения",
             "Вибрация в норме", "Температура в норме"], ["pump"],
        )

    reached = maintenance_intervals(telemetry, limits["service_intervals_hours"])
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
            ["Визуальный признак повреждения рамы или анкерных болтов", "Низкочастотная вибрация растёт",
             "Преобладает первая гармоника частоты вращения"], ["anchors"],
        )

    bearing_delta = number(telemetry.get("bearing_temperature_delta_c"))
    bearing_limit = number(limits["bearing_temperature_delta_c"])
    if (vision.get("oil_issue") is True and telemetry.get("bearing_overheat_sustained") is True
            and bearing_delta is not None and bearing_limit is not None and 0 < bearing_limit <= bearing_delta):
        add(
            "oil_degradation", "Деградация или нехватка масла", "warning",
            "Состояние масла и устойчивый перегрев указывают на ухудшение смазки подшипника.",
            "Передать в обслуживание на осмотр масла и узла. Согласовать срок, тип и объём масла с регламентом и паспортом насоса.",
            ["Масло потемнело, помутнело или уровень ниже MIN",
             f"Температура подшипника устойчиво выше базовой на {bearing_delta:g} °C"], ["oil"],
        )

    phase_imbalance = number(telemetry.get("phase_imbalance_pct"))
    imbalance_limit = number(limits["phase_imbalance_pct"])
    imbalance_high = (phase_imbalance is not None and imbalance_limit is not None
                      and 0 <= imbalance_limit < phase_imbalance)
    terminal_heating = telemetry.get("terminal_temperature_rising") is True
    if vision.get("terminal_damage") is True and (imbalance_high or terminal_heating):
        evidence = ["Визуальный признак повреждения или перегрева в зоне клеммной коробки"]
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
    pressure_limit = number(limits["pressure_mismatch_pct"])
    if (gauge is not None and gauge >= 0 and scada is not None and scada > 0
            and pressure_limit is not None and pressure_limit >= 0):
        # Compare decimal readings to preserve the strict boundary at 15%.
        reference = Decimal(str(scada))
        difference = abs(Decimal(str(gauge)) - reference) * 100
        if difference > Decimal(str(pressure_limit)) * reference:
            mismatch = difference / reference
            add(
                "gauge_mismatch", "Расхождение показаний давления", "warning",
                "Возможны дрейф калибровки электронного датчика, засорение импульсной линии или неисправность манометра.",
                "Сверить показания на месте и передать приборы и импульсную линию на проверку по регламенту.",
                [f"Показание манометра {gauge:g} МПа", f"Показание SCADA {scada:g} МПа",
                 f"Расхождение {mismatch:.1f}% от показания SCADA"], ["gauge"],
            )

    if vision.get("guard_issue") is True:
        add(
            "coupling_guard", "Нарушение защиты муфты", "critical",
            "Есть признак отсутствия, повреждения или критического смещения кожуха. Возможен контакт с вращающимся валом.",
            "Немедленно сообщить оператору. При подтверждении дефекта эксплуатация запрещена до восстановления ограждения по регламенту.",
            ["Визуальный признак нарушения защитного кожуха"], ["guard"],
        )

    return sorted(findings, key=lambda finding: SEVERITY_ORDER[finding["severity"]])
