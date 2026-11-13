"""Exercise the deployed model on supplied examples; no expert labels or accuracy claim."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from statistics import median
import time

from server import vision
from vision_runtime.worker import MODEL, REVISION, PROMPT

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=ROOT / 'data/customer')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/customer/vision-smoke-report.json')
    args = parser.parse_args()
    catalog = json.loads((args.directory / 'materials.json').read_text(encoding='utf-8'))
    rows = []
    started = datetime.now(timezone.utc).isoformat()
    for image in catalog['images']:
        content = (args.directory / 'previews' / f"{image['id']}.jpg").read_bytes()
        assert hashlib.sha256(content).hexdigest() == image['previewSha256']
        row = {'imageId': image['id'], 'sourcePath': image['sourcePath'],
               'previewSha256': image['previewSha256'], 'expertLabel': None}
        tick = time.monotonic()
        try:
            row['result'] = vision.analyze(content, backend='internvl3')
        except vision.VisionUnavailable as error:
            row['error'] = str(error)
        row['seconds'] = round(time.monotonic() - tick, 3)
        rows.append(row)
        print(json.dumps({'processed': len(rows), 'total': len(catalog['images']),
                          'status': row.get('result', {}).get('status', 'error'),
                          'seconds': row['seconds']}), flush=True)
    good = [row for row in rows if 'result' in row]
    report = {'startedAt': started, 'model': MODEL, 'revision': REVISION,
              'promptSha256': hashlib.sha256(PROMPT.encode()).hexdigest(),
              'scope': 'Functional inference on customer examples without expert defect labels. Valid JSON is not accuracy.',
              'summary': {'requests': len(rows), 'valid': len(good), 'errors': len(rows) - len(good),
                          'statuses': {status: sum(row['result']['status'] == status for row in good)
                                       for status in ('defect', 'clear', 'unusable')},
                          'medianSeconds': median(row['seconds'] for row in good) if good else None},
              'records': rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    staged = args.output.with_suffix('.tmp')
    staged.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    staged.replace(args.output)
    print(json.dumps(report['summary']), flush=True)


if __name__ == '__main__':
    main()
