"""Evaluate detector checkpoints against manually marked, held-out frames."""
import argparse
import json
import statistics
import time
from collections import Counter
from pathlib import Path


def overlap(a, b):
    width = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    height = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    intersection = width * height
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union if union > 0 else 0


def match_indices(predictions, boxes, iou):
    remaining = list(enumerate(boxes))
    matched = set()
    for prediction in sorted(predictions, key=lambda item: -item['score']):
        if not remaining:
            break
        scores = [overlap(prediction['xyxy'], box) for _, box in remaining]
        best = max(scores)
        if best >= iou:
            index, _ = remaining.pop(scores.index(best))
            matched.add(index)
    return matched


def count_matches(predictions, boxes, iou):
    return len(match_indices(predictions, boxes, iou))


def measurements(rows, predictions, labels, threshold, iou):
    summary = {}
    flagged_controls = set()
    for label in labels:
        tp = fp = fn = 0
        for frame in rows:
            predicted = [p for p in predictions[frame['file']] if p['score'] >= threshold and p['class'] == label]
            expected = [obj['xyxy'] for obj in frame['objects'] if obj['class'] == label]
            matched = count_matches(predicted, expected, iou)
            tp += matched
            fp += len(predicted) - matched
            fn += len(expected) - matched
            if not frame['objects'] and predicted:
                flagged_controls.add(frame['file'])
        summary[label] = {'tp': tp, 'fp': fp, 'fn': fn,
                          'recall': tp / (tp + fn) if tp + fn else None,
                          'precision': tp / (tp + fp) if tp + fp else None}
    return {'classes': summary, 'flagged_controls': sorted(flagged_controls)}


def class_negative_frames(rows, predictions, labels, threshold):
    """Count false alerts of each class, even when another class is present."""
    summary = {}
    for label in labels:
        negatives = [row['file'] for row in rows if all(obj['class'] != label for obj in row['objects'])]
        flagged = [name for name in negatives
                   if any(p['class'] == label and p['score'] >= threshold for p in predictions[name])]
        summary[label] = {'count': len(negatives), 'flagged': sorted(flagged)}
    return summary


def scene_coverage(rows, predictions, labels, threshold, iou):
    scenes = {}
    for frame in rows:
        scenes.setdefault(frame['scene'], []).append(frame)
    counts = {}
    for label in labels:
        positive, detected = [], []
        for scene, frames in scenes.items():
            result = measurements(frames, predictions, (label,), threshold, iou)['classes'][label]
            if result['tp'] + result['fn']:
                positive.append(scene)
            if result['tp']:
                detected.append(scene)
        counts[label] = {'positive_scenes': sorted(positive), 'detected_scenes': sorted(detected)}
    return counts


def localization_errors(rows, predictions, threshold=.25, iou=.5):
    """Identify missed objects with an overlapping prediction of the wrong class or low score."""
    errors = []
    for frame in rows:
        ranked = sorted(predictions[frame['file']], key=lambda item: -item['score'])
        matched = set()
        for label in {obj['class'] for obj in frame['objects']}:
            indexed = [(index, obj['xyxy']) for index, obj in enumerate(frame['objects']) if obj['class'] == label]
            candidates = [p for p in ranked if p['class'] == label and p['score'] >= threshold]
            matched.update(indexed[index][0] for index in match_indices(
                candidates, [box for _, box in indexed], iou))
        for index, expected in enumerate(frame['objects']):
            if index in matched:
                continue
            matching = [(prediction, overlap(prediction['xyxy'], expected['xyxy'])) for prediction in ranked]
            aligned = [(prediction, score) for prediction, score in matching if score >= iou]
            same_class = next(((prediction, score) for prediction, score in aligned
                               if prediction['class'] == expected['class']), None)
            candidate = same_class or next(iter(aligned), None)
            error = {'file': frame['file'], 'scene': frame['scene'], 'expected': expected['class']}
            if candidate:
                prediction, score = candidate
                reason = 'wrong_class'
                if same_class:
                    reason = 'shared_box' if prediction['score'] >= threshold else 'low_score'
                error.update({'reason': reason,
                              'predicted': prediction['class'], 'score': prediction['score'], 'iou': score})
            else:
                error.update({'reason': 'no_overlapping_box',
                              'best_iou': max((score for _, score in matching), default=0)})
            errors.append(error)
    return errors


def merge_boxes(predictions, iou=.5):
    kept = []
    for prediction in sorted(predictions, key=lambda item: -item['score']):
        if any(previous['class'] == prediction['class'] and overlap(previous['xyxy'], prediction['xyxy']) >= iou
               for previous in kept):
            continue
        kept.append(prediction)
    return kept


def infer(model, images, device, size, tiles=False):
    from PIL import Image

    def detect(image, offset_x=0, offset_y=0, fraction_x=1, fraction_y=1):
        result = model.predict(image, device=device, imgsz=size, conf=.01, verbose=False, max_det=100)[0]
        boxes = result.boxes.cpu()
        return [{'class': model.names[int(index)], 'score': float(score),
                 'xyxy': [offset_x + fraction_x * left, offset_y + fraction_y * top,
                          offset_x + fraction_x * right, offset_y + fraction_y * bottom]}
                for index, score, (left, top, right, bottom) in
                zip(boxes.cls.tolist(), boxes.conf.tolist(), boxes.xyxyn.tolist(), strict=True)]

    model.predict(Image.new('RGB', (size, size)), device=device, imgsz=size, verbose=False)
    predictions, timings = {}, {}
    for file in images:
        started = time.perf_counter()
        predictions[file.name] = detect(file)
        if tiles:
            with Image.open(file) as photo:
                photo = photo.convert('RGB')
                width, height = photo.size
                tile_width, tile_height = round(width * .65), round(height * .65)
                for x in (0, width - tile_width):
                    for y in (0, height - tile_height):
                        tile = photo.crop((x, y, x + tile_width, y + tile_height))
                        predictions[file.name].extend(detect(tile, x / width, y / height,
                                                             tile_width / width, tile_height / height))
            predictions[file.name] = merge_boxes(predictions[file.name])
        timings[file.name] = round((time.perf_counter() - started) * 1000, 3)
    return predictions, timings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('weights', type=Path)
    parser.add_argument('annotations', type=Path)
    parser.add_argument('images', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--size', type=int, required=True)
    parser.add_argument('--device', default='0')
    parser.add_argument('--tiles', action='store_true')
    parser.add_argument('--labels', nargs='+', help='Classes exhaustively annotated in this benchmark')
    args = parser.parse_args()
    from ultralytics import YOLO, settings
    settings.update({'sync': False})
    rows = json.loads(args.annotations.read_text(encoding='utf-8'))
    labels = {obj['class'] for row in rows for obj in row['objects']}
    if args.labels and labels - set(args.labels):
        parser.error('--labels must include all annotated classes')
    images = [args.images / row['file'] for row in rows]
    predictions, timings = infer(YOLO(str(args.weights.resolve(strict=True))), images, args.device, args.size, args.tiles)
    if args.labels:
        labels = set(args.labels)
    else:
        labels.update(p['class'] for items in predictions.values() for p in items)
    labels = sorted(labels)
    report = {'weights': str(args.weights), 'size': args.size, 'tiles': args.tiles,
              'median_ms': statistics.median(timings.values()), 'timings_ms': timings,
              'support_by_scene': dict(Counter(row['scene'] for row in rows)),
              'metrics': {f'{confidence:.2f}/{iou:.2f}': measurements(rows, predictions, labels, confidence, iou)
                          for confidence in (.05, .15, .25, .40) for iou in (.30, .50)},
              'scene_coverage_at_0.25/0.50': scene_coverage(rows, predictions, labels, .25, .50),
              'class_negative_frames_at_0.25': class_negative_frames(rows, predictions, labels, .25),
              'localization_errors_at_0.25/0.50': localization_errors(rows, predictions),
              'predictions': predictions}
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(args.output)


if __name__ == '__main__':
    main()
