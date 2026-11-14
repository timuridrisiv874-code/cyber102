"""Check single-depth planar scale on ARDepthLimits v0.4, not the two-depth UI or EXIF.

Usage: python3 ios/Tests/check_square_depth_scale.py /path/to/ARDepthLimits-0.4.zip
Data: Denys Markov and Vira Babenko, https://doi.org/10.5281/zenodo.23577624 (CC BY 4.0).
The archive contains one scene-depth sample per frame and four independently detected corner pixels.
"""

import argparse
import csv
import hashlib
import io
import json
import math
import statistics
import zipfile
from collections import defaultdict
from pathlib import Path

ARCHIVE_SHA = '7961968edbdee3de5b62f1a654f117d9c3dbfd7c4c73bb10615f9de390d0fa93'
ROOT = 'ARDepthLimits-0.4/Data/'


def side_estimate_mm(frame):
    depth = frame['depth']['distance']
    corners = frame['square']['corners']
    camera = frame['intrinsics']
    edges = zip(corners, corners[1:] + corners[:1])
    # Same axial Z at both ends, so principal point cancels. Ground truth is never used here.
    return statistics.mean(1000 * depth * math.hypot((b['x'] - a['x']) / camera['fx'],
                                                      (b['y'] - a['y']) / camera['fy'])
                           for a, b in edges)


def metrics(values, reference):
    absolute = [abs(value - reference) for value in values]
    return {k: round(v, 4) for k, v in {
        'median_estimate_mm': statistics.median(values),
        'mean_absolute_error_mm': statistics.mean(absolute),
        'median_absolute_error_mm': statistics.median(absolute),
        'p95_absolute_error_mm': statistics.quantiles(absolute, n=100, method='inclusive')[94],
        'max_absolute_error_mm': max(absolute),
    }.items()}


def check(path):
    if hashlib.sha256(path.read_bytes()).hexdigest() != ARCHIVE_SHA:
        raise ValueError('Expected ARDepthLimits v0.4 archive')
    with zipfile.ZipFile(path) as data:
        def table(file):
            return csv.DictReader(io.StringIO(data.read(ROOT + 'Analysis/tables/' + file + '.csv').decode()))
        records = {(r['session'], int(r['record'])): r for r in table('records') if r['holding'] == 'mount'}
        flagged = {(r['session'], int(r['record']), int(r['frame'])) for r in table('frames')
                   if r['misfound_corner'] == 'True'}
        all_values, distances, sessions, groups = [], [], set(), defaultdict(list)
        record_estimates = []
        for name in sorted(data.namelist()):
            if not name.startswith(ROOT + 'Sessions/ARDepthLimits ') or not name.endswith('.json'):
                continue
            session = json.loads(data.read(name))
            key = Path(name).stem
            for i, record in enumerate(session['records'], 1):
                if (key, i) not in records:
                    continue
                values = []
                for j, frame in enumerate(record['frames']):
                    if (key, i, j) in flagged or frame.get('trackingState') != 'normal':
                        continue
                    depth = frame.get('depth', {})
                    square = frame.get('square', {})
                    if (depth.get('confidence', -1) < 1 or not 0.25 < depth.get('distance', 0) <= 0.75
                            or not 0 <= square.get('tilt', 180) <= 5):
                        continue
                    values.append(side_estimate_mm(frame))
                    distances.append(depth['distance'])
                if values:
                    sessions.add(key)
                    all_values.extend(values)
                    representative = statistics.median(values)
                    record_estimates.append(representative)
                    groups[records[key, i]['group']].append(representative)
        # The real print was independently measured as 50.19 by 50.25 mm with a steel ruler.
        # Their mean matches our per-frame mean across the four edges of the print.
        reference = (50.19 + 50.25) / 2
        return {'doi': '10.5281/zenodo.23577624', 'hardware': 'one iPhone 17 Pro',
                'method': 'same sceneDepth sample at both endpoints; mean of four edges',
                'selection': 'mount, normal tracking, valid corners, confidence >= medium, tilt <= 5 deg, 0.25 < z <= 0.75 m',
                'sessions': len(sessions), 'frames': len(all_values), 'records': len(record_estimates),
                'repeated_placement_groups': len(groups), 'observed_depth_range_m': [min(distances), max(distances)],
                'reference_mm': reference, 'frame_weighted': metrics(all_values, reference),
                'record_weighted': metrics(record_estimates, reference),
                'group_weighted': metrics([statistics.median(v) for v in groups.values()], reference)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    print(json.dumps(check(parser.parse_args().archive), indent=2))
