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


def count_matches(predictions, boxes, iou):
    remaining = list(boxes)
    matched = 0
    for prediction in sorted(predictions, key=lambda item: -item['score']):
        scores = [overlap(prediction['xyxy'], box) for box in remaining]
        best = max(scores, default=0)
        if best >= iou:
            remaining.pop(scores.index(best))
            matched += 1
    return matched


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
    args = parser.parse_args()
    from ultralytics import YOLO, settings
    settings.update({'sync': False})
    rows = json.loads(args.annotations.read_text(encoding='utf-8'))
    labels = {obj['class'] for row in rows for obj in row['objects']}
    images = [args.images / row['file'] for row in rows]
    predictions, timings = infer(YOLO(str(args.weights.resolve(strict=True))), images, args.device, args.size, args.tiles)
    labels.update(p['class'] for items in predictions.values() for p in items)
    report = {'weights': str(args.weights), 'size': args.size, 'tiles': args.tiles,
              'median_ms': statistics.median(timings.values()), 'timings_ms': timings,
              'support_by_scene': dict(Counter(row['scene'] for row in rows)),
              'metrics': {f'{confidence:.2f}/{iou:.2f}': measurements(rows, predictions, sorted(labels), confidence, iou)
                          for confidence in (.05, .15, .25, .40) for iou in (.30, .50)},
              'predictions': predictions}
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(args.output)


if __name__ == '__main__':
    main()
