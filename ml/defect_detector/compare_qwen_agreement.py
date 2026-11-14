"""Evaluate two Qwen prediction reports and boxes supported by both prompts."""
import argparse
import json
from pathlib import Path
from statistics import median

from evaluate import class_negative_frames, measurements, overlap, scene_coverage


def compare(rows, primary, secondary, labels, agreement_iou):
    names = {row['file'] for row in rows}
    if not names or len(names) != len(rows):
        raise ValueError('Annotation filenames must be nonempty and unique')
    identity = ('model', 'revision')
    if any(primary[key] != secondary[key] for key in identity):
        raise ValueError('Prediction reports must use the same model revision')
    detections = []
    for report in (primary, secondary):
        available = report['labels'] if 'labels' in report else report['metrics']['0.50']['classes']
        if set(labels) - set(available):
            raise ValueError('Both reports must evaluate every requested class')
        if any(set(report[key]) != names for key in ('predictions', 'timings_ms')):
            raise ValueError('Both reports must cover exactly the annotated frames')
        detections.append({name: [box for box in boxes if box['class'] in labels]
                           for name, boxes in report['predictions'].items()})
    first, second = detections
    # Preserve the primary coordinates for a direct comparison against the primary report.
    agreed = {name: [box for box in boxes if any(
        other['class'] == box['class'] and overlap(box['xyxy'], other['xyxy']) >= agreement_iou
        for other in second[name])] for name, boxes in first.items()}

    def summarize(predictions):
        return {'at0.30': measurements(rows, predictions, labels, 1, .30),
                'at0.50': measurements(rows, predictions, labels, 1, .50),
                'scenes': scene_coverage(rows, predictions, labels, 1, .50),
                'negativeFrames': class_negative_frames(rows, predictions, labels, 1)}

    return {**{key: primary[key] for key in identity}, 'agreementIoU': agreement_iou,
            'labels': labels, 'primary': summarize(first), 'secondary': summarize(second),
            'agreement': summarize(agreed),
            'primaryErrors': primary['errors'], 'secondaryErrors': secondary['errors'],
            'medianSummedMs': median(primary['timings_ms'][name] + secondary['timings_ms'][name]
                                     for name in names), 'predictions': agreed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('annotations', type=Path)
    parser.add_argument('primary', type=Path)
    parser.add_argument('secondary', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--labels', nargs='+', required=True)
    parser.add_argument('--agreement-iou', type=float, default=.50)
    args = parser.parse_args()
    if not 0 < args.agreement_iou <= 1:
        parser.error('--agreement-iou must be above zero and at most one')
    rows, primary, secondary = (json.loads(path.read_bytes())
                                for path in (args.annotations, args.primary, args.secondary))
    report = compare(rows, primary, secondary, tuple(dict.fromkeys(args.labels)), args.agreement_iou)
    report['primaryReport'] = args.primary.name
    report['secondaryReport'] = args.secondary.name
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
