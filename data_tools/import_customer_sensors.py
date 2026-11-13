"""Import the customer vibration and temperature sample into traceable JSON.

Install openpyxl in the import environment. The resulting JSON is dependency-free.
"""

import argparse
from collections import Counter
from datetime import date, datetime, timedelta
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from unicodedata import normalize
from xml.etree import ElementTree
from zipfile import ZipFile


WORD_NAMESPACE = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
HOURLY = timedelta(hours=1)
VIBRATION_COLUMNS = (
    "Дата и время", "Обороты (об/мин)", "СКЗ общее (мм/с)", "Ампл. 1x (мм/с)",
    "Ампл. 2x (мм/с)", "ВЧ шум (g)", "Диагноз по вибрации",
)
TEMPERATURE_COLUMNS = (
    "Дата и время", "Обороты (об/мин)", "Темп. подшипника насоса (°C)",
    "Темп. обмоток ЭД (°C)", "Диагноз по температуре",
)
VIBRATION_KEYS = ("rms_mm_s", "one_x_mm_s", "two_x_mm_s", "high_frequency_g")
TEMPERATURE_KEYS = ("bearing_c", "winding_c")
SEVERITY = {"alarm": 3, "warning": 2, "below_threshold": 1, "unknown": 0}
LABELS = {
    "one_x": {"warning": "Пред: Рост дисбаланса", "alarm": "Тревога: Выс. дисбаланс (РК)"},
    "two_x": {"warning": "Пред: Ухудшение центровки", "alarm": "Тревога: Расцентровка"},
    "high_frequency": {"warning": "Пред: Износ/Шум"},
    "bearing": {"warning": "Пред: Рост темп. подш.", "alarm": "Тревога: Перегрев подш. насоса"},
    "winding": {"warning": "Пред: Рост темп. ЭД", "alarm": "Тревога: Перегрев обмоток ЭД"},
}


def find_file(source_root, folder, suffix):
    paths = sorted((source_root / folder).glob(f"*.{suffix}"))
    if len(paths) != 1:
        raise ValueError(f"Expected exactly one .{suffix} file in {folder}; found {len(paths)}")
    return paths[0]


def source_record(identifier, path, source_root):
    with path.open("rb") as file:
        digest = sha256()
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return {
        "id": identifier,
        "file": normalize("NFC", path.relative_to(source_root).as_posix()),
        "sha256": digest.hexdigest(),
        "bytes": path.stat().st_size,
    }


def read_description(path):
    with ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    return [
        "".join(part.text or "" for part in paragraph.iter(f"{WORD_NAMESPACE}t"))
        for paragraph in root.iter(f"{WORD_NAMESPACE}p")
    ]


def threshold_rule(paragraphs, source, pattern, unit, baseline_prefix=None):
    matches = [
        (number, text, match)
        for number, text in enumerate(paragraphs, start=1)
        if (match := re.search(pattern, text, re.IGNORECASE))
    ]
    if len(matches) != 1:
        raise ValueError(f"{source['file']}: expected a unique paragraph for {pattern}")
    number, text, match = matches[0]
    warning, *other = [float(value.replace(",", ".")) for value in match.groups()]
    alarm = other[0] if other else None
    rule = {
        "unit": unit, "operator": ">", "warning": warning, "alarm": alarm,
        "source": {"sourceId": source["id"], "file": source["file"], "paragraph": number, "text": text},
    }
    if baseline_prefix:
        for index, paragraph in enumerate(paragraphs, start=1):
            baseline = re.search(r"~\s*(\d+(?:[.,]\d+)?)\s*°C", paragraph)
            if paragraph.startswith(baseline_prefix) and baseline:
                rule["approximateBaseline"] = float(baseline[1].replace(",", "."))
                rule["baselineSource"] = {
                    "sourceId": source["id"], "file": source["file"], "paragraph": index, "text": paragraph,
                }
                break
        else:
            raise ValueError(f"{source['file']}: expected documented baseline for {baseline_prefix}")
    return rule


def read_thresholds(vibration_path, temperature_path, sources):
    vibration = read_description(vibration_path)
    temperature = read_description(temperature_path)
    vibration_source = sources["vibration_description"]
    temperature_source = sources["temperature_description"]
    number = r"([0-9]+(?:[.,][0-9]+)?)"
    return {
        "vibration": {
            "rms_overall": {
                "unit": "mm/s", "operator": None, "warning": None, "alarm": None,
                "source": {"sourceId": vibration_source["id"], "file": vibration_source["file"]},
            },
            "one_x": threshold_rule(
                vibration, vibration_source, rf"1x выше {number}\s*мм/с.*тревога при >\s*{number}", "mm/s",
            ),
            "two_x": threshold_rule(
                vibration, vibration_source, rf"2x выше {number}\s*мм/с.*тревога при >\s*{number}", "mm/s",
            ),
            "high_frequency": threshold_rule(
                vibration, vibration_source, rf"ВЧ шума выше {number}\s*g", "g",
            ),
        },
        "temperature": {
            "bearing": threshold_rule(
                temperature, temperature_source,
                rf"^Подшипник:.*выше {number}\s*°C.*выше {number}\s*°C", "°C",
                baseline_prefix="Темп. подшипника насоса",
            ),
            "winding": threshold_rule(
                temperature, temperature_source,
                rf"^Электродвигатель.*предупреждение при >\s*{number}\s*°C.*тревогу при >\s*{number}\s*°C",
                "°C", baseline_prefix="Темп. обмоток ЭД",
            ),
        },
    }


def timestamp(value):
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value)
    else:
        raise ValueError("timestamp is missing or not a date and time")
    if parsed.tzinfo is not None:
        raise ValueError("this sample expects timestamps without an offset")
    return parsed


def numeric(value):
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"expected a finite numeric cell, received {value!r}")
    return value


def read_sheet(path, source, sheet_name, headers, keys):
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=False, keep_links=False)
    try:
        if workbook.sheetnames != [sheet_name]:
            raise ValueError(f"{source['file']}: unexpected sheets {workbook.sheetnames}")
        sheet = workbook[sheet_name]
        if sheet.sheet_state != "visible":
            raise ValueError(f"{source['file']}: expected a visible sheet")
        rows = iter(sheet.iter_rows())
        header = tuple(cell.value for cell in next(rows, ()))
        if header != headers:
            raise ValueError(f"{source['file']}: unexpected header {header!r}")
        records = {}
        empty_rows = 0
        for number, cells in enumerate(rows, start=2):
            values = [cell.value for cell in cells]
            if all(value is None for value in values):
                empty_rows += 1
                continue
            if any(cell.data_type in {"f", "e"} for cell in cells if cell.value is not None):
                raise ValueError(f"{source['file']} {sheet_name}:{number}: formula or Excel error")
            try:
                when = timestamp(values[0])
                rpm = numeric(values[1])
                readings = {key: numeric(value) for key, value in zip(keys, values[2:-1], strict=True)}
            except ValueError as error:
                raise ValueError(f"{source['file']} {sheet_name}:{number}: {error}") from error
            if when in records:
                raise ValueError(f"{source['file']} {sheet_name}:{number}: duplicate timestamp {when}")
            if values[-1] is not None and not isinstance(values[-1], str):
                raise ValueError(f"{source['file']} {sheet_name}:{number}: source label must be text")
            records[when] = {
                "rpm": rpm,
                "readings": {**readings, "source_label": values[-1]},
                "provenance": {"sourceId": source["id"], "file": source["file"], "sheet": sheet_name, "row": number},
            }
        properties = workbook.properties
        source.update({
            "sheet": sheet_name, "headerRow": 1, "rowCount": len(records), "emptyRows": empty_rows,
            "formulaCells": 0, "columns": list(headers), "creator": properties.creator,
            "created": properties.created.isoformat() if properties.created else None,
            "modified": properties.modified.isoformat() if properties.modified else None,
        })
        if not records:
            raise ValueError(f"{source['file']}: no data rows")
        if list(records) != sorted(records):
            raise ValueError(f"{source['file']}: timestamps are not ordered")
        return records
    finally:
        workbook.close()


def assessment(value, rule):
    if value is None or rule["warning"] is None:
        level = "unknown"
    elif rule["alarm"] is not None and value > rule["alarm"]:
        level = "alarm"
    elif value > rule["warning"]:
        level = "warning"
    else:
        level = "below_threshold"
    return {"level": level, "value": value, "unit": rule["unit"]}


def assess_group(readings, thresholds, mapping):
    channels = {name: assessment(readings[key], thresholds[name]) for name, key in mapping.items()}
    channel, result = max(channels.items(), key=lambda item: SEVERITY[item[1]["level"]])
    level = result["level"]
    missing_threshold_inputs = any(
        channels[name]["value"] is None for name in channels if thresholds[name]["warning"] is not None
    )
    if level in {"warning", "alarm"}:
        computed_label = LABELS[channel][level]
    elif level == "below_threshold" and not missing_threshold_inputs:
        computed_label = "Норма"
    else:
        computed_label = None
    source_label = readings["source_label"]
    disagreement = computed_label != source_label if None not in (computed_label, source_label) else None
    return {**channels, "computed_label": computed_label, "disagreement": disagreement}


def join_measurements(vibration, temperature, thresholds):
    if set(vibration) != set(temperature):
        raise ValueError("Vibration and temperature timestamps differ; verify the source relationship before import")
    rows = []
    for when, v in vibration.items():
        t = temperature[when]
        if v["rpm"] is None or t["rpm"] is None or v["rpm"] != t["rpm"]:
            raise ValueError(f"RPM is missing or inconsistent at {when.isoformat()}")
        v_readings = v["readings"]
        t_readings = t["readings"]
        rows.append({
            "timestamp": when.isoformat(timespec="seconds"),
            "rpm": v["rpm"],
            "vibration": v_readings,
            "temperature": t_readings,
            "assessments": {
                "vibration": assess_group(v_readings, thresholds["vibration"], {
                    "one_x": "one_x_mm_s", "two_x": "two_x_mm_s",
                    "high_frequency": "high_frequency_g", "rms_overall": "rms_mm_s",
                }),
                "temperature": assess_group(t_readings, thresholds["temperature"], {
                    "bearing": "bearing_c", "winding": "winding_c",
                }),
            },
            "provenance": {"vibration": v["provenance"], "temperature": t["provenance"]},
        })
    return rows


def ranges(values):
    present = [value for value in values if value is not None]
    return {
        "min": min(present) if present else None, "max": max(present) if present else None,
        "count": len(present), "missing": len(values) - len(present),
    }


def summarize_group(rows, group, metrics, channels):
    raw = [row[group] for row in rows]
    computed = [row["assessments"][group] for row in rows]
    return {
        "ranges": {key: ranges([record[key] for record in raw]) for key in metrics},
        "sourceLabelCounts": dict(Counter(record["source_label"] or "unknown" for record in raw)),
        "computedLabelCounts": dict(Counter(record["computed_label"] or "unknown" for record in computed)),
        "assessmentCounts": {
            key: dict(Counter(record[key]["level"] for record in computed)) for key in channels
        },
        "labelDisagreements": sum(record["disagreement"] is True for record in computed),
    }


def build_dataset(source_root, as_of):
    paths = {
        "vibration_data": find_file(source_root, "Вибрация", "xlsx"),
        "temperature_data": find_file(source_root, "Температура", "xlsx"),
        "vibration_description": find_file(source_root, "Вибрация", "docx"),
        "temperature_description": find_file(source_root, "Температура", "docx"),
    }
    sources = {key: source_record(key, path, source_root) for key, path in paths.items()}
    thresholds = read_thresholds(paths["vibration_description"], paths["temperature_description"], sources)
    vibration = read_sheet(
        paths["vibration_data"], sources["vibration_data"], "Вибрация", VIBRATION_COLUMNS, VIBRATION_KEYS,
    )
    temperature = read_sheet(
        paths["temperature_data"], sources["temperature_data"], "Термометрия", TEMPERATURE_COLUMNS,
        TEMPERATURE_KEYS,
    )
    rows = join_measurements(vibration, temperature, thresholds)
    dates = list(vibration)
    nonhourly = sum(end - start != HOURLY for start, end in zip(dates, dates[1:]))
    dataset = {
        "id": "customer-sensors-example",
        "source": "customer_example",
        "equipmentSerial": None,
        "timezone": None,
        "intervalHours": 1 if len(dates) > 1 and not nonhourly else None,
        "relationshipNote": (
            "Две книги синхронизированы по совпадающим дате, времени и оборотам. "
            "Серийный номер и связь с физическим насосом, P-102 или фотографией не подтверждены."
        ),
    }
    summary = {
        "rowCount": len(rows), "firstTimestamp": rows[0]["timestamp"], "lastTimestamp": rows[-1]["timestamp"],
        "matchedTimestamps": len(rows), "matchedRpm": len(rows), "nonHourlyTransitions": nonhourly,
        "asOfLocalDate": as_of.isoformat(), "futureDatedRows": sum(when.date() > as_of for when in dates),
        "rpm": ranges([row["rpm"] for row in rows]),
        "vibration": summarize_group(rows, "vibration", VIBRATION_KEYS, tuple(thresholds["vibration"])),
        "temperature": summarize_group(rows, "temperature", TEMPERATURE_KEYS, tuple(thresholds["temperature"])),
    }
    source_records = list(sources.values())
    data = {
        "schemaVersion": 1, "dataset": dataset, "thresholds": thresholds,
        "sources": source_records, "summary": summary, "rows": rows,
    }
    report = {
        "schemaVersion": 1, "dataset": dataset, "sources": source_records, "summary": summary,
        "thresholds": thresholds,
        "labelDisagreements": [
            {
                "group": group, "timestamp": row["timestamp"], "sourceLabel": row[group]["source_label"],
                "computedLabel": row["assessments"][group]["computed_label"], "provenance": row["provenance"][group],
            }
            for row in rows for group in ("vibration", "temperature") if row["assessments"][group]["disagreement"]
        ],
        "limitations": [
            "Уставки извлечены из DOCX заказчика и относятся к примеру. Применимость к оборудованию требует паспорта.",
            "Исходные даты без часового пояса. Строки после контрольной календарной даты сохранены.",
            "Для общего СКЗ не задана уставка, поэтому его уровень unknown.",
            "Для ВЧ ускорения описано предупреждение выше 2.5 g. Порог тревоги отсутствует. "
            "Метки тревоги из XLSX сохранены и отмечены как расхождение.",
            "Метки XLSX описывают вывод примера. Подтверждение фактических дефектов или способ формирования не предоставлены.",
            "Фазные токи, давление, КПД, моточасы, изображения, ориентация датчика и сырой спектр отсутствуют.",
        ],
    }
    return data, report


def write_json(path, data):
    with NamedTemporaryFile(dir=path.parent, encoding="utf-8", mode="w", delete=False) as temporary:
        temporary_path = Path(temporary.name)
        try:
            json.dump(data, temporary, ensure_ascii=False, allow_nan=False, indent=2)
            temporary.write("\n")
        except BaseException:
            temporary.close()
            temporary_path.unlink()
            raise
    temporary_path.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True, help="Directory with the two supplied folders")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 11, 13),
                        help="Reference calendar date for future rows, default 2026-11-13")
    args = parser.parse_args()
    data, report = build_dataset(args.source_root, args.as_of)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.output_dir / "sensors.json", data)
    write_json(args.output_dir / "sensor-import-report.json", report)
    print(f"Imported {data['summary']['rowCount']} synchronized rows into {args.output_dir}")


if __name__ == "__main__":
    main()
