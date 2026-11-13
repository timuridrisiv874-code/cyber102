"""Compare local InternVL profiles on identical frames, without claiming accuracy.

Run on the inference host, or against a loopback SSH port forward:
python -m vision_runtime.benchmark_profiles --repeat 3 --output var/profile-benchmark.json
The synthetic/unlabelled samples are a functional and latency check, not ground truth.
"""
import argparse
import base64
import hashlib
import ipaddress
import json
import math
from pathlib import Path
from statistics import median
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit

from server.vision import request_json, result_from_observation
from vision_runtime.benchmark import images
from vision_runtime.worker import MODEL, OBSERVATION_KINDS, PROFILES, REVISION


def local_endpoint(value):
    """Do not allow a benchmark to forward private test images to an external host."""
    try:
        target = urlsplit(value)
        if (target.scheme != 'http' or target.username or target.password or target.query or target.fragment
                or target.path != '/analyze' or not target.port
                or not ipaddress.ip_address(target.hostname).is_loopback):
            raise ValueError()
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError('Use http://127.0.0.1:PORT/analyze or an IPv6 loopback URL') from None
    return value


def percentile95(values):
    return sorted(values)[max(0, math.ceil(len(values) * 0.95) - 1)] if values else None


def summarize(records, profiles):
    summary = {}
    for profile in profiles:
        rows = [row for row in records if row['profile'] == profile and row['phase'] == 'measured']
        durations = [row['seconds'] for row in rows if 'result' in row]
        summary[profile] = {
            'requests': len(rows), 'successful': len(durations), 'errors': len(rows) - len(durations),
            'medianSeconds': median(durations) if durations else None,
            'p95Seconds': percentile95(durations),
            'allRequestP95Seconds': percentile95([row['seconds'] for row in rows]),
        }
    return summary


def box_iou(a, b):
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0, right - left) * max(0, bottom - top)
    area_a = max(0, a[2] - a[0]) * max(0, a[3] - a[1])
    area_b = max(0, b[2] - b[0]) * max(0, b[3] - b[1])
    union = area_a + area_b - intersection
    return round(intersection / union, 4) if union else None


def compare(records, baseline='standard'):
    measured = [row for row in records if row['phase'] == 'measured']
    originals = {(row['case'], row['repeat']): row for row in measured if row['profile'] == baseline}
    comparisons = []
    for row in measured:
        if row['profile'] == baseline:
            continue
        original = originals.get((row['case'], row['repeat']))
        if original is None:
            continue
        match = {'case': row['case'], 'repeat': row['repeat'], 'baseline': baseline, 'profile': row['profile']}
        if 'result' not in row or 'result' not in original:
            match['comparable'] = False
        else:
            a, b = original['result']['observation'], row['result']['observation']
            match.update({
                'comparable': True,
                'statusChanged': original['status'] != row['status'],
                'componentChanged': a['component'] != b['component'],
                'conditionChanged': a['condition'] != b['condition'],
                'observationChanged': a['observation'] != b['observation'],
                'boxIoU': box_iou(a['box'], b['box']) if original['status'] != 'unusable' and row['status'] != 'unusable' else None,
            })
        comparisons.append(match)
    return comparisons


def run_request(endpoint, profile, sample, phase, repeat):
    name, data, source = sample
    row = {'case': name, 'source': source, 'sha256': hashlib.sha256(data).hexdigest(),
           'phase': phase, 'repeat': repeat, 'profile': profile}
    started = time.perf_counter()
    try:
        response = request_json(endpoint, {'image': base64.b64encode(data).decode('ascii'), 'profile': profile}, timeout=100)
        if (response.get('model') != MODEL or response.get('profile') != profile
                or response.get('observationKind') != OBSERVATION_KINDS[profile]):
            raise ValueError('Unexpected model/profile; the worker may need updating')
        row['status'] = result_from_observation(response['observation'], model=MODEL)['status']
        row['result'] = response
    except HTTPError as error:
        row['error'] = f'HTTP {error.code}'
    except Exception as error:
        row['error'] = f'{type(error).__name__}: {error}'
    row['seconds'] = round(time.perf_counter() - started, 3)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', type=local_endpoint, default='http://127.0.0.1:11436/analyze')
    parser.add_argument('--profiles', nargs='+', choices=tuple(PROFILES), default=list(PROFILES))
    parser.add_argument('--repeat', type=int, default=3)
    parser.add_argument('--warmup-rounds', type=int, default=1)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.repeat <= 100 or not 0 <= args.warmup_rounds <= 10:
        parser.error('repeat must be 1..100 and warmup-rounds 0..10')
    if len(set(args.profiles)) != len(args.profiles):
        parser.error('profiles must not contain duplicates')
    samples = list(images())
    if not samples:
        parser.error('No sample images are available')
    records = []
    for phase, rounds, phase_samples in (
        ('warmup', args.warmup_rounds, samples[:1]),
        ('measured', args.repeat, samples),
    ):
        for repeat in range(rounds):
            for index, sample in enumerate(phase_samples):
                # Alternate order to reduce the bias of always running one profile first.
                order = args.profiles if (repeat + index) % 2 == 0 else args.profiles[::-1]
                for profile in order:
                    row = run_request(args.endpoint, profile, sample, phase, repeat)
                    records.append(row)
                    print(json.dumps(row, ensure_ascii=False), flush=True)
    report = {
        'scope': 'Functional and latency comparison on identical synthetic/unlabelled images. '
                 'Differences are disagreements, not correctness or accuracy measurements. '
                 'Warmup requests are reported separately; cold-start latency is not measured.',
        'model': MODEL, 'expectedRevision': REVISION,
        'profiles': {name: {'maxPatches': PROFILES[name], 'maxNewTokens': 180,
                            'outputMode': 'compact' if name == 'fast' else 'descriptive',
                            'observationKind': OBSERVATION_KINDS[name]} for name in args.profiles},
        'records': records, 'summary': summarize(records, args.profiles),
        'comparisons': compare(records, baseline=args.profiles[0]),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
