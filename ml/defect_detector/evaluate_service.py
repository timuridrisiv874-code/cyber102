"""Measure the running local vision service against a frame annotation set."""
import argparse
import base64
import json
from pathlib import Path
import statistics
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from evaluate import measurements

LOOPBACK = 'http://127.0.0.1:11436'
DEFECTS = frozenset({'corrosion', 'wet_patch', 'visible_damage'})
GROUP = {'crack': 'visible_damage', 'terminal_damage': 'visible_damage'}


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def run_local(opener, photo, profile):
    payload = {'image': base64.b64encode(photo).decode('ascii'), 'profile': profile}
    request = Request(LOOPBACK + '/analyze', json.dumps(payload).encode('ascii'),
                      {'Content-Type': 'application/json'})
    for attempt in range(3):
        try:
            with opener.open(request, timeout=100) as response:
                return json.load(response)
        except HTTPError as exc:
            with exc:
                try:
                    failure = json.load(exc)
                except ValueError:
                    failure = {'error': f'HTTP {exc.code}'}
                if exc.code != 503 or failure.get('code') != 'vision_busy' or attempt == 2:
                    return {'error': failure.get('error'), 'status': exc.code}
            time.sleep(2)


def frame_conditions(rows, predictions):
    result = {}
    for name in sorted(DEFECTS):
        positive = {row['file'] for row in rows if any(obj['class'] == name for obj in row['objects'])}
        flagged = {row['file'] for row in rows if any(obj['class'] == name for obj in predictions[row['file']])}
        result[name] = {'tp': len(positive & flagged), 'fp': len(flagged - positive), 'fn': len(positive - flagged),
                        'positive_frames': sorted(positive), 'flagged_frames': sorted(flagged)}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('annotations', type=Path)
    parser.add_argument('images', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--profile', choices=('standard', 'fast'), default='standard')
    args = parser.parse_args()
    rows = json.loads(args.annotations.read_text(encoding='utf-8'))
    opener = build_opener(ProxyHandler({}), NoRedirects())
    with opener.open(LOOPBACK + '/health', timeout=5) as response:
        status = json.load(response)
    samples, predictions, timings = {}, {}, []
    for frame in rows:
        name = frame['file']
        response = run_local(opener, (args.images / name).read_bytes(), args.profile)
        if 'observation' in response:
            if response['model'] != status['model'] or response['revision'] != status['revision']:
                raise ValueError('Local service changed model during benchmark')
            observation = response['observation']
            category = observation['condition']
            predictions[name] = [{'class': category, 'score': 1,
                                  'xyxy': [n / 1000 for n in observation['box']]}] if category in DEFECTS else []
            timings.append(response['metrics']['totalMs'])
        else:
            predictions[name] = []
        samples[name] = response
        print(name, response.get('observation', {}).get('condition', response.get('error', 'invalid result')),
              flush=True)
    grouped_rows = [{**frame, 'objects': [{**obj, 'class': GROUP.get(obj['class'], obj['class'])}
                                          for obj in frame['objects']]} for frame in rows]
    report = {'model': status['model'], 'revision': status['revision'], 'device': status['device'],
              'profile': args.profile,
              'box_type': 'equipment component', 'median_ms': statistics.median(timings) if timings else None,
              'failed_frames': sorted(name for name, result in samples.items() if 'observation' not in result),
              'frame_conditions': frame_conditions(grouped_rows, predictions),
              'metrics': {f'{iou:.2f}': measurements(grouped_rows, predictions, sorted(DEFECTS), 1, iou)
                          for iou in (.3, .5)},
              'samples': samples, 'predictions': predictions}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
