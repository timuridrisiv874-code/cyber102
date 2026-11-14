"""Rank compressed YOLO candidates by size after strict holdout regression checks."""
import argparse
from collections import Counter
from hashlib import file_digest
import json
from pathlib import Path


def compare(baseline, candidate, thresholds):
    failures, positives = [], Counter()
    if baseline.keys() != candidate.keys() or not baseline:
        raise ValueError('Expected identical, nonempty dataset coverage')
    for name, test in baseline.items():
        reference = test['summary']
        actual = candidate[name]['summary']
        if reference.keys() != actual.keys() or not reference:
            raise ValueError(f'Class coverage differs in {name}')
        for label, row in reference.items():
            result = actual[label]
            for cutoff in thresholds:
                a = row['thresholds'][cutoff]
                b = result['thresholds'][cutoff]
                if a['tp'] + a['fn'] != b['tp'] + b['fn']:
                    raise ValueError(f'Mismatched support for {name}/{label}')
                if a['tp'] > b['tp'] or a['fp'] < b['fp']:
                    failures.append({'dataset': name, 'class': label, 'threshold': cutoff,
                                     'tpBefore': a['tp'], 'tpAfter': b['tp'],
                                     'fpBefore': a['fp'], 'fpAfter': b['fp']})
                before_hits = row['matchedObjects'][cutoff]
                after_hits = result['matchedObjects'][cutoff]
                if (sum(map(len, before_hits.values())) != a['tp']
                        or sum(map(len, after_hits.values())) != b['tp']):
                    raise ValueError(f'Matched objects disagree with metrics for {name}/{label}')
                for frame, hits in before_hits.items():
                    missing = sorted(set(hits) - set(after_hits.get(frame, ())))
                    if missing:
                        failures.append({'dataset': name, 'class': label, 'threshold': cutoff,
                                         'frame': frame, 'lostObjects': missing})
                if cutoff == thresholds[0]:
                    positives[label] += a['tp']
            for cutoff, clean in row['negativeFrames'].items():
                if cutoff not in thresholds:
                    continue
                after = result['negativeFrames'][cutoff]
                if clean['count'] != after['count']:
                    raise ValueError(f'Mismatched negative support for {name}/{label}')
                new = sorted(set(after['flagged']) - set(clean['flagged']))
                if new:
                    failures.append({'dataset': name, 'class': label, 'threshold': cutoff,
                                     'newFalseAlerts': new})
    return failures, positives


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('evaluation', type=Path)
    p.add_argument('output', type=Path)
    p.add_argument('--baseline', required=True)
    p.add_argument('--baseline-onnx', type=Path, required=True)
    p.add_argument('--candidate', action='append', nargs=2, required=True, metavar=('NAME', 'PRUNING_JSON'))
    p.add_argument('--size', type=int, default=960)
    p.add_argument('--max-bytes', type=int, help='Require a specific deployment size in addition to compression')
    args = p.parse_args()
    if args.max_bytes is not None and args.max_bytes < 1:
        p.error('--max-bytes must be positive')
    runs = json.loads(args.evaluation.read_bytes())['runs']
    baseline = runs[args.baseline]
    baseline_bytes = args.baseline_onnx.stat().st_size
    limit = min(baseline_bytes - 1, args.max_bytes or baseline_bytes)
    thresholds = ('0.05', '0.25')
    results, eligible = {}, []
    for name, path in args.candidate:
        if name in results:
            raise ValueError(f'Duplicate candidate: {name}')
        pruning = json.loads(Path(path).read_bytes())
        run = runs[name]
        if pruning['sourceSha256'] != baseline['sha256'] or pruning['checkpointSha256'] != run['sha256']:
            raise ValueError(f'Checkpoint provenance does not match benchmark: {name}')
        size = str(args.size)
        failures, support = compare(baseline['variants'][size], run['variants'][size], thresholds)
        onnx = Path(pruning['onnx'])
        onnx_bytes = onnx.stat().st_size
        with onnx.open('rb') as file:
            digest = file_digest(file, 'sha256').hexdigest()
        if (pruning['onnxBytes'] != onnx_bytes or pruning['onnxSha256'] != digest
                or not pruning['onnxOutputShape']):
            raise ValueError(f'Expected verified ONNX export: {name}')
        unsupported = sorted(label for label, hits in support.items() if not hits)
        passed = bool(support and not unsupported and not failures and 0 < onnx_bytes <= limit
                      and pruning['parametersAfter'] < pruning['parametersBefore'])
        results[name] = {'passed': passed, 'regressions': failures, 'baselineHitsByClass': support,
                         'unverifiedClasses': unsupported,
                         'onnxBytes': onnx_bytes, 'onnx': str(onnx),
                         'parameters': pruning['parametersAfter'],
                         'peakGpuAllocatedBytes': max((row.get('peakGpuAllocatedBytes') or 0
                                                       for row in run['variants'][size].values()), default=0) or None,
                         'medianMsByDataset': {k: v['medianMs'] for k, v in run['variants'][size].items()}}
        if passed:
            eligible.append((onnx_bytes, name))
    report = {'thresholds': thresholds, 'baselineBytes': baseline_bytes, 'maxBytes': limit,
              'selected': min(eligible)[1] if eligible else None, 'candidates': results}
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
