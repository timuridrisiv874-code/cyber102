"""Evaluate local Grounding DINO weights on the Cyber102 inspection benchmark."""
import argparse
from collections import Counter
import json
from pathlib import Path
import statistics
import time

from evaluate import class_negative_frames, measurements, merge_boxes, scene_coverage


PHRASES = {
    'crack': ('crack in metal', 'jagged break in metal'),
    'corrosion': ('patch of rust', 'corroded metal'),
    'terminal_damage': ('damaged electrical terminal', 'burned wire connector'),
    'wet_patch': ('leaking liquid', 'wet patch'),
}
INSULATION_PHRASES = {
    'insulation_hole': ('hole in pipe insulation jacket', 'puncture in metal cladding'),
    'insulation_open': ('open pipe insulation jacket', 'exposed insulation on pipe elbow'),
}


def query(tokenizer, phrases=PHRASES):
    text, spans = '', []
    for label, candidates in phrases.items():
        for candidate in candidates:
            spans.append((label, len(text), len(text) + len(candidate)))
            text += candidate + '. '
    offsets = tokenizer(text, return_offsets_mapping=True).offset_mapping
    positions = [(label, [i for i, (left, right) in enumerate(offsets)
                          if start <= left < right <= end]) for label, start, end in spans]
    return text, positions


def predict(model, processor, image, text, positions, device, min_score):
    import torch

    inputs = processor(images=image, text=text, return_tensors='pt').to(device)
    with torch.inference_mode():
        output = model(**inputs)
    probability = output.logits[0].sigmoid()
    scores = torch.stack([probability[:, tokens].mean(dim=1) for _, tokens in positions], dim=1)
    confidence, candidate = scores.max(dim=1)
    keep = torch.where(confidence >= min_score)[0]
    box = output.pred_boxes[0, keep]
    corners = torch.cat((box[:, :2] - box[:, 2:] / 2, box[:, :2] + box[:, 2:] / 2), dim=1)
    return merge_boxes([{'class': positions[label][0], 'score': score, 'xyxy': coords}
                        for label, score, coords in zip(candidate[keep].tolist(), confidence[keep].tolist(),
                                                        corners.clamp(0, 1).tolist(), strict=True)])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('model', type=Path)
    parser.add_argument('annotations', type=Path)
    parser.add_argument('images', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--device', default='cpu')
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--target', choices=('inspection', 'pipe-insulation'), default='inspection')
    args = parser.parse_args()
    from PIL import Image, ImageOps
    import torch
    from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

    torch.set_num_threads(args.threads)
    processor = AutoProcessor.from_pretrained(args.model, local_files_only=True)
    model = AutoModelForZeroShotObjectDetection.from_pretrained(
        args.model, local_files_only=True, use_safetensors=True).to(args.device).eval()
    phrases = INSULATION_PHRASES if args.target == 'pipe-insulation' else PHRASES
    text, positions = query(processor.tokenizer, phrases)
    predict(model, processor, Image.new('RGB', (420, 420)), text, positions, args.device, .05)

    timings, predictions = {}, {}
    rows = json.loads(args.annotations.read_text(encoding='utf-8'))
    for row in rows:
        name = row['file']
        with Image.open(args.images / name) as source:
            frame = ImageOps.exif_transpose(source).convert('RGB')
        start = time.perf_counter()
        predictions[name] = predict(model, processor, frame, text, positions, args.device, .05)
        timings[name] = round((time.perf_counter() - start) * 1000, 2)
        print(f"{name} {timings[name]} ms, {len(predictions[name])} boxes", flush=True)
    report = {'weights': str(args.model), 'phrases': phrases, 'device': args.device,
              'median_ms': statistics.median(timings.values()), 'timings_ms': timings,
              'support_by_scene': dict(Counter(row['scene'] for row in rows)),
              'metrics': {f'{confidence:.2f}/{iou:.2f}': measurements(rows, predictions, sorted(phrases), confidence, iou)
                          for confidence in (.05, .10, .15, .25, .40) for iou in (.30, .50)},
              'class_negative_frames_at_0.25': class_negative_frames(rows, predictions, sorted(phrases), .25),
              'predictions': predictions}
    if all('site' in row for row in rows):
        site_rows = [dict(row, scene=row['site']) for row in rows]
        report['site_coverage_at_0.25/0.50'] = scene_coverage(site_rows, predictions, sorted(phrases), .25, .50)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
