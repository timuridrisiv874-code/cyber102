"""Submit annotated video frames to the local asynchronous web API and score verified boxes."""
import argparse
import base64
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic, sleep
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler
from uuid import uuid4

from evaluate_service import NoRedirects
from evaluate_video_component_gate import measure_reviewed


def run_clip(base, folder, frames, duration):
    payload = []
    for frame in frames:
        name = frame['file']
        if Path(name).name != name:
            raise ValueError('Unexpected frame name')
        raw = (folder / name).read_bytes()
        if sha256(raw).hexdigest() != frame['sha256']:
            raise ValueError(f'Frame SHA mismatch: {name}')
        payload.append({'image': base64.b64encode(raw).decode('ascii'), 'timestampSeconds': frame['timestampSeconds']})
    body = json.dumps({'frames': payload, 'durationSeconds': duration}).encode('ascii')
    client = uuid4().hex
    opener = build_opener(ProxyHandler({}), NoRedirects())
    started = monotonic()
    request = Request(base + '/api/video-review', body, {'Content-Type': 'application/json',
        'Prefer': 'respond-async', 'X-Analysis-Client': client, 'X-Analysis-Request': uuid4().hex})
    try:
        with opener.open(request, timeout=15) as response:
            if response.status != 202:
                raise ValueError(f'Expected 202, got {response.status}')
            location = response.headers['Location']
            initial = json.load(response)['job']
        if location != '/api/analysis-jobs/' + initial['id']:
            raise ValueError('Unexpected job URL')
        first = monotonic() - started
        while monotonic() - started < 165:
            how = Request(base + location, headers={'X-Analysis-Client': client})
            with opener.open(how, timeout=15) as response:
                job = json.load(response)['job']
            if job['status'] in ('queued', 'running'):
                sleep(.5)
                continue
            elapsed = round((monotonic() - started) * 1000, 3)
            if job['status'] != 'succeeded':
                return {'httpStatus': 202, 'elapsedMs': elapsed, 'job': job}
            return {'httpStatus': 200, 'elapsedMs': elapsed, 'queuedResponseMs': round(first * 1000, 3),
                    'response': job['result']}
    except HTTPError as error:
        with error:
            return {'httpStatus': error.code, 'error': error.read(2048).decode('utf-8', errors='replace')}
    raise TimeoutError('Video review job did not finish in 165 seconds')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('dataset', type=Path)
    p.add_argument('annotations', type=Path)
    p.add_argument('output', type=Path)
    p.add_argument('--port', type=int, default=8104)
    p.add_argument('--overview-only', action='store_true')
    args = p.parse_args()
    clips = json.loads((args.dataset / 'clips.json').read_bytes())
    rows = json.loads(args.annotations.read_bytes())
    for row in rows:
        sample = clips[row['clip']]['frames'][row['frameIndex']]
        if any(row[key] != sample[key] for key in ('file', 'timestampSeconds', 'sha256')):
            raise ValueError('Annotations differ from input frames')
    predictions, responses = {}, {}
    for name, clip in clips.items():
        frames = clip['frames']
        reply = run_clip(f'http://127.0.0.1:{args.port}', args.dataset, frames, clip['durationSeconds'])
        responses[name] = reply
        print(name, reply['httpStatus'], reply.get('elapsedMs'), flush=True)
        if reply['httpStatus'] != 200:
            break
        result = reply['response']
        observations = result['observations']
        details = result.get('defectDetails')
        if details is None and not args.overview_only:
            raise ValueError('Missing defect details on the candidate server')
        sets = [observations, details] if details is not None else [observations]
        for items in sets:
            for index, (item, frame) in enumerate(zip(items, frames, strict=True)):
                if item['frameIndex'] != index or item['timestampSeconds'] != frame['timestampSeconds']:
                    raise ValueError('Response lost frame identity or order')
        if details is not None:
            for item, frame in zip(details, frames, strict=True):
                predictions[frame['file']] = [{'class': box['kind'], 'xyxy': [v / 1000 for v in box['box']],
                                              'score': 1.0} for box in item['boxes']]
        args.output.write_text(json.dumps({'clips': responses}, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    complete = len(clips) == len(responses) and all(r['httpStatus'] == 200 for r in responses.values())
    report = {'clips': responses, 'complete': complete}
    if complete and predictions:
        report['reviewedMetrics'] = measure_reviewed(rows, predictions)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    if not complete:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
