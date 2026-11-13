"""Repeatable inference smoke on selected frames, not an accuracy evaluation."""
import argparse
from io import BytesIO
import json
from pathlib import Path
from statistics import median
import time

from PIL import Image, ImageDraw
from server import vision

ROOT = Path(__file__).resolve().parents[1]


def images():
    for name in ('demo-pump.jpg', 'demo-pump-clean.jpg'):
        yield name, (ROOT / 'web' / name).read_bytes(), 'synthetic_demo'
    catalog = json.loads((ROOT / 'data/commons-manifest.json').read_text(encoding='utf-8'))
    reference = catalog[0]
    path = ROOT / 'data/raw' / reference['local_file']
    if path.exists():
        with Image.open(path) as original:
            normalized = original.convert('RGB')
            normalized.thumbnail((1280, 1280))
            out = BytesIO(); normalized.save(out, 'JPEG', quality=85)
        yield reference['title'], out.getvalue(), 'licensed_unlabelled_reference'
    empty = Image.new('RGB', (640, 480), 'white')
    out = BytesIO(); empty.save(out, 'JPEG')
    yield 'blank', out.getvalue(), 'synthetic_negative'
    label = Image.new('RGB', (640, 480), '#eeeeee')
    ImageDraw.Draw(label).text((80, 180), 'CYBER102 TEST: NO EQUIPMENT', fill='black')
    out = BytesIO(); label.save(out, 'JPEG')
    yield 'text_only', out.getvalue(), 'synthetic_negative'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=vision.BACKENDS, required=True)
    parser.add_argument('--repeat', type=int, default=2)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    records = []
    for repeat in range(args.repeat):
        for name, image, source in images():
            started = time.monotonic()
            try:
                result = vision.analyze(image, backend=args.backend)
                row = {'case': name, 'source': source, 'repeat': repeat, 'seconds': round(time.monotonic()-started, 3), 'result': result}
            except Exception as error:
                row = {'case': name, 'source': source, 'repeat': repeat, 'seconds': round(time.monotonic()-started, 3), 'error': str(error)}
            records.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    durations = [r['seconds'] for r in records if 'result' in r]
    report = {'backend': args.backend, 'scope': 'Functional smoke on synthetic and licensed unlabelled reference frames. No field labels or accuracy claim.',
              'records': records, 'successful': len(durations), 'requests': len(records),
              'median_seconds': median(durations) if durations else None}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__':
    main()
