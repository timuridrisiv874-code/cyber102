"""Export the selected per-class heads to the Android linear-head format."""
import argparse
import json
import math
import struct
from pathlib import Path

CLASSES = ('crack', 'terminal', 'rust', 'liquid')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('configuration', nargs=len(CLASSES), help='One candidate[:weight][+candidate:weight]@threshold per class')
    args = parser.parse_args()
    result = bytearray(struct.pack('<4sII', b'VPH1', 960, len(CLASSES)))
    row_size = (960 * 2 + 2) * 4
    report = {}
    for index, (label, entry) in enumerate(zip(CLASSES, args.configuration)):
        mixtures, threshold_text = entry.split('@')
        limit = float(threshold_text)
        if not 0 < limit < 1:
            raise ValueError(f'Invalid threshold for {label}')
        offset = 12 + row_size * index
        combined = [0.0] * (960 * 2 + 1)
        logits = None
        assigned = 0.0
        for item in mixtures.split('+'):
            candidate, _, weight_text = item.partition(':')
            fraction = float(weight_text or '1')
            assigned += fraction
            payload = (args.directory / f'{candidate}.bin').read_bytes()
            if payload[:12] != result[:12] or len(payload) != 12 + row_size * len(CLASSES):
                raise ValueError(f'Invalid weight format in {candidate}')
            values = struct.unpack_from(f'<{len(combined)}f', payload, offset)
            combined = [a + fraction * b for a, b in zip(combined, values)]
            data = json.loads((args.directory / f'{candidate}.json').read_text())
            observed = {}
            for row in data['eval']:
                value = max(0.000001, min(0.999999, row['scores'][label]))
                observed[row['file']] = fraction * math.log(value / (1 - value))
            logits = observed if logits is None else {
                name: value + observed[name] for name, value in logits.items() if name in observed
            }
        if not math.isclose(assigned, 1.0):
            raise ValueError(f'Weights must sum to 1 for {label}')
        result.extend(struct.pack(f'<{len(combined) + 1}f', *combined, limit))
        for name, logit in logits.items():
            value = 1 / (1 + math.exp(-logit))
            report.setdefault(name, {})[label] = {'score': round(value, 4), 'detected': value >= limit}
    args.output.write_bytes(result)
    args.output.with_suffix('.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
