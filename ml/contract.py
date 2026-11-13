"""Dataset validation and asset-disjoint, forward-in-time splits."""

import csv
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


FEATURES = (
    "vibration_rms_mm_s",
    "broadband_vibration_rms_mm_s",
    "axial_vibration_rms_mm_s",
    "vibration_1x_mm_s",
    "vibration_high_frequency_g",
    "bearing_temperature_c",
    "bearing_temperature_delta_c",
    "motor_current_a",
    "phase_imbalance_pct",
    "inlet_pressure_mpa",
    "discharge_pressure_mpa",
    "flow_m3_h",
    "efficiency_pct",
    "efficiency_drop_pct",
    "hours",
    "shaft_speed_rpm",
)
LABELS = ("bearing_fault", "cavitation", "shaft_misalignment", "hydraulic_wear", "foundation_looseness")
PARTITIONS = ("train", "validation", "test")
METADATA = ("serial", "timestamp", "source")
FIELDS = set(METADATA + FEATURES + LABELS)


@dataclass(frozen=True)
class Observation:
    serial: str
    timestamp: datetime
    features: tuple[float, ...]
    labels: dict[str, int | None]


def utc_timestamp(value):
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO 8601 timestamp with a time zone")
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if instant.tzinfo is None:
            raise ValueError
        return instant.astimezone(timezone.utc)
    except (ValueError, OverflowError) as error:
        raise ValueError("timestamp must be an ISO 8601 timestamp with a time zone") from error


def serial_number(value):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError("serial must be a nonempty string without surrounding whitespace")
    return value


def unique_fields(pairs):
    result = {}
    for field, value in pairs:
        if field in result:
            raise ValueError(f"duplicate JSON field {field!r}")
        result[field] = value
    return result


def observation_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as data:
        if path.suffix.lower() == ".csv":
            reader = csv.DictReader(data, strict=True)
            columns = reader.fieldnames or []
            if len(columns) != len(set(columns)) or not set(METADATA).issubset(columns):
                raise ValueError("CSV needs unique columns including serial, timestamp and source")
            if set(columns) - FIELDS:
                raise ValueError(f"unknown CSV columns: {sorted(set(columns) - FIELDS)}")
            for row in reader:
                yield reader.line_num, row
        elif path.suffix.lower() == ".jsonl":
            for line_number, line in enumerate(data, 1):
                if line.strip():
                    try:
                        yield line_number, json.loads(line, object_pairs_hook=unique_fields)
                    except ValueError as error:
                        raise ValueError(f"row {line_number}: {error}") from error
        else:
            raise ValueError("data file must have extension .csv or .jsonl")


def read_feature(field, value, *, csv_input):
    if value is None or (csv_input and value == ""):
        return math.nan
    if type(value) not in (float, int) and not (csv_input and isinstance(value, str)):
        raise ValueError(f"{field} must be a finite number or missing")
    try:
        result = float(value)
    except (ValueError, OverflowError) as error:
        raise ValueError(f"{field} must be a finite number or missing") from error
    if not math.isfinite(result):
        raise ValueError(f"{field} must be a finite number or missing")
    return result


def read_label(field, value, *, csv_input):
    if value is None or (csv_input and value == ""):
        return None
    if csv_input and value in ("0", "1"):
        return int(value)
    if type(value) is int and value in (0, 1):
        return value
    raise ValueError(f"{field} must be 0, 1 or missing")


def read_observations(path: Path):
    seen = set()
    observations = []
    csv_input = path.suffix.lower() == ".csv"
    for line, row in observation_rows(path):
        try:
            if not isinstance(row, dict) or row.keys() - FIELDS:
                raise ValueError("record must use only fields from the published schema")
            if csv_input and any(value is None for value in row.values()):
                raise ValueError("CSV row has fewer values than columns")
            if row.get("source") != "field":
                raise ValueError("source must be field. Demonstration and synthetic records are excluded")
            serial = serial_number(row.get("serial"))
            instant = utc_timestamp(row.get("timestamp"))
            key = (serial, instant)
            if key in seen:
                raise ValueError("duplicate serial and timestamp after UTC normalization")
            seen.add(key)
            features = tuple(read_feature(field, row.get(field), csv_input=csv_input) for field in FEATURES)
            if all(math.isnan(value) for value in features):
                raise ValueError("at least one measurement is required")
            labels = {field: read_label(field, row.get(field), csv_input=csv_input) for field in LABELS}
            observations.append(Observation(serial, instant, features, labels))
        except ValueError as error:
            raise ValueError(f"row {line}: {error}") from error
    if not observations:
        raise ValueError("dataset contains no observations")
    return sorted(observations, key=lambda row: (row.timestamp, row.serial))


def split_observations(observations, manifest):
    required = {"validation_from", "test_from", *PARTITIONS}
    if not isinstance(manifest, dict) or manifest.keys() != required:
        raise ValueError("split needs train, validation, test, validation_from and test_from")
    validation_from = utc_timestamp(manifest["validation_from"])
    test_from = utc_timestamp(manifest["test_from"])
    if validation_from >= test_from:
        raise ValueError("validation_from must be before test_from")
    assets = {}
    for partition in PARTITIONS:
        serials = manifest[partition]
        if not isinstance(serials, list) or not serials:
            raise ValueError(f"{partition} must list at least one asset serial")
        for raw in serials:
            serial = serial_number(raw)
            if serial in assets:
                raise ValueError(f"serial {serial!r} occurs more than once in split")
            assets[serial] = partition

    partitions = {partition: [] for partition in PARTITIONS}
    used_assets = set()
    for observation in observations:
        time_partition = "train" if observation.timestamp < validation_from else (
            "validation" if observation.timestamp < test_from else "test"
        )
        if assets.get(observation.serial) == time_partition:
            partitions[time_partition].append(observation)
            used_assets.add(observation.serial)
    unused = assets.keys() - used_assets
    if unused:
        raise ValueError(f"no observations within the assigned time window for serials: {', '.join(sorted(unused))}")
    return partitions


def labeled_partitions(partitions, labels):
    result = {}
    for label in labels:
        result[label] = {}
        for partition, observations in partitions.items():
            labeled = [observation for observation in observations if observation.labels[label] is not None]
            if {row.labels[label] for row in labeled} != {0, 1}:
                raise ValueError(f"{label}: {partition} must contain both independently verified classes")
            result[label][partition] = labeled
    return result
