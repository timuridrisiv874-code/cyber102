"""Index the organizer's supplied PDF set and its visually verified passport fields."""

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import NamedTemporaryFile, TemporaryDirectory
import unicodedata


SOURCE = "organizer_provided"
PUMP_MODEL = "AMS PCP60S12/3 01 Ex B1 HNBR DTS"
MOTOR_MODEL = "BRA160M4 FБУ1"
GEAR_MODEL = "WXRF87-AM160-5.30-M1-Ø300"
SENSOR_MODEL = "ПТ 0304Ex-ВТ"
TYPE_LABEL = "Одновинтовой эксцентриковый насосный агрегат"
FIELD_LABELS = {
    "serial": "Серийный номер", "model": "Модель", "manufacturer": "Изготовитель",
    "year": "Год выпуска", "technical_specification": "Технические условия", "purpose": "Назначение",
    "design_resource_before_writeoff": "Паспортный ресурс до списания, не менее",
    "design_lifetime": "Паспортный срок службы", "flow_range": "Паспортный диапазон подачи",
    "assigned_resource": "Назначенный ресурс", "assigned_lifetime": "Назначенный срок службы",
    "product_temperature_max_steaming_pump_off": "Макс. температура продукта при пропарке и выключенном насосе",
    "product_density_max": "Макс. плотность продукта", "solid_fraction_max": "Макс. концентрация твёрдой фазы",
    "dimensions": "Габариты Д × Ш × В", "mass": "Масса", "pressure_max": "Давление, до",
    "required_npsh": "Допускаемый кавитационный запас NPSHr",
    "product_temperature_range": "Температура продукта", "manufacture_date": "Дата изготовления",
    "protection_rating": "Степень защиты", "ratio": "Передаточное отношение",
    "max_output_torque": "Макс. крутящий момент на выходе", "max_radial_shaft_load": "Макс. радиальная нагрузка на вал",
    "primary_converter_serial": "Серийный номер первичного преобразователя", "acceptance_date": "Дата приёмки",
}
DOCUMENTS = (
    *[(f"pump-{n}-passport", f"1) Насос №{n} ПС.pdf", f"Паспорт насоса №{n}", "passport") for n in range(1, 5)],
    *[(f"drive-{n}-passports", f"2) Редукт, ЭД, ТПП №{n} ПС.pdf", f"Паспорта редуктора, двигателя и ТПП №{n}", "passport") for n in range(1, 5)],
    ("conformity-declarations", "Декл серт..pdf", "Декларации и сертификаты", "certificate"),
    ("operation-manual", "Инструкция по ЭиО.pdf", "Инструкция по эксплуатации и обслуживанию насосов АМС", "manual"),
    ("gear-motor-manual", "Мотор-редукт. РП.pdf", "Руководство по применению мотор-редуктора", "manual"),
    ("questionnaire-0201", "ОЛ 0201.pdf", "Опросный лист 0201", "questionnaire"),
    ("questionnaire-0601", "ОЛ 0601.pdf", "Опросный лист 0601", "questionnaire"),
    ("document-list", "Перечень док-ов.pdf", "Перечень документов", "index"),
    ("lubricants", "Перечень смазочных мат-ов.pdf", "Перечень смазочных материалов проекта 21031004", "maintenance"),
    ("valve-goreltekh", "Присоед. арматура Горэлтех.pdf", "Присоединительная арматура Горэлтех", "manual"),
    ("motor-manual", "РЭ ЭД.pdf", "Руководство по эксплуатации электродвигателя", "manual"),
    ("assembly-drawings", "СБ, уст. чертеж и др..pdf", "Сборочные и установочные чертежи", "drawing"),
    ("elemer-certificates", "Свид СИ, СС Элемер.pdf", "Свидетельства и сертификаты Элемер", "certificate"),
    ("motor-certificates", "Серты ЭД.pdf", "Сертификаты электродвигателей", "certificate"),
    ("fcr-15-30", "FCR-15-30.pdf", "FCR 15–30", "reference"),
    ("hf-132", "HF до 132 м3-час.pdf", "HF до 132 м³/ч", "reference"),
)
# The structured fields were checked against this exact set of PDF revisions.
EXPECTED_SHA256 = {
    "pump-1-passport": "52b71533cbe41cca80bf72d1baac37b73ccd7f5dee773e6adb1387bb087564f0",
    "pump-2-passport": "e85d57ec02ba1f5a9c60ea4df2ab7697c8cbfcd0f0cd1919856eb4acb59183ae",
    "pump-3-passport": "a889bebd47e44d7fde5473e81fa771e9f2c25870436704d5010ecdacbd9abe21",
    "pump-4-passport": "40dff57cd0097f80f73cf8b4180e3458d25574219a0540660a4cd2ffe685c902",
    "drive-1-passports": "51933d938a3b4e28eeafce88eb665ca21aa9e5a27483ca873b17c14ac74acd41",
    "drive-2-passports": "92f4a87dbbdc59df6a15ae36ef0e0da32ab0060769cad2779b56e4a9ff3f6979",
    "drive-3-passports": "fc7651168288b4b3606a2ae22ce2b678ab3e9f7a93636afd94cd9f9325aaa75a",
    "drive-4-passports": "671882320ac5108469a018eceb1ed8f1516de36082c4bf26992abe189649d593",
    "conformity-declarations": "7d7bc1197f36f8a8842f54eeb391eff05f73c3980094b83762564345e3b7691f",
    "operation-manual": "849c350c671bee44438b0ca5c81a792a9197b408e10865e5ccf715fade9a0b3d",
    "gear-motor-manual": "00ba1669e528cf0522a69950c765f81499941615e1a17a1acd4b770682ffab27",
    "questionnaire-0201": "5281c9696df905eee381c37586e61b3680beefd09299cc55e90b091b673b4dfe",
    "questionnaire-0601": "5106c481d3f5a6d52d3cffe83b942c5461db584bc46dbf1acc4cbdf1debd83ed",
    "document-list": "70031d5404b047ad384cbf43b006423dd4803078a6295b9a9d966b536d76857c",
    "lubricants": "f07eaf35add6f7e7de21b2fad853294a56208edd5744bdf5c772ca5fa2ef2f56",
    "valve-goreltekh": "521393c3b0b0ce489518f845946045f177343b5140aece2b962355bfef069be1",
    "motor-manual": "c9f571c3ce269bd4f8f51cc2d1192efa934b8afe123c2e33dc05b88503df8366",
    "assembly-drawings": "97873cfade9866cad8e440b8bfc17e485e84a47a202386919bf6bc5c4d37d2fa",
    "elemer-certificates": "0fc19e5ef76cf6f37bcc3fef0f109d2b3d4d076abfa308cef27d212a0a24af7c",
    "motor-certificates": "5ac8a6922547bb1aeaf15aec6c747c89446c48a4cdecd79fe45c18f64aff3bda",
    "fcr-15-30": "a219e241fa1d6e7a8255edda32c37950dd05f79c3daa2583ee5413eea0ce3614",
    "hf-132": "b7a6bca19162808516646747c6eef92c6c64333dacaff8a9a6947aaa3a0cb074",
}
SWIFT_OCR = r'''
import Foundation
import Vision

struct Result: Codable {
    let filename: String
    let language: [String]
    let lines: [String]
    let confidence: Double?
    let error: String?
}

let encoder = JSONEncoder()
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
let supported = try request.supportedRecognitionLanguages()
request.recognitionLanguages = ["ru-RU", "en-US"].filter { supported.contains($0) }
request.usesLanguageCorrection = true
for filename in CommandLine.arguments.dropFirst() {
    let handler = VNImageRequestHandler(url: URL(fileURLWithPath: filename), options: [:])
    do {
        try handler.perform([request])
        let candidates = (request.results ?? []).compactMap { $0.topCandidates(1).first }
        let confidence = candidates.isEmpty ? nil : candidates.map { Double($0.confidence) }.reduce(0, +) / Double(candidates.count)
        let result = Result(filename: filename, language: request.recognitionLanguages, lines: candidates.map { $0.string }, confidence: confidence, error: nil)
        print(String(decoding: try encoder.encode(result), as: UTF8.self))
    } catch {
        let result = Result(filename: filename, language: request.recognitionLanguages, lines: [], confidence: nil, error: error.localizedDescription)
        print(String(decoding: try encoder.encode(result), as: UTF8.self))
    }
}
'''


def digest(path):
    with path.open("rb") as stream:
        return sha256_file(stream)


def sha256_file(stream):
    result = sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        result.update(block)
    return result.hexdigest()


def load_sources(source_root):
    sources = {}
    for identifier, filename, title, kind in DOCUMENTS:
        path = source_root / filename
        if not path.is_file():
            raise FileNotFoundError(path)
        source_hash = digest(path)
        if source_hash != EXPECTED_SHA256[identifier]:
            raise ValueError(f"{filename}: PDF differs from the visually verified revision")
        sources[identifier] = (path, title, kind, source_hash)
    return sources


def read_embedded_text(path):
    result = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", str(path), "-"], check=True, capture_output=True)
    pages = result.stdout.decode("utf-8").split("\f")
    if pages[-1] == "":
        pages.pop()
    return [normalize_text(page) for page in pages]


def normalize_text(text):
    return unicodedata.normalize("NFC", "\n".join(line.rstrip() for line in text.splitlines()).strip())


def ocr_rows(lines):
    results = {}
    for line in lines:
        result = json.loads(line)
        match = re.fullmatch(r"([a-z0-9][a-z0-9-]*)-(\d+)\.png", Path(result["filename"]).name)
        if not match:
            raise ValueError(f"Invalid OCR page filename: {result['filename']}")
        key = (match[1], int(match[2]))
        if key in results:
            raise ValueError(f"Duplicate OCR result for {key}")
        results[key] = result
    return results


def run_ocr(sources, text_pages):
    with TemporaryDirectory(prefix="cyber102-pdf-ocr-") as directory:
        directory = Path(directory)
        source = directory / "ocr.swift"
        source.write_text(SWIFT_OCR, encoding="utf-8")
        worker = directory / "ocr"
        subprocess.run(["swiftc", "-module-cache-path", str(directory / "swift-cache"), str(source), "-o", str(worker)], check=True)
        images = []
        for identifier, texts in text_pages.items():
            if all(texts):
                continue
            print(f"Rendering {identifier} for OCR", flush=True)
            path = sources[identifier][0]
            subprocess.run(["pdftoppm", "-scale-to", "1800", "-png", str(path), str(directory / identifier)], check=True)
            for image in sorted(directory.glob(f"{identifier}-*.png")):
                page = int(image.stem.rsplit("-", 1)[1])
                if not texts[page - 1]:
                    images.append(str(image))
        if not images:
            return {}
        result = subprocess.run([str(worker), *images], check=True, capture_output=True, text=True, encoding="utf-8")
        rows = ocr_rows(result.stdout.splitlines())
        if rows and all(row.get("error") for row in rows.values()):
            raise RuntimeError(f"Apple Vision OCR failed for all pages: {next(iter(rows.values()))['error']}")
        return rows


def page_record(identifier, page, native, results):
    if native:
        return {"page": page, "method": "pdf_text", "quality": "readable", "text": native}
    result = results.get((identifier, page), {})
    extracted = normalize_text("\n".join(result.get("lines", [])))
    if not extracted:
        return {"page": page, "method": "unreadable", "quality": "unreadable", "text": ""}
    confidence = result.get("confidence")
    readable = confidence is not None and confidence >= 0.75 and sum(letter.isalpha() for letter in extracted) >= 45
    return {
        "page": page, "method": "ocr", "quality": "readable" if readable else "limited", "text": extracted,
        "ocrEngine": "Apple Vision", "ocrLanguages": result.get("language", []),
        "ocrMeanConfidence": round(confidence, 3) if confidence is not None else None,
    }


def evidence(doc, page, quote):
    return {"documentId": doc, "page": page, "quote": quote}


def field(value, unit, doc, page, quote):
    return {"value": value, "unit": unit, "source": evidence(doc, page, quote)}


def equipment_record(identifier, title, group, kind, fields):
    return {
        "id": identifier, "title": title, "groupNumber": group, "kind": kind,
        **{key: fields[key]["value"] for key in ("serial", "model", "manufacturer")},
        "fields": {key: dict(item, label=FIELD_LABELS[key]) for key, item in fields.items()},
        "sources": [dict(item["source"], field=key, label=FIELD_LABELS[key]) for key, item in fields.items()],
    }


def pump_record(group):
    doc = f"pump-{group}-passport"
    general_page, technical_page = (2, 3) if group == 4 else (3, 4)
    serial = f"21031004-{group}"
    fields = {
        "serial": field(serial, None, doc, 1, f"Серийный номер {serial}"),
        "model": field(PUMP_MODEL, None, doc, 1, PUMP_MODEL),
        "manufacturer": field("ООО «Альтернативные Механические Системы»", None, doc, 1, "ООО «Альтернативные Механические Системы»"),
        "year": field(2023, "год", doc, 1, "Москва, 2023"),
        "technical_specification": field("ТУ3631-001-23430200-2015", None, doc, 1, "ТУ3631-001-23430200-2015"),
        "purpose": field("Перекачивание обводненного нефтепродукта", None, doc, general_page, "перекачивание обводненного нефтепродукта"),
        "design_resource_before_writeoff": field(11000, "ч", doc, general_page, "РЕСУРС ДО СПИСАНИЯ — не менее 11 000 ч"),
        "design_lifetime": field(20, "лет", doc, general_page, "СРОК СЛУЖБЫ — 20 лет"),
        "flow_range": field([5, 20], "м³/ч", doc, technical_page, "Подача — 5–20 м³/ч"),
        "product_temperature_max_steaming_pump_off": field(90, "°C", doc, technical_page, "Макс. температура продукта при проведении пропарки и выключенном насосе — +90 °C"),
        "product_density_max": field(1600, "кг/м³", doc, technical_page, "Макс. плотность — 1600 кг/м³"),
        "solid_fraction_max": field(5, "%", doc, technical_page, "Концентрация твердой фазы — до 5 %"),
        "dimensions": field([2742, 360, 605], "мм", doc, technical_page, "Габариты: Длина х Ширина х Высота — 2742х360х605 мм"),
        "mass": field(430, "кг", doc, technical_page, "Масса — 430 кг"),
    }
    if group != 3:
        # Several rows of unit 3's technical table have faded in the scan.
        fields.update({
            "pressure_max": field(10, "бар", doc, technical_page, "Давление — до 10 бар"),
            "required_npsh": field(2, "м", doc, technical_page, "Допускаемый кавитационный запас NPSHr — 2 м"),
            "product_temperature_range": field([5, 45], "°C", doc, technical_page, "Температура продукта — +5…+45 °C"),
        })
    return equipment_record(f"pump-{group}", f"Насосный агрегат №{group} · {PUMP_MODEL}", group, "pump", fields)


def component_records(group):
    doc = f"drive-{group}-passports"
    motor_page, gear_page, sensor_page = (3, 1, 5) if group == 3 else (1, 3, 6)
    motor_number = ("230201068", "230201069", "230201067", "230201070")[group - 1]
    gear_number = "SAL023020084_00100" + ("2", "3", "4", "1")[group - 1]
    sensor_number = ("10096664", "10096661", "10096663", "10096662")[group - 1]
    probe_number = ("10094328", "10094325", "10094327", "10094326")[group - 1]
    motor_fields = {
        "serial": field(motor_number, None, doc, motor_page, f"Заводской номер {motor_number}"),
        "model": field(MOTOR_MODEL, None, doc, motor_page, f"Двигатель трехфазный асинхронный типа {MOTOR_MODEL}"),
        "manufacturer": field("АО «Ярославский электромашиностроительный завод»", None, doc, motor_page, "Изготовитель — АО «Ярославский электромашиностроительный завод»"),
        "manufacture_date": field("2023-03-01", None, doc, motor_page, "Дата изготовления 2023.03.01"),
        "protection_rating": field("IP55", None, doc, motor_page, "Степень защиты IP 55"),
        "assigned_resource": field(60000, "ч", doc, motor_page + 1, "Назначенный ресурс - 60000 ч."),
        "assigned_lifetime": field(20, "лет", doc, motor_page + 1, "Назначенный срок службы - 20 лет."),
    }
    gear_fields = {
        "serial": field(gear_number, None, doc, gear_page, f"Редуктор {GEAR_MODEL} №{gear_number}"),
        "model": field(GEAR_MODEL, None, doc, gear_page, f"Редуктор {GEAR_MODEL}"),
        "manufacturer": field("WANSHSIN Seikou (Hunan) Co., Ltd", None, doc, gear_page, "Производитель WANSHSIN Seikou (Hunan) Co., Ltd"),
        "ratio": field(5.30, None, doc, gear_page, "Передаточное отношение 5,30"),
        "max_output_torque": field(910, "Н·м", doc, gear_page, "Максимальный крутящий момент на выходе 910 Нм"),
        "max_radial_shaft_load": field(8980, "Н", doc, gear_page, "Максимальная радиальная нагрузка на вал 8980 Н"),
    }
    sensor_fields = {
        "serial": field(sensor_number, None, doc, sensor_page, f"Термопреобразователь прецизионный ПТ 0304Ex-ВТ заводской номер № {sensor_number}"),
        "model": field(SENSOR_MODEL, None, doc, sensor_page, "Термопреобразователь прецизионный ПТ 0304Ex-ВТ"),
        "manufacturer": field("НПП «ЭЛЕМЕР»", None, doc, sensor_page, "упакован научно-производственным предприятием «ЭЛЕМЕР»"),
        "primary_converter_serial": field(probe_number, None, doc, sensor_page, f"первичный преобразователь (ПП) ПТ 0304-ВТ заводской номер № {probe_number}"),
        "acceptance_date": field("2023-03-20", None, doc, sensor_page, "СВИДЕТЕЛЬСТВО О ПРИЕМКЕ — 2023.03.20"),
    }
    return [
        equipment_record(f"motor-{group}", f"Электродвигатель №{group} · {MOTOR_MODEL}", group, "motor", motor_fields),
        equipment_record(f"gearbox-{group}", f"Редуктор №{group} · {GEAR_MODEL}", group, "gearbox", gear_fields),
        equipment_record(f"temperature-transmitter-{group}", f"Термопреобразователь №{group} · {SENSOR_MODEL}", group, "temperature_transmitter", sensor_fields),
    ]


def equipment_catalog():
    groups = [1, 2, 3, 4]
    requirements = [
        {
            "title": "Текущий технический осмотр",
            "text": "Для режима 8 часов в день, 5 суток в неделю руководство АМС указывает еженедельный контроль герметичности и постороннего шума; крепления и затяжку перечисленных монтажных элементов проверяют каждые 3 месяца. Для другого режима нужен согласованный график площадки.",
            "appliesTo": "Серия одновинтовых эксцентриковых насосов АМС, инструкция § 8.1.1",
            "sources": [evidence("operation-manual", 7, "При работе насоса 8 часов в день в течении 5 суток в неделю текущий осмотр подразумевает контроль")],
        },
        {
            "title": "Обслуживание торцевого уплотнения",
            "text": "В разделе об одинарном торцевом уплотнении руководство рекомендует наблюдать за износом пар трения. Раздел о сальниковой набивке относится к другому типу уплотнения.",
            "appliesTo": "Одинарное торцевое уплотнение, инструкция АМС § 8.1.5",
            "sources": [evidence("operation-manual", 8, "Рекомендуется вести наблюдение только за износом пар трения поз.2,6")],
        },
        {
            "title": "Масло редуктора",
            "text": "Перечень проекта указывает синтетическое масло VG220 / Synoil, 3,7 л. Тип и объём относятся к указанному исполнению редуктора.",
            "appliesTo": f"{GEAR_MODEL}, проект 21031004",
            "sources": [evidence("lubricants", 1, "Синтетическое масло VG220 / Synoil — 3,7 л")],
        },
        {
            "title": "Смазочные материалы соединения вала и шарниров",
            "text": "Для соединения вала редуктора с полым валом указан BECHEM Berulub 932 Antiseize Paste. Для двух шарнирных узлов указан Bechem Berutox FE 18 EP. Количество в перечне не указано.",
            "appliesTo": "Насосные агрегаты проекта 21031004",
            "sources": [evidence("lubricants", 1, "BECHEM Berulub 932 Antiseize Paste"), evidence("lubricants", 1, "Bechem Berutox FE 18 EP")],
        },
    ]
    equipment = []
    for group in groups:
        pump = pump_record(group)
        pump["typeLabel"] = TYPE_LABEL
        equipment.extend([pump, *component_records(group)])
    return {
        "schemaVersion": 1, "source": SOURCE, "equipmentType": "progressive_cavity", "typeLabel": TYPE_LABEL,
        "applicableDiagnostics": "not_validated", "equipment": equipment, "requirements": requirements,
        "limitations": [
            "Номер группы отражает комплектность переданных PDF. Монтажные позиции и замены на площадке не подтверждены.",
            "Связи паспортов с фотографиями, XLSX телеметрии и демонстрационным P-102 не установлены.",
            "Ресурс до списания и срок службы приведены из паспорта. Фактическая наработка и остаточный ресурс отсутствуют.",
            "Диагностические связки и интервалы 4000, 8000 и 16000 ч требуют проверки применимости для этого оборудования.",
            "У насоса №3 строки давления, NPSHr и рабочего диапазона температуры выцвели, поэтому поля этих параметров оставлены пустыми.",
            "Цитаты свёрнутых строк таблиц сохраняют значения и единицы. При разночтении используйте указанную страницу оригинала.",
        ],
    }


def copy_pdf(path, destination, source_hash):
    if destination.exists() and digest(destination) == source_hash:
        return
    with NamedTemporaryFile(dir=destination.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        shutil.copy2(path, temporary_path)
        if digest(temporary_path) != source_hash:
            raise ValueError(f"{path.name}: SHA-256 changed during copy")
        temporary_path.replace(destination)
    finally:
        temporary_path.unlink(missing_ok=True)


def write_json(path, value):
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
        try:
            json.dump(value, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise
    temporary_path.replace(path)


def import_documents(source_root, output_dir, *, ocr_cache=None, text_only=False):
    sources = load_sources(source_root)
    text_pages = {identifier: read_embedded_text(path) for identifier, (path, _, _, _) in sources.items()}
    if ocr_cache is not None:
        with ocr_cache.open(encoding="utf-8") as cache:
            results = ocr_rows(cache)
    elif text_only:
        results = {}
    else:
        results = run_ocr(sources, text_pages)
    expected_ocr = {
        (identifier, page) for identifier, pages in text_pages.items()
        for page, native in enumerate(pages, start=1) if not native
    }
    if not text_only and (missing := expected_ocr - results.keys()):
        raise ValueError(f"Missing OCR results for pages {sorted(missing)}")
    output_dir.mkdir(parents=True, exist_ok=True)
    raw = output_dir / "documents"
    raw.mkdir(exist_ok=True)
    entries = []
    methods = Counter()
    quality = Counter()
    for identifier, (path, title, kind, source_hash) in sources.items():
        pages = [page_record(identifier, index, value, results) for index, value in enumerate(text_pages[identifier], start=1)]
        methods.update(page["method"] for page in pages)
        quality.update(page["quality"] for page in pages)
        copy_pdf(path, raw / f"{source_hash}.pdf", source_hash)
        entries.append({
            "id": identifier, "title": title, "filename": path.name, "sha256": source_hash,
            "bytes": path.stat().st_size, "kind": kind, "relativePath": f"documents/{source_hash}.pdf",
            "url": f"/api/customer/documents/{identifier}/pdf", "pageCount": len(pages), "pages": pages,
        })
    summary = {
        "documents": len(entries), "pages": sum(methods.values()),
        "extractionMethods": dict(sorted(methods.items())), "pageQuality": dict(sorted(quality.items())),
    }
    index = {"schemaVersion": 1, "source": SOURCE, "summary": summary, "documents": entries}
    write_json(output_dir / "documents.json", index)
    write_json(output_dir / "equipment-passports.json", equipment_catalog())
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True, help="Folder with the organizer PDF set")
    parser.add_argument("--output-dir", type=Path, default=Path("data/customer"))
    extraction = parser.add_mutually_exclusive_group()
    extraction.add_argument("--ocr-cache", type=Path, help="Replay Apple Vision page results in JSONL format")
    extraction.add_argument("--text-only", action="store_true", help="Index native text only and mark scans unreadable")
    args = parser.parse_args()
    summary = import_documents(args.source_root, args.output_dir, ocr_cache=args.ocr_cache, text_only=args.text_only)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
