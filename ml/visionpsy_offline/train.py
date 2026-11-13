"""Train binary findings on VisionPsy projector tokens exported as little-endian floats."""
import argparse
import json
import struct
from pathlib import Path

import numpy as np
import torch
from torch.nn import functional as F

CLASSES = ('crack', 'terminal', 'rust', 'liquid')
DIMENSION = 960


def read_features(path):
    patches = []
    with path.open('rb') as source:
        while header := source.read(8):
            count, dimension = struct.unpack('<II', header)
            if dimension != DIMENSION or not 0 < count <= 1024:
                raise ValueError(f'{path}: unexpected image token shape')
            values = np.frombuffer(source.read(count * dimension * 4), dtype='<f4')
            patches.append(values.reshape(count, dimension))
    tokens = np.concatenate(patches)
    return np.concatenate((tokens.mean(0), tokens.max(0)))


def fit(features, targets, indices, pooling, ridge, normalized):
    """Transform the inputs for fitting, then fold the scaling into the weights."""
    x = features[indices]
    y = targets[indices]
    mean = x.mean(0)
    scale = x.std(0).clamp_min(.02) if normalized else torch.ones_like(mean)
    x = (x - mean) / scale
    weights = torch.zeros((len(CLASSES), x.shape[1]), device=x.device, requires_grad=True)
    bias = torch.zeros(len(CLASSES), device=x.device, requires_grad=True)
    pos_weights = ((1 - y.mean(0)) / y.mean(0).clamp_min(.01)).pow(.65)
    optimizer = torch.optim.AdamW([weights, bias], lr=.008, weight_decay=0)
    for _ in range(170):
        optimizer.zero_grad()
        logits = F.linear(x, weights, bias)
        loss = F.binary_cross_entropy_with_logits(logits, y, pos_weight=pos_weights)
        loss = loss + ridge * weights.square().sum() / len(CLASSES)
        loss.backward()
        optimizer.step()
    with torch.no_grad():
        coefficients = weights / scale
        offset = bias - coefficients @ mean
        if pooling == 'mean':
            coefficients[:, DIMENSION:] = 0
        elif pooling == 'max':
            coefficients[:, :DIMENSION] = 0
    return coefficients, offset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('features', type=Path, help='Directory containing NAME.jpg.bin files')
    parser.add_argument('labels', type=Path, help='JSON array of file, group, labels. Paths start with train/ or eval/.')
    parser.add_argument('output', type=Path, help='Path ending in .bin for this trained candidate')
    parser.add_argument('--pooling', choices=['mean_max', 'mean', 'max'], default='mean_max')
    parser.add_argument('--ridge', type=float, required=True)
    parser.add_argument('--normalized', action='store_true')
    parser.add_argument('--device', default='cuda')
    args = parser.parse_args()
    rows = sorted(json.loads(args.labels.read_text()), key=lambda row: row['file'])
    training = [i for i, row in enumerate(rows) if row['file'].startswith('train/')]
    evaluation = [i for i, row in enumerate(rows) if row['file'].startswith('eval/')]
    groups = {rows[i]['group'] for i in training}
    if groups.intersection(rows[i]['group'] for i in evaluation):
        raise ValueError('Training and evaluation contain overlapping source groups')
    features = np.stack([read_features(args.features / (Path(row['file']).name + '.bin')) for row in rows])
    if args.pooling == 'mean':
        features[:, DIMENSION:] = 0
    elif args.pooling == 'max':
        features[:, :DIMENSION] = 0
    features = torch.from_numpy(features).to(args.device)
    target = torch.tensor([[c in row['labels'] for c in CLASSES] for row in rows],
                          dtype=torch.float32, device=args.device)
    weights, bias = fit(features, target, training, args.pooling, args.ridge, args.normalized)
    scores = F.linear(features[evaluation], weights, bias).sigmoid().cpu().numpy()
    report = {'train_groups': len(groups), 'train_images': len(training), 'eval': [
        dict(rows[index], scores=dict(zip(CLASSES, map(float, row))))
        for index, row in zip(evaluation, scores)
    ]}
    # The combiner writes the release thresholds after candidate comparison.
    payload = torch.cat((weights, bias[:, None], torch.full_like(bias[:, None], .5)), dim=1).cpu().numpy()
    args.output.write_bytes(struct.pack('<4sII', b'VPH1', DIMENSION, len(CLASSES)) + payload.astype('<f4').tobytes())
    args.output.with_suffix('.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
