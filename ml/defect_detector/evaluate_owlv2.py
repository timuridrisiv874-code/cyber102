"""Benchmark a verified local OWLv2 snapshot on annotated inspection photos."""
import argparse
import hashlib
import json
from pathlib import Path
from statistics import median
from time import perf_counter

from evaluate import class_negative_frames, measurements, merge_boxes, scene_coverage


PROMPTS = {
    'crack': ('a crack in metal', 'a jagged split in a metal housing', 'a ruptured steel pipe'),
    'terminal_damage': ('a burnt electrical terminal', 'a melted wire connector', 'a severed terminal wire'),
    'corrosion': ('a patch of rust on metal', 'corroded metal'),
    'wet_patch': ('a patch of leaked liquid', 'liquid dripping from equipment'),
}
WEIGHTS_SHA256 = 'e1e130b9e404cf91a75ad45644c1da9d7fa5284085eecc864266a6923efb99e7'
SCORE_FLOOR = .025


def detections(result, labels, width, height):
    boxes = []
    for index, score, xyxy in zip(result['labels'].tolist(), result['scores'].tolist(),
                                  result['boxes'].tolist(), strict=True):
        left, top, right, bottom = (
            max(0., min(1., value / edge))
            for value, edge in zip(xyxy, (width, height, width, height), strict=True)
        )
        if left < right and top < bottom:
            boxes.append({'class': labels[index], 'xyxy': [left, top, right, bottom], 'score': score})
    return merge_boxes(boxes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('model', type=Path)
    parser.add_argument('annotations', type=Path)
    parser.add_argument('images', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--device', choices=('cpu', 'cuda'), default='cpu')
    parser.add_argument('--labels', nargs='+', choices=tuple(PROMPTS), required=True)
    args = parser.parse_args()
    labels = tuple(dict.fromkeys(args.labels))
    prompts = [(label, prompt) for label in labels for prompt in PROMPTS[label]]
    class_ids, text = zip(*prompts)
    rows = json.loads(args.annotations.read_bytes())
    names = {row['file'] for row in rows}
    if not names or len(names) != len(rows) or any(Path(name).name != name for name in names):
        raise ValueError('Annotation filenames must be unique basenames')
    for row in rows:
        actual = hashlib.sha256((args.images / row['file']).read_bytes()).hexdigest()
        if actual != row['sha256']:
            raise ValueError(f"Unexpected image bytes: {row['file']}")
    with (args.model / 'model.safetensors').open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != WEIGHTS_SHA256:
            raise ValueError('OWLv2 weights do not match the pinned snapshot')

    from PIL import Image, ImageOps
    import torch
    from transformers import Owlv2ForObjectDetection, Owlv2Processor

    torch.set_num_threads(4)
    processor = Owlv2Processor.from_pretrained(args.model, local_files_only=True)
    model = Owlv2ForObjectDetection.from_pretrained(args.model, local_files_only=True,
                                                    use_safetensors=True).to(args.device).eval()

    def infer(image):
        width, height = image.size
        inputs = processor(text=[list(text)], images=image, return_tensors='pt').to(args.device)
        with torch.inference_mode():
            output = model(**inputs)
            result = processor.post_process_grounded_object_detection(
                output, target_sizes=torch.tensor([(height, width)], device=args.device),
                threshold=SCORE_FLOOR)[0]
        return detections(result, class_ids, width, height)

    with Image.new('RGB', (512, 384), 'white') as blank:
        infer(blank)
    predictions, timings = {}, {}
    for row in rows:
        name = row['file']
        with Image.open(args.images / name) as source, ImageOps.exif_transpose(source).convert('RGB') as image:
            start = perf_counter()
            predictions[name] = infer(image)
            timings[name] = round((perf_counter() - start) * 1000, 3)
        print(f'{name}: {len(predictions[name])} boxes in {timings[name]:.0f} ms', flush=True)

    report = {'model': 'google/owlv2-base-patch16-ensemble', 'weightsSha256': WEIGHTS_SHA256,
              'device': args.device, 'prompts': {label: PROMPTS[label] for label in labels},
              'medianMs': median(timings.values()), 'timingsMs': timings,
              'metrics': {f'{score:.3f}': measurements(rows, predictions, labels, score, .50)
                          for score in (SCORE_FLOOR, .05, .10, .15, .25, .40)},
              'scenesAt0.10': scene_coverage(rows, predictions, labels, .10, .50),
              'negativeFramesAt0.10': class_negative_frames(rows, predictions, labels, .10),
              'predictions': predictions}
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
