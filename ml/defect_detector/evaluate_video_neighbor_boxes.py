"""Benchmark projecting defect boxes between adjacent, geometrically aligned video samples."""
import argparse
import hashlib
import json
import math
from pathlib import Path
from statistics import median
from time import perf_counter

from evaluate import merge_boxes, overlap
from evaluate_qwen_video_grounding import LABELS
from evaluate_video_component_gate import measure_reviewed


def frame_features(sift, gray, size):
    import numpy as np

    points, descriptors = sift.detectAndCompute(gray, None)
    return size, np.array([point.pt for point in points], dtype=np.float32).reshape(-1, 2), descriptors


def sampled_features(folder, clips, sift):
    from PIL import Image, ImageOps
    import numpy as np

    result = {}
    for clip in clips.values():
        for frame in clip['frames']:
            name = frame['file']
            if name in result or Path(name).name != name:
                raise ValueError(f'Expected unique image basename: {name!r}')
            path = folder / name
            if hashlib.sha256(path.read_bytes()).hexdigest() != frame['sha256']:
                raise ValueError(f'Sampled image checksum differs: {name}')
            with Image.open(path) as original, ImageOps.exif_transpose(original).convert('L') as image:
                image.thumbnail((960, 960))
                result[name] = frame_features(sift, np.asarray(image), image.size)
    return result


def match_features(source, target):
    import cv2
    import numpy as np

    if source[2] is None or target[2] is None:
        return np.empty((0, 2)), np.empty((0, 2))
    matcher = cv2.BFMatcher(cv2.NORM_L2)

    def matches(a, b):
        return {one.queryIdx: one.trainIdx for row in matcher.knnMatch(a, b, k=2) if len(row) == 2
                for one, two in [row] if one.distance < .75 * two.distance}

    forward = matches(source[2], target[2])
    reverse = matches(target[2], source[2])
    indices = [(x, y) for x, y in forward.items() if reverse.get(y) == x]
    if not indices:
        return np.empty((0, 2)), np.empty((0, 2))
    left, right = zip(*indices)
    return source[1][list(left)], target[1][list(right)]


def project_box(box, source_size, target_size, left, right):
    import cv2
    import numpy as np

    x1, y1, x2, y2 = np.asarray(box['xyxy']) * (*source_size, *source_size)
    width, height = x2 - x1, y2 - y1
    margin = max(24., .35 * max(width, height))
    near = ((left[:, 0] >= x1 - margin) & (left[:, 0] <= x2 + margin)
            & (left[:, 1] >= y1 - margin) & (left[:, 1] <= y2 + margin))
    src, dst = left[near], right[near]
    if len(src) < 8:
        return None
    matrix, mask = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=3)
    if matrix is None or mask is None:
        return None
    inliers = mask.ravel().astype(bool)
    if inliers.sum() < 8 or inliers.mean() < .65:
        return None
    confirmed = src[inliers]
    scale = float(np.linalg.norm(matrix[0, :2]))
    if not .5 <= scale <= 2 or (np.ptp(confirmed, axis=0) < (max(12., width * .30), max(12., height * .30))).any():
        return None
    middle = np.array([(x1 + x2) / 2, (y1 + y2) / 2], dtype=np.float32)
    hull = cv2.convexHull(confirmed)
    # Require features around the defect, rather than extrapolating from a distant background.
    if cv2.pointPolygonTest(hull, tuple(map(float, middle)), True) < -min(margin * .25, 16.):
        return None
    corners = np.float32([[[x1, y1], [x2, y1], [x2, y2], [x1, y2]]])
    projected = cv2.transform(corners, matrix)[0] / target_size
    raw = np.concatenate((projected.min(axis=0), projected.max(axis=0)))
    clean = raw.clip(0, 1)
    area = float((raw[2] - raw[0]) * (raw[3] - raw[1]))
    kept = float((clean[2] - clean[0]) * (clean[3] - clean[1]))
    if area <= 0 or kept / area < .80:
        return None
    return {**box, 'xyxy': clean.tolist()}


def intermediate_features(capture, first, second, max_gap, sift):
    import cv2

    first_time, second_time = (frame['timestampSeconds'] for frame in (first, second))
    steps = math.ceil((second_time - first_time) / max_gap)
    for index in range(1, steps):
        seconds = first_time + (second_time - first_time) * index / steps
        if not capture.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000):
            raise ValueError(f'Cannot seek video to {seconds:.3f} s')
        ok, frame = capture.read()
        if not ok:
            raise ValueError(f'Cannot decode video at {seconds:.3f} s')
        height, width = frame.shape[:2]
        scale = min(960 / max(width, height), 1)
        size = (max(1, round(scale * width)), max(1, round(scale * height)))
        gray = cv2.cvtColor(cv2.resize(frame, size, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
        yield frame_features(sift, gray, size)


def follow(boxes, sequence, matches):
    for (first, second), (left, right) in zip(zip(sequence, sequence[1:]), matches, strict=True):
        boxes = [projected for box in boxes
                 if (projected := project_box(box, first[0], second[0], left, right)) is not None]
        if not boxes:
            break
    return boxes


def compare(clips, cached, baseline, sift, videos=None, max_gap=.4):
    import cv2

    transferred = {name: [] for name in baseline}
    timings, links = [], []
    for clip_name, clip in clips.items():
        capture = cv2.VideoCapture(str(videos / clip_name)) if videos else None
        try:
            if capture is not None:
                fps = capture.get(cv2.CAP_PROP_FPS)
                if not .1 <= fps <= 1000 or abs(capture.get(cv2.CAP_PROP_FRAME_COUNT) / fps - clip['durationSeconds']) > .1:
                    raise ValueError(f'Video duration or frame rate differs: {clip_name}')
            frames = clip['frames']
            for a, b in zip(frames, frames[1:]):
                first, second = a['file'], b['file']
                if not baseline[first] and not baseline[second]:
                    continue
                started = perf_counter()
                intermediate = intermediate_features(capture, a, b, max_gap, sift) if capture is not None else ()
                sequence = [cached[first], *intermediate, cached[second]]
                pairs = [match_features(left, right) for left, right in zip(sequence, sequence[1:])]
                forward = follow(baseline[first], sequence, pairs)
                reverse = follow(baseline[second], sequence[::-1], [(r, l) for l, r in pairs[::-1]])
                transferred[second].extend(forward)
                transferred[first].extend(reverse)
                timings.append(round((perf_counter() - started) * 1000, 3))
                links.append({'first': first, 'second': second, 'matchesPerStep': [len(l) for l, _ in pairs],
                              'transfers': [len(forward), len(reverse)]})
        finally:
            if capture is not None:
                capture.release()
    combined = {name: merge_boxes(baseline[name] + boxes) for name, boxes in transferred.items()}
    confirmed = {name: [box for box in boxes if any(box['class'] == neighbor['class']
                                                  and overlap(box['xyxy'], neighbor['xyxy']) >= .30
                                                  for neighbor in transferred[name])]
                 for name, boxes in baseline.items()}
    return combined, confirmed, transferred, links, timings


def main():
    import cv2

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('annotations', nargs=2, type=Path)
    parser.add_argument('images', type=Path)
    parser.add_argument('box_report', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--videos', type=Path, help='Decode intermediate frames from the original local videos')
    parser.add_argument('--max-gap', type=float, default=.4, help='Maximum interval in seconds with --videos')
    args = parser.parse_args()
    sets = {path.stem: json.loads(path.read_bytes()) for path in args.annotations}
    clips = json.loads((args.images / 'clips.json').read_bytes())
    report_body = args.box_report.read_bytes()
    report = json.loads(report_body)
    baseline = report['predictions']
    rows = [row for annotation in sets.values() for row in annotation]
    expected = {row['file'] for row in rows}
    if not expected or len(expected) != len(rows) or expected != set(baseline):
        raise ValueError('Benchmark annotations and predictions must cover the same frames')
    video_hashes = {}
    for clip, data in clips.items():
        times = [frame['timestampSeconds'] for frame in data['frames']]
        if (Path(clip).name != clip or not times or not all(math.isfinite(t) for t in times)
                or not 0 <= times[0] <= times[-1] <= data['durationSeconds']
                or any(a >= b for a, b in zip(times, times[1:]))):
            raise ValueError(f'Invalid clip or sampling timestamps: {clip}')
        if args.videos:
            with (args.videos / clip).open('rb') as stream:
                video_hashes[clip] = hashlib.file_digest(stream, 'sha256').hexdigest()
    if not .1 <= args.max_gap <= 3:
        parser.error('--max-gap must be between 0.1 and 3 seconds')
    for row in rows:
        frame = clips[row['clip']]['frames'][row['frameIndex']]
        if any(row[key] != frame[key] for key in ('file', 'sha256', 'timestampSeconds')):
            raise ValueError(f'Annotation does not match sampled image: {row["file"]}')
    if any(box['class'] not in LABELS for boxes in baseline.values() for box in boxes):
        raise ValueError('Unexpected box class in input predictions')
    start = perf_counter()
    sift = cv2.SIFT_create(nfeatures=1800)
    cached = sampled_features(args.images, clips, sift)
    if set(cached) != expected:
        raise ValueError('Sampled clips must cover the same frames as the annotations')
    extraction = round((perf_counter() - start) * 1000, 3)
    combined, confirmed, transferred, links, timings = compare(clips, cached, baseline, sift, args.videos, args.max_gap)
    novel = {name: [box for box in boxes if box not in baseline[name]] for name, boxes in combined.items()}
    output = {'model': report['model'], 'revision': report['revision'], 'featureExtractionMs': extraction,
              'labels': LABELS, 'mode': 'tracked-candidates', 'predictions': novel,
              'inputReportSha256': hashlib.sha256(report_body).hexdigest(),
              'videoSha256': video_hashes, 'maxGapSeconds': args.max_gap if args.videos else None,
              'medianNeighborPairMs': median(timings) if timings else None, 'totalNeighborMs': sum(timings),
              'links': links,
              'transferred': transferred, 'combined': combined, 'confirmed': confirmed,
              'sets': {key: {'baseline': measure_reviewed(items, baseline),
                             'combined': measure_reviewed(items, combined),
                             'confirmed': measure_reviewed(items, confirmed)} for key, items in sets.items()}}
    args.output.write_text(json.dumps(output, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
