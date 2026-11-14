"""Compare MVTec cable localization on one fixed split and one shared class."""
import argparse
import hashlib
import json
from pathlib import Path

from evaluate import class_negative_frames, measurements, merge_boxes
from prepare_mvtec_cable import ANY_CABLE_DEFECT, CLASSES


CABLE_LABELS = {*CLASSES.values(), ANY_CABLE_DEFECT}


def grouped_boxes(boxes):
    return merge_boxes([{**box, 'class': ANY_CABLE_DEFECT} for box in boxes if box['class'] in CABLE_LABELS])


def grouped_annotations(rows):
    if any(obj['class'] not in CABLE_LABELS for row in rows for obj in row['objects']):
        raise ValueError('Unexpected label in cable annotations')
    return [{**row, 'objects': [{**obj, 'class': ANY_CABLE_DEFECT} for obj in row['objects']]} for row in rows]


def compare(rows, candidates):
    files = {row['file'] for row in rows}
    if not files or len(files) != len(rows):
        raise ValueError('Expected unique evaluation frames')
    labels = (ANY_CABLE_DEFECT,)
    summary = {}
    for name, report in candidates.items():
        source = report['predictions']
        if set(source) != files:
            raise ValueError(f'Predictions differ from the fixed test split: {name}')
        boxes = {file: grouped_boxes(items) for file, items in source.items()}
        summary[name] = {
            'iou0.50': {f'{score:.2f}': measurements(rows, boxes, labels, score, .50)['classes'][ANY_CABLE_DEFECT]
                        for score in (.05, .15, .25, .35, .50)},
            'negativeFramesAt0.25': class_negative_frames(rows, boxes, labels, .25)[ANY_CABLE_DEFECT],
            'medianMs': report['median_ms'],
        }
    return summary


def read_json(path):
    content = path.read_bytes()
    return json.loads(content), hashlib.sha256(content).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('annotations', type=Path)
    parser.add_argument('grouped_annotations', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('reports', nargs='+', type=Path)
    args = parser.parse_args()
    original, original_hash = read_json(args.annotations)
    grouped, grouped_hash = read_json(args.grouped_annotations)
    rows = grouped_annotations(original)
    if rows != grouped:
        raise ValueError('Class grouping changed the evaluation images or coordinates')
    if len({path.stem for path in args.reports}) != len(args.reports):
        parser.error('Predictor report basenames must be distinct')
    loaded = {path.stem: read_json(path) for path in args.reports}
    result = {
        'frames': len(rows), 'objects': sum(len(row['objects']) for row in rows),
        'annotationsSha256': [original_hash, grouped_hash],
        'reportSha256': {key: value[1] for key, value in loaded.items()},
        'models': compare(rows, {key: value[0] for key, value in loaded.items()}),
    }
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
