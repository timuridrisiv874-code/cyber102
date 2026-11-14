"""Evaluate the live Cyber102 video route on pre-extracted, timestamped frames."""
import argparse
import base64
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from evaluate import measurements, scene_coverage
from evaluate_service import NoRedirects, frame_conditions


def run_clip(opener, port, folder, metadata):
    frames = []
    for frame in metadata['frames']:
        name = frame['file']
        if Path(name).name != name:
            raise ValueError('Expected an image basename')
        image = (folder / name).read_bytes()
        if hashlib.sha256(image).hexdigest() != frame['sha256']:
            raise ValueError(f'Image hash mismatch: {name}')
        frames.append({'image': base64.b64encode(image).decode('ascii'),
                       'timestampSeconds': frame['timestampSeconds']})
    payload = {'durationSeconds': metadata['durationSeconds'], 'frames': frames}
    request = Request(f'http://127.0.0.1:{port}/api/video-review', json.dumps(payload).encode('ascii'),
                      {'Content-Type': 'application/json'})
    started = time.perf_counter()
    try:
        with opener.open(request, timeout=155) as response:
            data = json.load(response)
    except HTTPError as error:
        with error:
            return {'httpStatus': error.code, 'error': error.read(4096).decode('utf-8', errors='replace'),
                    'elapsedMs': round((time.perf_counter() - started) * 1000, 3)}
    if (len(data['observations']) != len(frames) or any(
            observation['frameIndex'] != index or observation['timestampSeconds'] != frame['timestampSeconds']
            for index, (observation, frame) in enumerate(zip(data['observations'], frames, strict=True)))):
        raise ValueError('Response does not match supplied frame positions')
    return {'httpStatus': 200, 'elapsedMs': round((time.perf_counter() - started) * 1000, 3),
            'response': data}


def evaluate(annotations, clips, responses):
    grouped = defaultdict(list)
    for row in annotations:
        grouped[row['clip']].append(row)
    rows, predictions = [], {}
    for name, data in responses.items():
        if data['httpStatus'] != 200:
            continue
        for row in grouped[name]:
            index = row['frameIndex']
            frame = clips[name]['frames'][index]
            if any(row[key] != frame[key] for key in ('file', 'sha256', 'timestampSeconds')):
                raise ValueError(f'Annotation does not match supplied image: {row["file"]}')
            observation = data['response']['observations'][index]
            objects = [{**obj, 'class': 'visible_damage'} for obj in row['objects']]
            rows.append({**row, 'objects': objects})
            predictions[row['file']] = ([{'class': 'visible_damage', 'score': 1,
                                          'xyxy': [n / 1000 for n in observation['box']]}]
                                        if observation['condition'] == 'visible_damage' else [])
    label = ('visible_damage',)
    return {'evaluatedFrames': len(rows), 'coverage': frame_conditions(rows, predictions)['visible_damage'],
            'at0.50': measurements(rows, predictions, label, 1, .50),
            'scenesAt0.50': scene_coverage(rows, predictions, label, 1, .50)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('annotations', type=Path)
    parser.add_argument('directory', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--clips', nargs='+', required=True)
    args = parser.parse_args()
    rows = json.loads(args.annotations.read_bytes())
    clips = json.loads((args.directory / 'clips.json').read_bytes())
    opener = build_opener(ProxyHandler({}), NoRedirects())
    responses = {}
    for name in args.clips:
        responses[name] = run_clip(opener, args.port, args.directory, clips[name])
        print(name, responses[name]['httpStatus'], responses[name]['elapsedMs'], flush=True)
    report = {'clips': responses, **evaluate(rows, clips, responses)}
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
