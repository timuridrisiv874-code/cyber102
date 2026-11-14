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
            for cutoff in thresholds:
                clean = row['negativeFrames'][cutoff]
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
    p.add_argument('--export-evaluation', type=Path,
                   help='Benchmark using --square, with ONNX runs named <model>-onnx')
    p.add_argument('--size', type=int, default=960)
    p.add_argument('--max-bytes', type=int, help='Require a specific deployment size in addition to compression')
    args = p.parse_args()
    if args.max_bytes is not None and args.max_bytes < 1:
        p.error('--max-bytes must be positive')
    evaluation = json.loads(args.evaluation.read_bytes())
    runs = evaluation['runs']
    exported = json.loads(args.export_evaluation.read_bytes()) if args.export_evaluation else None
    if exported is not None:
        if (not exported['squarePadding'] or exported['imageHashes'] != evaluation['imageHashes']
                or exported['annotationHashes'] != evaluation['annotationHashes']):
            raise ValueError('ONNX evaluation needs square padding and identical benchmark images and annotations')
        baseline_onnx = exported['runs'][f'{args.baseline}-onnx']
        with args.baseline_onnx.open('rb') as file:
            if baseline_onnx['sha256'] != file_digest(file, 'sha256').hexdigest():
                raise ValueError('Full ONNX export differs from the benchmark')
    baseline = runs[args.baseline]
    baseline_bytes = args.baseline_onnx.stat().st_size
    limit = min(baseline_bytes - 1, args.max_bytes or baseline_bytes)
    thresholds = ('0.05', '0.15', '0.25')
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
        checkpoint_passed = bool(support and not unsupported and not failures and 0 < onnx_bytes <= limit
                                 and pruning['parametersAfter'] < pruning['parametersBefore'])
        export_check = None
        if exported is not None:
            square_pt = exported['runs'][name]
            square_onnx = exported['runs'][f'{name}-onnx']
            if (square_pt['sha256'] != run['sha256'] or square_onnx['sha256'] != digest
                    or Path(square_onnx['artifact']).suffix.lower() != '.onnx'):
                raise ValueError(f'Artifact differs from ONNX evaluation: {name}')
            runtime = square_onnx['variants'][size]
            parity, _ = compare(square_pt['variants'][size], runtime, thresholds)
            baseline_regressions, export_support = compare(baseline_onnx['variants'][size], runtime, thresholds)
            native_regressions, _ = compare(baseline['variants'][size], runtime, thresholds)
            missing = sorted(label for label in support if not export_support[label])
            export_check = {'passed': not (parity or baseline_regressions or native_regressions or missing),
                            'exportRegressions': parity, 'fullExportRegressions': baseline_regressions,
                            'fullCheckpointRegressions': native_regressions, 'unverifiedClasses': missing,
                            'device': exported['device'],
                            'medianMsByDataset': {key: row['medianMs'] for key, row in runtime.items()}}
        passed = checkpoint_passed and bool(export_check and export_check['passed'])
        results[name] = {'passed': passed, 'checkpointPassed': checkpoint_passed, 'onnxEvaluation': export_check,
                         'regressions': failures, 'baselineHitsByClass': support,
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
