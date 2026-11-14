"""Measure whether an independent detector can verify Qwen proposals."""
import argparse
import json
from pathlib import Path

from evaluate import measurements, overlap


def compare(rows, proposals, verifier, labels, score, iou):
    names = {row['file'] for row in rows}
    if not names or len(names) != len(rows) or names != set(proposals) or names != set(verifier):
        raise ValueError('Both reports must cover the same annotated frames')
    common = {
        name: [box for box in boxes if any(
            candidate['class'] == box['class'] and candidate['score'] >= score
            and overlap(candidate['xyxy'], box['xyxy']) >= iou for candidate in verifier[name])]
        for name, boxes in proposals.items()
    }
    return {'qwen': measurements(rows, proposals, labels, 1., .50),
            'verified': measurements(rows, common, labels, 1., .50),
            'predictions': common}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('annotations', type=Path)
    parser.add_argument('proposals', type=Path)
    parser.add_argument('verifier', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--label', choices=('crack', 'terminal_damage', 'wet_patch'), required=True)
    parser.add_argument('--score', type=float, default=.05)
    parser.add_argument('--agreement-iou', type=float, default=.50)
    args = parser.parse_args()
    if not 0 <= args.score <= 1 or not 0 < args.agreement_iou <= 1:
        parser.error('Expected score in [0, 1] and agreement IoU in (0, 1]')
    rows, proposals, verifier = [json.loads(path.read_bytes())
                                 for path in (args.annotations, args.proposals, args.verifier)]
    result = {'verifierModel': verifier['model'], 'score': args.score,
              'agreementIoU': args.agreement_iou,
              **compare(rows, proposals['predictions'], verifier['predictions'],
                        (args.label,), args.score, args.agreement_iou)}
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
