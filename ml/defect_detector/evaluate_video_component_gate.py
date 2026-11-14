"""Compare defect boxes with the component and condition reported by the video API."""
import argparse
import json
from pathlib import Path

from evaluate_qwen_video_grounding import LABELS, measure


def has_component_support(box, observation):
    if observation['condition'] != 'visible_damage':
        return False
    left, top, right, bottom = (value / 1000 for value in observation['box'])
    x1, y1, x2, y2 = box['xyxy']
    return left <= (x1 + x2) / 2 <= right and top <= (y1 + y2) / 2 <= bottom


def compare(rows, clips, baseline, candidate):
    raw, supported = {}, {}
    for row in rows:
        name, clip, index = row['file'], row['clip'], row['frameIndex']
        frame = clips[clip]['frames'][index]
        actual = baseline['clips'][clip]
        if actual['httpStatus'] != 200:
            raise ValueError(f'No successful video API response for {clip}')
        response = actual['response']
        if any(response[key] != candidate[key] for key in ('model', 'revision')):
            raise ValueError('Candidate and API reports use different model revisions')
        observation = response['observations'][index]
        if (any(frame[key] != row[key] for key in ('file', 'sha256', 'timestampSeconds'))
                or observation['frameIndex'] != index or observation['timestampSeconds'] != row['timestampSeconds']):
            raise ValueError(f'Frame does not match annotations: {name}')
        raw[name] = [box for box in candidate['predictions'][name] if box['class'] in LABELS]
        supported[name] = [box for box in raw[name] if has_component_support(box, observation)]
    return {'model': candidate['model'], 'revision': candidate['revision'],
            'baseline': measure(rows, raw), 'componentSupported': measure(rows, supported),
            'predictions': supported}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('annotations', type=Path)
    parser.add_argument('clip_manifest', type=Path)
    parser.add_argument('service_report', type=Path)
    parser.add_argument('candidate_report', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    inputs = (args.annotations, args.clip_manifest, args.service_report, args.candidate_report)
    report = compare(*(json.loads(path.read_bytes()) for path in inputs))
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
