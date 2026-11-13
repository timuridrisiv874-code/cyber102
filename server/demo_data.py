"""Synthetic pump records for rehearsing the first checkpoint."""

PASSPORT = {
    "serial": "P-102",
    "model": "Д 160-1126",
    "name": "Центробежный насос",
    "serviceLife": "По паспорту изготовителя",
    "nextService": "По наработке и регламенту ТО",
    "source": "Демонстрационные данные",
    "thresholds": {"service_intervals_hours": [4000, 8000, 16000]},
}


_BASE_TELEMETRY = {
    "vibration": "normal",
    "temperature": "normal",
    "current": "normal",
    "broadband_vibration": "normal",
    "high_frequency_vibration": "normal",
    "inlet_pressure": "normal",
    "axial_vibration": "normal",
    "coupling_vibration": "normal",
    "low_frequency_vibration": "normal",
    "one_x_dominant": False,
    "efficiency_drop_pct": 0,
    "hours": 3800,
    "previous_hours": 3799,
    "bearing_overheat_sustained": False,
    "bearing_temperature_delta_c": 0,
    "phase_imbalance_pct": 1.5,
    "terminal_temperature_rising": False,
    "discharge_pressure": 0.8,
    "sound_sensor_present": False,
}

_BASE_VISION = {
    "seal_leak": False,
    "foundation_issue": False,
    "oil_issue": False,
    "terminal_damage": False,
    "gauge_pressure": 0.8,
    "guard_issue": False,
    "flow_unstable": False,
}

# Normalized coordinates on the pump schematic, not on camera frames.
_ZONES = {
    "inlet": {"x": 0.04, "y": 0.41, "width": 0.20, "height": 0.17},
    "pump": {"x": 0.22, "y": 0.29, "width": 0.22, "height": 0.43},
    "seal": {"x": 0.40, "y": 0.43, "width": 0.06, "height": 0.13},
    "bearing": {"x": 0.45, "y": 0.36, "width": 0.11, "height": 0.26},
    "coupling": {"x": 0.55, "y": 0.43, "width": 0.13, "height": 0.14},
    "guard": {"x": 0.54, "y": 0.32, "width": 0.15, "height": 0.32},
    "motor": {"x": 0.69, "y": 0.31, "width": 0.24, "height": 0.34},
    "terminal": {"x": 0.73, "y": 0.17, "width": 0.14, "height": 0.15},
    "anchors": {"x": 0.15, "y": 0.73, "width": 0.76, "height": 0.12},
    "oil": {"x": 0.40, "y": 0.30, "width": 0.10, "height": 0.10},
    "gauge": {"x": 0.25, "y": 0.13, "width": 0.10, "height": 0.18},
}


def _scenario(scenario_id, label, *, telemetry=None, vision=None):
    return {
        "id": scenario_id,
        "label": label,
        "telemetry": {**_BASE_TELEMETRY, **(telemetry or {})},
        "vision": {**_BASE_VISION, **(vision or {})},
        "zones": _ZONES,
    }


SCENARIOS = [
    _scenario("healthy", "Показания в норме"),
    _scenario(
        "bearing_wear", "Рост вибрации и температуры",
        telemetry={"vibration": "rising", "temperature": "rising"},
    ),
    _scenario(
        "cavitation", "Падение давления на всасывании",
        telemetry={"broadband_vibration": "rising", "inlet_pressure": "falling"},
    ),
    _scenario(
        "misalignment", "Высокая осевая вибрация муфты",
        telemetry={"axial_vibration": "high", "current": "slightly_high"},
    ),
    _scenario(
        "hydraulic_wear", "Снижение КПД на 15%",
        telemetry={"efficiency_drop_pct": 15},
    ),
    _scenario(
        "service_interval", "Достигнут интервал 8000 моточасов",
        telemetry={"previous_hours": 7999, "hours": 8000},
    ),
    _scenario(
        "seal_leak", "Утечка в зоне уплотнения и перегрев",
        telemetry={"vibration": "rising", "temperature": "rising", "current": "slightly_high"},
        vision={"seal_leak": True},
    ),
    _scenario(
        "foundation_looseness", "Ослабление крепления к фундаменту",
        telemetry={"low_frequency_vibration": "rising", "one_x_dominant": True},
        vision={"foundation_issue": True},
    ),
    _scenario(
        "lubrication_loss", "Проблема с маслом и перегрев подшипника",
        telemetry={"bearing_overheat_sustained": True, "bearing_temperature_delta_c": 12},
        vision={"oil_issue": True},
    ),
    _scenario(
        "electrical_contact", "Следы перегрева на клеммной коробке",
        telemetry={"phase_imbalance_pct": 7},
        vision={"terminal_damage": True},
    ),
    _scenario(
        "pressure_mismatch", "Манометр и SCADA показывают разное давление",
        vision={"gauge_pressure": 0.96},
    ),
    _scenario("coupling_guard", "Отсутствует защитный кожух", vision={"guard_issue": True}),
    _scenario(
        "cavitation_confirmed", "Падение давления и признаки кавитации",
        telemetry={
            "inlet_pressure": "falling",
            "broadband_vibration": "rising",
            "high_frequency_vibration": "rising",
            "sound_sensor_present": True,
            "gravel_noise": True,
        },
        vision={"flow_unstable": True},
    ),
    _scenario(
        "multiple_findings", "Кожух, утечка и регламентное ТО",
        telemetry={"vibration": "rising", "temperature": "rising", "previous_hours": 15999, "hours": 16001},
        vision={"guard_issue": True, "seal_leak": True},
    ),
]
