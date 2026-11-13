"""Compare an isolated llama.cpp InternVL3 candidate on Cyber102 smoke fixtures."""

import argparse
import base64
from copy import deepcopy
from io import BytesIO
import json
import os
from pathlib import Path
from statistics import median
from time import perf_counter
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from PIL import Image, ImageDraw, ImageOps

from server.vision import SCHEMA, result_from_observation
from vision_runtime.worker import PROMPT

ROOT = Path(__file__).resolve().parents[2]
CONFIG = json.loads(Path(__file__).with_name("gguf-config.json").read_text(encoding="utf-8"))
ENDPOINT = "http://127.0.0.1:11437/v1/chat/completions"
NEGATIVE_CASES = frozenset(("blank", "text_only", "person", "furniture"))

RUSSIAN_PROMPT = '''Осмотри приложенный кадр. Сначала проверь, виден ли настоящий насос, мотор, муфта, труба или подшипник. На белом кадре, листе с текстом или кадре без оборудования верни {"component":"none","condition":"uncertain","box":[0,0,0,0],"observation":""}. Слово «насос» на бумаге не является насосом.
Если оборудование видно, укажи component из pump, motor, coupling, pipe, bearing. Не подставляй выдуманные координаты. Рамка box=[лево,верх,право,низ] охватывает выбранную деталь в координатах 0–1000. При явном отсутствии повреждения condition=no_visible_defect. При сомнении condition=uncertain. Используй corrosion только когда видна ржавчина, wet_patch только когда видна жидкость, visible_damage только при видимом повреждении. Цвет краски и тени не ржавчина. Текст observation напиши только по-русски, до 15 слов. Верни ровно один JSON с четырьмя полями.'''


def write_negative_fixtures(directory):
    directory.mkdir(parents=True, exist_ok=True)
    blank = Image.new("RGB", (640, 480), "white")
    blank.save(directory / "blank.jpg", quality=85)

    words = blank.copy()
    ImageDraw.Draw(words).text((130, 180), "INDUSTRIAL PUMP\n\nShow only a valid inspection.", fill="black")
    words.save(directory / "text-only.jpg", quality=85)

    person = Image.new("RGB", (640, 480), "#eeeeea")
    draw = ImageDraw.Draw(person)
    draw.ellipse((265, 58, 375, 168), fill="#9d6f4c")
    draw.rounded_rectangle((230, 168, 410, 358), radius=32, fill="#2c5079")
    draw.rounded_rectangle((182, 182, 250, 350), radius=22, fill="#9d6f4c")
    draw.rounded_rectangle((390, 182, 458, 350), radius=22, fill="#9d6f4c")
    draw.rectangle((245, 340, 307, 479), fill="#34413a")
    draw.rectangle((333, 340, 395, 479), fill="#34413a")
    person.save(directory / "person.jpg", quality=85)

    furniture = Image.new("RGB", (640, 480), "#e3dfd1")
    draw = ImageDraw.Draw(furniture)
    draw.rectangle((0, 390, 639, 479), fill="#b39b81")
    draw.rounded_rectangle((223, 81, 418, 260), radius=18, fill="#8d5d37")
    draw.polygon([(194, 247), (397, 247), (445, 293), (235, 293)], fill="#ae7845")
    for x, y in [(215, 279), (411, 281), (251, 293), (412, 293)]:
        draw.rectangle((x, y, x + 19, y + 132), fill="#73472d")
    furniture.save(directory / "furniture.jpg", quality=85)


def select_fixtures(args):
    cases = {
        "pump": ROOT / "web/demo-pump.jpg",
        "clean": ROOT / "web/demo-pump-clean.jpg",
        "blank": args.fixtures / "blank.jpg",
        "text_only": args.fixtures / "text-only.jpg",
    }
    if args.expanded:
        cases.update(person=args.fixtures / "person.jpg", furniture=args.fixtures / "furniture.jpg")
    cases["warman"] = args.warman or ROOT / "data/raw/unlabelled" / CONFIG["warman_file"]
    missing = [str(path) for path in cases.values() if not path.is_file()]
    if missing:
        raise SystemExit("Missing fixtures: " + ", ".join(missing))
    return cases


def image_uri(path):
    with Image.open(path) as source:
        normalized = ImageOps.exif_transpose(source).convert("RGB")
        normalized.thumbnail((1280, 1280))
        data = BytesIO()
        normalized.save(data, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(data.getvalue()).decode("ascii")


def payload(uri, variant):
    prompt = RUSSIAN_PROMPT if variant == "conservative" else PROMPT
    request = {
        "model": "internvl3-2b-gguf",
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": uri}},
            {"type": "text", "text": prompt},
        ]}],
        **CONFIG["request_options"],
    }
    if variant != "unconstrained":
        schema = deepcopy(SCHEMA)
        if variant == "conservative":
            schema["properties"]["component"]["enum"].remove("none")
            schema["properties"]["component"]["enum"].insert(0, "none")
            schema["properties"]["condition"]["enum"] = [
                "uncertain", "no_visible_defect", "corrosion", "wet_patch", "visible_damage",
            ]
        request["response_format"] = {"type": "json_object", "schema": schema}
    return request


def sample(opener, name, uri, variant, run, headers):
    request = Request(ENDPOINT, json.dumps(payload(uri, variant)).encode(), headers)
    started = perf_counter()
    row = {"run": run, "fixture": name}
    content = None
    try:
        with opener.open(request, timeout=45) as response:
            envelope = json.load(response)
        row["seconds"] = round(perf_counter() - started, 3)
        content = envelope["choices"][0]["message"]["content"]
        observation = json.loads(content)
        result = result_from_observation(observation, model="InternVL3-2B-Instruct-Q4_K_M")
        row.update(status=result["status"], raw=observation, timings=envelope.get("timings"), valid=True)
    except (HTTPError, URLError, TimeoutError, ValueError, KeyError, TypeError) as error:
        row.update(seconds=round(perf_counter() - started, 3), raw=content, valid=False, error=str(error))
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--warman", type=Path, help="Local licensed copy of the Warman photo")
    parser.add_argument("--generate-fixtures", action="store_true", help="Create the four synthetic negatives")
    parser.add_argument("--variant", choices=("baseline", "unconstrained", "conservative"), default="baseline")
    parser.add_argument("--expanded", action="store_true", help="Include the person and furniture drawings")
    parser.add_argument("--runs", type=int, default=1)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be positive")
    if args.generate_fixtures:
        write_negative_fixtures(args.fixtures)
    cases = select_fixtures(args)
    headers = {"Content-Type": "application/json"}
    if key := os.getenv("CYBER102_GGUF_API_KEY"):
        headers["Authorization"] = f"Bearer {key}"
    opener = build_opener(ProxyHandler({}))
    rows = []
    for run in range(1, args.runs + 1):
        for name, path in cases.items():
            row = sample(opener, name, image_uri(path), args.variant, run, headers)
            rows.append(row)
            print(json.dumps(row), flush=True)
    valid = [row["seconds"] for row in rows if row["valid"]]
    negatives = [row for row in rows if row["fixture"] in NEGATIVE_CASES]
    summary = {
        "valid": len(valid), "total": len(rows),
        "negative_abstentions": sum(row.get("status") == "unusable" for row in negatives),
        "negative_total": len(negatives),
    }
    if valid:
        summary.update(min=min(valid), median=median(valid), max=max(valid))
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
