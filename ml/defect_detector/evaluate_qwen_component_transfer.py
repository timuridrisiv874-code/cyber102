"""Test the frozen video localization recipe on individually annotated photos."""
import argparse
import hashlib
import json
from pathlib import Path
from statistics import median
import sys
from time import perf_counter

from evaluate import measurements, scene_coverage
from evaluate_qwen_boxes import MAX_NEW_TOKENS, make_prompt, parse_detections
from evaluate_video_component_gate import LABELS, has_component_support


def summarize(rows, predictions):
    by_class = {}
    for label in LABELS:
        reviewed = [row for row in rows if label in row['reviewedLabels']]
        if not reviewed:
            continue
        metrics = measurements(reviewed, predictions, (label,), 1, .50)
        by_class[label] = {**metrics['classes'][label],
                           'negativeFrames': sum(not any(box['class'] == label for box in row['objects'])
                                                 for row in reviewed),
                           'flaggedControls': sorted(row['file'] for row in reviewed
                                                     if not any(box['class'] == label for box in row['objects'])
                                                     and any(box['class'] == label for box in predictions[row['file']])),
                           **scene_coverage(reviewed, predictions, (label,), 1, .50)[label]}
    return by_class


def frozen_observations(rows, clips, report, candidate):
    observations = {}
    for row in rows:
        clip, index = row['clip'], row['frameIndex']
        frame = clips[clip]['frames'][index]
        record = report['clips'][clip]
        if record['httpStatus'] != 200:
            raise ValueError(f'No successful video API response for {clip}')
        response = record['response']
        observation = response['observations'][index]
        if (any(response[key] != candidate[key] for key in ('model', 'revision'))
                or any(frame[key] != row[key] for key in ('file', 'sha256', 'timestampSeconds'))
                or observation['frameIndex'] != index or observation['timestampSeconds'] != row['timestampSeconds']):
            raise ValueError(f'Frozen API response does not match {row["file"]}')
        observations[row['file']] = observation
    return observations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repo', type=Path)
    parser.add_argument('model', type=Path)
    parser.add_argument('annotations', type=Path)
    parser.add_argument('images', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--video-api-report', type=Path)
    args = parser.parse_args()
    rows = json.loads(args.annotations.read_bytes())
    names = set()
    for row in rows:
        name = row['file']
        reviewed = set(row['reviewedLabels'])
        if (name in names or Path(name).name != name or not reviewed or reviewed - set(LABELS)
                or any(box['class'] not in reviewed for box in row['objects'])):
            raise ValueError(f'Invalid or duplicated annotation: {name}')
        if hashlib.sha256((args.images / name).read_bytes()).hexdigest() != row['sha256']:
            raise ValueError(f'Image contents differ: {name}')
        names.add(name)
    if not names:
        raise ValueError('No labeled images')

    sys.path.insert(0, str(args.repo.resolve(strict=True)))
    from PIL import Image, ImageOps
    from vision_runtime.qwen_worker import Engine, PHOTO_MAX_PIXELS, VIDEO_MAX_PIXELS
    from vision_runtime.worker import _parse_json, deny_external_connections, parse_compact

    sys.addaudithook(deny_external_connections)
    engine = Engine(args.model.resolve(strict=True))
    identity = {'model': engine.model_id, 'revision': engine.revision}
    frozen = None
    if args.video_api_report:
        clips = json.loads((args.images / 'clips.json').read_bytes())
        frozen = frozen_observations(rows, clips, json.loads(args.video_api_report.read_bytes()), identity)
    crack_labels = ('crack',)

    def prompt(labels, mode):
        return engine._prompt([{'type': 'image'}, {'type': 'text', 'text': make_prompt(labels, mode)}])

    numbered = prompt(LABELS, 'numbered')
    detailed = prompt(crack_labels, 'detailed')
    run_settings = ((detailed, crack_labels, 'detailed'), (numbered, LABELS, 'numbered'))
    records, proposals, supported = {}, {}, {}
    with Image.new('RGB', (512, 384), 'white') as blank:
        try:
            engine._infer_video_batch([blank], [0], 25)
        except ValueError:
            pass
    for row in rows:
        name = row['file']
        start = perf_counter()
        errors, timings, boxes = {}, {}, []
        with Image.open(args.images / name) as source, ImageOps.exif_transpose(source).convert('RGB') as image:
            if frozen is not None:
                observation = frozen[name]
            else:
                try:
                    observation = engine._run(image, engine.compact_prompt, max_pixels=VIDEO_MAX_PIXELS,
                                              max_new_tokens=96, max_time=28, parse=parse_compact)
                except ValueError as error:
                    observation = None
                    errors['overview'] = str(error)
            timings['overview'] = round((perf_counter() - start) * 1000, 3)
            for text, labels, mode in run_settings:
                wanted = 'crack' if mode == 'detailed' else 'terminal_damage'
                if wanted not in row['reviewedLabels']:
                    continue
                begun = perf_counter()
                try:
                    result = engine._run(image, text, max_pixels=PHOTO_MAX_PIXELS,
                                         max_new_tokens=MAX_NEW_TOKENS[mode], max_time=35,
                                         parse=lambda raw: parse_detections(raw, _parse_json, labels, mode))
                except ValueError as error:
                    errors[mode] = str(error)
                else:
                    boxes.extend(box for box in result if box['class'] == wanted)
                timings[mode] = round((perf_counter() - begun) * 1000, 3)
        proposals[name] = boxes
        supported[name] = [box for box in boxes if observation and has_component_support(box, observation)]
        records[name] = {'observation': observation, 'timingsMs': timings, 'errors': errors}
        print(name, len(boxes), len(supported[name]), round(sum(timings.values())), flush=True)
    extra = [sum(ms for name, ms in record['timingsMs'].items() if name != 'overview') for record in records.values()
             if (record['observation'] or {}).get('condition') == 'visible_damage']
    report = {**identity, 'frames': len(rows), 'overviewSource': 'video_api_report' if frozen is not None else 'single_frame',
              'raw': summarize(rows, proposals), 'componentSupported': summarize(rows, supported),
              'medianExtraMsOnEligibleFrames': median(extra) if extra else None,
              'medianEstimatedConditionalMs': None if frozen is not None else median(
                  sum(record['timingsMs'].values()) if (record['observation'] or {}).get('condition') == 'visible_damage'
                  else record['timingsMs']['overview'] for record in records.values()),
              'predictions': supported, 'proposals': proposals, 'records': records}
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
