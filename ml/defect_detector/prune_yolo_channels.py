"""Prune private channels in YOLO11 and YOLO26 bottlenecks and attention feed-forward layers."""
import argparse
from copy import deepcopy
from hashlib import file_digest
import json
from pathlib import Path

import torch
from torch import nn
from ultralytics import YOLO, settings
from ultralytics.models.yolo.detect import DetectionTrainer
from ultralytics.nn.modules import Bottleneck
from ultralytics.nn.modules.block import C3k2, PSABlock


def shrink_pair(first, second, fraction):
    """Remove matched channels while preserving the block's public input and output shapes."""
    a, b = first.conv, second.conv
    norm = first.bn
    if (a.groups != 1 or b.groups != 1 or a.out_channels != b.in_channels
            or not isinstance(norm, nn.BatchNorm2d) or norm.num_features != a.out_channels):
        return 0
    width = a.out_channels
    keep = max(16, round(width * (1 - fraction) / 8) * 8)
    if keep >= width:
        return 0

    with torch.no_grad():
        scale = norm.weight.abs() / (norm.running_var + norm.eps).sqrt()
        strength = a.weight.flatten(1).norm(dim=1) * scale
        strength *= b.weight.transpose(0, 1).reshape(width, -1).norm(dim=1)
        indices = strength.argsort(descending=True, stable=True)[:keep].sort().values
        a.weight = nn.Parameter(a.weight.index_select(0, indices))
        if a.bias is not None:
            a.bias = nn.Parameter(a.bias.index_select(0, indices))
        a.out_channels = keep
        norm.weight = nn.Parameter(norm.weight.index_select(0, indices))
        norm.bias = nn.Parameter(norm.bias.index_select(0, indices))
        norm.running_mean = norm.running_mean.index_select(0, indices)
        norm.running_var = norm.running_var.index_select(0, indices)
        norm.num_features = keep
        b.weight = nn.Parameter(b.weight.index_select(1, indices))
        b.in_channels = keep
    return width - keep


def prune(model, fraction):
    layers = {}
    for name, block in list(model.named_modules()):
        if isinstance(block, Bottleneck):
            removed = shrink_pair(block.cv1, block.cv2, fraction)
        elif isinstance(block, PSABlock):
            removed = shrink_pair(*block.ffn, fraction)
        else:
            continue
        if removed:
            layers[name] = removed
    if not layers:
        raise ValueError('No supported internal channels could be removed')
    return layers


class PreservePrunedShapes(DetectionTrainer):
    def get_model(self, cfg=None, weights=None, verbose=True):
        if weights is None or weights.names != self.data['names']:
            raise ValueError('Recovery data must have the checkpoint class order')
        # Rebuilding from the original YAML would recreate all removed channels.
        return deepcopy(weights).float()


def count_parameters(model):
    return sum(parameter.numel() for parameter in model.parameters())


def checksum(path):
    with Path(path).open('rb') as stream:
        return file_digest(stream, 'sha256').hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('weights', type=Path)
    p.add_argument('output', type=Path)
    p.add_argument('--fraction', type=float, required=True)
    p.add_argument('--size', type=int, default=960)
    p.add_argument('--data', type=Path)
    p.add_argument('--recover', type=int, default=0, help='Number of recovery training epochs')
    p.add_argument('--device', default='0')
    p.add_argument('--batch', type=int, default=2)
    p.add_argument('--smoke', action='store_true', help='Run forward validation at 128 px without ONNX export')
    args = p.parse_args()
    if not 0 < args.fraction < .9 or args.size <= 0 or args.size % 32 or args.batch < 1:
        p.error('Specify a valid pruning fraction, input size and batch')
    if args.recover < 0 or (args.recover and (args.data is None or args.smoke)):
        p.error('Recovery requires --data and a normal export')
    settings.update({'sync': False})
    source = args.weights.resolve(strict=True)
    detector = YOLO(str(source), task='detect')
    model = detector.model.float().eval()
    if not any(isinstance(layer, C3k2) for layer in model.model):
        raise ValueError('Expected a YOLO detector with C3k2 blocks')
    before = count_parameters(model)
    head = model.model[-1]
    old_shape = (head.nc, head.nl, head.reg_max, getattr(head, 'end2end', False))
    classes = dict(detector.names)
    layers = prune(model, args.fraction)
    after = count_parameters(model)
    if after >= before or (head.nc, head.nl, head.reg_max, getattr(head, 'end2end', False)) != old_shape:
        raise ValueError('Pruning did not preserve detection head semantics')

    test_size = 128 if args.smoke else args.size
    torch.set_num_threads(4)
    with torch.inference_mode():
        probe = model(torch.zeros((1, 3, test_size, test_size)))
        output = probe[0] if isinstance(probe, tuple) else probe
        end_to_end = old_shape[3]
        channel_axis, channels = (-1, 6) if end_to_end else (1, 4 + head.nc)
        if output.ndim != 3 or output.shape[channel_axis] != channels or not torch.isfinite(output).all():
            raise ValueError('Pruned checkpoint returned an invalid prediction tensor')
    args.output.mkdir(parents=True, exist_ok=False)
    raw = args.output / 'pruned.pt'
    detector.save(raw)
    exported = None
    checkpoint = raw
    if args.recover:
        detector = YOLO(str(raw), task='detect')
        detector.train(trainer=PreservePrunedShapes, data=str(args.data.resolve(strict=True)),
                       imgsz=args.size, epochs=args.recover, device=args.device, batch=args.batch,
                       project=str(args.output.resolve()), name='recovery', exist_ok=False,
                       workers=0, cache=False, amp=False, mosaic=0, optimizer='AdamW', lr0=.00008,
                       plots=False, seed=102)
        checkpoint = Path(detector.trainer.best)
    runtime_shape = None
    if not args.smoke:
        candidate = YOLO(str(checkpoint), task='detect')
        if count_parameters(candidate.model) != after or candidate.names != classes:
            raise ValueError('Reloaded checkpoint does not retain the pruned architecture')
        exported = Path(candidate.export(format='onnx', imgsz=args.size, opset=17,
                                         dynamic=False, simplify=False, device='cpu'))
        import numpy as np
        import onnxruntime

        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = 4
        session = onnxruntime.InferenceSession(str(exported), sess_options=options,
                                               providers=['CPUExecutionProvider'])
        input_tensor = session.get_inputs()[0]
        result, = session.run(None, {input_tensor.name: np.zeros((1, 3, args.size, args.size), dtype=np.float32)})
        if result.shape != tuple(output.shape) or not np.isfinite(result).all():
            raise ValueError('ONNX Runtime did not preserve the expected detection output')
        runtime_shape = list(result.shape)
    report = {'sourceSha256': checksum(source), 'parametersBefore': before, 'parametersAfter': after,
              'removedChannels': layers, 'fraction': args.fraction, 'imageSize': test_size,
              'checkpoint': str(checkpoint), 'checkpointBytes': checkpoint.stat().st_size,
              'checkpointSha256': checksum(checkpoint), 'classes': classes,
              'onnx': str(exported) if exported else None, 'onnxBytes': exported.stat().st_size if exported else None,
              'onnxSha256': checksum(exported) if exported else None, 'onnxOutputShape': runtime_shape}
    (args.output / 'pruning.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
