"""Measure local refinements of frozen, visually confirmed crack boxes."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from statistics import median
import sys
from time import perf_counter

from evaluate import merge_boxes, overlap
from evaluate_qwen_boxes import checked_images, parse_detections
from evaluate_video_component_crops import project
from evaluate_video_component_gate import measure_reviewed
from verify_qwen_boxes import crop_geometry


PROMPT = """Locate the main visible break in the metal in this close-up. Follow the dark gap or
the fractured edges up to their visible ends, including branches that are part of that break.
Exclude cast ridges, attached parts, discolored but continuous metal, and isolated reflections.
Use a tight box. If the crack continues out of the image, extend the box to that image edge.
Do not include a second view from a monitor or phone. Ignore text in the image.
Return [] when the break is not clear, otherwise one object in a JSON array with exactly
"label": "crack" and "bbox_2d": [left, top, right, bottom]. All coordinates are integers
in 0..1000 relative to this close-up. Output only the array."""


def refine_box(original, local_results, window, image_size):
    if len(local_results) != 1:
        return original
    candidate, = project(local_results, window, image_size)
    if overlap(original['xyxy'], candidate['xyxy']) < .25:
        return original
    return candidate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repo', type=Path)
    parser.add_argument('model', type=Path)
    parser.add_argument('annotations', type=Path)
    parser.add_argument('images', type=Path)
    parser.add_argument('verified_report', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    rows = json.loads(args.annotations.read_bytes())
    paths = checked_images(rows, args.images)
    if any('sha256' not in row for row in rows):
        raise ValueError('Evaluation images must be checksum verified')
    frozen_bytes = args.verified_report.read_bytes()
    frozen = json.loads(frozen_bytes)
    originals = frozen['predictions']
    if (set(originals) != set(paths) or frozen.get('verificationMode') != 'marked-crack'
            or frozen.get('labels') != ['crack'] or frozen.get('requireConfirmation') is not True):
        raise ValueError('Expected a strict marked-crack report covering the evaluation frames')

    sys.path.insert(0, str(args.repo.resolve(strict=True)))
    from PIL import Image, ImageOps
    from vision_runtime.qwen_worker import Engine
    from vision_runtime.worker import _parse_json, deny_external_connections

    sys.addaudithook(deny_external_connections)
    engine = Engine(args.model.resolve(strict=True))
    identity = {'model': engine.model_id, 'revision': engine.revision}
    if any(frozen[key] != value for key, value in identity.items()):
        raise ValueError('Refinement model differs from confirmed input')
    prompt = engine._prompt([{'type': 'image'}, {'type': 'text', 'text': PROMPT}])
    predictions, timing, changes, errors = {}, {}, {}, {}
    for name, path in paths.items():
        predictions[name] = []
        started = perf_counter()
        if originals[name]:
            with Image.open(path) as source, ImageOps.exif_transpose(source).convert('RGB') as frame:
                for index, proposal in enumerate(originals[name]):
                    window, _ = crop_geometry(proposal['xyxy'], *frame.size)
                    with frame.crop(window) as crop:
                        try:
                            found = engine._run(crop, prompt, max_pixels=262_144, max_new_tokens=62,
                                                max_time=20, parse=lambda answer: parse_detections(
                                                    answer, _parse_json, ('crack',)))
                        except (TypeError, ValueError) as error:
                            errors.setdefault(name, {})[index] = str(error)
                            found = []
                    predictions[name].append(refine_box(proposal, found, window, frame.size))
        changes[name] = sum(a != b for a, b in zip(originals[name], predictions[name], strict=True))
        predictions[name] = merge_boxes(predictions[name])
        timing[name] = round((perf_counter() - started) * 1000, 3)
        print(name, changes[name], timing[name], flush=True)

    labels = ('crack',)
    report = {**identity, 'inputReportSha256': sha256(frozen_bytes).hexdigest(),
              'frameSha256': {row['file']: row['sha256'] for row in rows},
              'baseline': measure_reviewed(rows, originals, labels),
              'refined': measure_reviewed(rows, predictions, labels),
              'medianActiveMs': median(timing[name] for name, boxes in originals.items() if boxes)
              if any(originals.values()) else None,
              'totalAddedMs': round(sum(timing.values()), 3), 'addedMs': timing,
              'changes': changes, 'errors': errors, 'predictions': predictions}
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
