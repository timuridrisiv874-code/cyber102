"""Replay frame selection policies for an optional terminal pass after video review."""
import argparse
import hashlib
import json
import math
from pathlib import Path
from statistics import median

from evaluate_video_component_gate import measure_reviewed


LABELS = ('terminal_damage',)


def observations(clips, review, identity):
    result = {}
    for clip, item in clips.items():
        recorded = review[clip]
        response = recorded.get('response', {})
        if recorded['httpStatus'] != 200 or any(response.get(key) != identity[key] for key in ('model', 'revision')):
            raise ValueError(f'Unexpected review result for {clip}')
        frames = item['frames']
        checked = response['observations']
        if len(frames) != len(checked):
            raise ValueError(f'Review and sampled frames differ: {clip}')
        for index, (frame, observation) in enumerate(zip(frames, checked, strict=True)):
            name = frame['file']
            if (name in result or observation['frameIndex'] != index
                    or observation['timestampSeconds'] != frame['timestampSeconds']):
                raise ValueError(f'Unexpected video observation: {name}')
            result[name] = observation
    return result


def select(names, overview, budget):
    by_evidence = sorted(names[::2] + names[1::2], key=lambda name: (
        overview[name]['component'] != 'terminal',
        overview[name]['condition'] != 'visible_damage'))
    return {
        'all': names,
        'alternateEven': names[::2][:budget],
        'alternateOdd': names[1::2][:budget],
        'overviewPriority': by_evidence[:budget],
    }


def compare(rows, clips, review, proposals, verification, budget):
    if any(proposals[key] != verification[key] for key in ('model', 'revision')):
        raise ValueError('Verification uses a different model revision')
    lookup = observations(clips, review, proposals)
    names = {row['file'] for row in rows}
    if (len(rows) != len(names) or names != lookup.keys()
            or any(names != result[key].keys() for result, key in (
                (proposals, 'predictions'), (proposals, 'frameMs'),
                (verification, 'predictions'), (verification, 'verificationMs')))):
        raise ValueError('Require the same reviewed frames and timings from all inputs')
    for clip in clips:
        duration = review[clip]['elapsedMs']
        if type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0:
            raise ValueError(f'Invalid recorded review latency: {clip}')
    for row in rows:
        frame = clips[row['clip']]['frames'][row['frameIndex']]
        if any(row[key] != frame[key] for key in ('file', 'sha256', 'timestampSeconds')):
            raise ValueError(f'Annotation differs from sampled frame: {row["file"]}')
        name = row['file']
        for duration in (proposals['frameMs'][name], verification['verificationMs'][name]):
            if type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0:
                raise ValueError(f'Invalid recorded latency: {name}')
        localized = proposals['predictions'][name]
        if any(box not in localized for box in verification['predictions'][name]):
            raise ValueError(f'Verification returned an unrecognized proposal: {name}')
    plans = {}
    for clip, item in clips.items():
        for mode, selected in select([frame['file'] for frame in item['frames']], lookup, budget).items():
            plans.setdefault(mode, {})[clip] = selected
    results = {}
    for mode, selection in plans.items():
        included = {name for chosen in selection.values() for name in chosen}
        predictions = {name: boxes if name in included else []
                       for name, boxes in verification['predictions'].items()}
        elapsed = {clip: round(sum(proposals['frameMs'][name] + verification['verificationMs'][name]
                                   for name in chosen), 3) for clip, chosen in selection.items()}
        results[mode] = {'frames': selection, 'extraMsByClip': elapsed,
                         'medianExtraMs': median(elapsed.values()),
                         'medianEstimatedReviewPlusExtraMs': median(
                             review[clip]['elapsedMs'] + elapsed[clip] for clip in selection),
                         **measure_reviewed(rows, predictions, LABELS)['terminal_damage']}
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('annotations', nargs='+', type=Path)
    parser.add_argument('clip_manifest', type=Path)
    parser.add_argument('review', type=Path)
    parser.add_argument('proposals', type=Path)
    parser.add_argument('verification', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--budget', type=int, default=4)
    args = parser.parse_args()
    if not 1 <= args.budget <= 8:
        parser.error('--budget must be between one and eight frames')
    rows = [row for path in args.annotations for row in json.loads(path.read_bytes())]
    clips = json.loads(args.clip_manifest.read_bytes())
    review = json.loads(args.review.read_bytes())['clips']
    proposals_bytes = args.proposals.read_bytes()
    proposals = json.loads(proposals_bytes)
    verification = json.loads(args.verification.read_bytes())
    digest = hashlib.sha256(proposals_bytes).hexdigest()
    if digest != verification['inputReportSha256']:
        raise ValueError('Verification belongs to a different localization run')
    result = compare(rows, clips, review, proposals, verification, args.budget)
    args.output.write_text(json.dumps({'proposalSha256': digest, 'budget': args.budget,
                                       'modes': result}, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
