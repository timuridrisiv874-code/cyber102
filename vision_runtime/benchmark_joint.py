"""Isolated, frozen overview/crop/joint comparison; never edits serving files.

The supervisor must stop the existing GPU worker before invoking this script,
and restore it in a finally block. --check verifies inputs without loading CUDA.
"""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import importlib.util
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import time


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def prepare_image(image, crop):
    """Match the previous benchmark's JPEG upload and real web normalization."""
    from server.vision import extract_request
    from PIL import Image
    first, _ = crop.normalized_jpeg(image)
    body, headers = crop.multipart(first, boundary='joint-frozen-upload')
    raw, profile = extract_request(headers['Content-Type'], body)
    assert profile == 'standard'
    with Image.open(BytesIO(raw)) as encoded:
        decoded = encoded.convert('RGB')
    return decoded, raw


def measure(engine, call):
    from server.vision import result_from_observation
    started = time.perf_counter()
    row = {'success': False}
    try:
        observation = call()
        result = result_from_observation(observation, model=engine.model_id)
        row.update(success=True, observation=observation, result=result)
    except (ValueError, KeyError, TypeError) as error:
        # Invalid outputs count as failures, never retry them for quality.
        row['error'] = {'type': type(error).__name__, 'message': str(error)[:300]}
    row.update(seconds=time.perf_counter()-started, rawOutput=engine.last_output,
               metrics=engine.last_metrics, memory=engine.last_memory_metrics,
               inputMetadata=engine.last_input_metadata)
    return row


def normalized_crop_box(crop, size):
    x0, y0, x1, y1 = crop['pixels']
    width, height = size
    return [1000*x0/width, 1000*y0/height, 1000*x1/width, 1000*y1/height]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('root', 'plan', 'output', 'candidate', 'helper'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    sys.path.insert(0, str(args.root))
    from vision_runtime import worker
    from server.vision_models import model_spec
    plan_bytes = args.plan.read_bytes()
    plan = json.loads(plan_bytes)
    for name, expected in plan['productionSourceSha256'].items():
        if sha((args.root/name).read_bytes()) != expected:
            raise ValueError('Serving source changed: ' + name)
    for name, path in [('script', Path(__file__)), ('candidate', args.candidate), ('helper', args.helper)]:
        if sha(path.read_bytes()) != plan['experimentSourceSha256'][name]:
            raise ValueError('Experiment source changed: ' + name)
    candidate = load_module('vision_runtime.joint_candidate', args.candidate)
    crop = load_module('vision_runtime.joint_crop_helper', args.helper)
    spec = model_spec('qwen3_vl_4b')
    if (plan['model'], plan['revision']) != (spec.model_id, spec.revision):
        raise ValueError('Wrong pinned model')
    if (candidate.MODEL, candidate.REVISION) != (spec.model_id, spec.revision):
        raise ValueError('Wrong candidate model')
    loading_mode = plan.get('loadingMode', 'cpu_then_cuda')
    if loading_mode not in ('cpu_then_cuda', 'direct_cuda'):
        raise ValueError('Unsupported frozen loading mode')
    if loading_mode == 'direct_cuda' and importlib.util.find_spec('accelerate') is None:
        raise ValueError('Direct CUDA loading requires the installed Accelerate package')
    if [r['index'] for r in plan['images']] != crop.PANEL or plan['cropSettings'] != crop.SETTINGS:
        raise ValueError('Frozen panel or crop settings changed')
    if args.output.exists():
        raise ValueError('Output exists; refusing a rerun/overwrite')
    source_root = args.root/'data/customer/raw'
    for item in plan['images']:
        with crop.open_original(crop.source_file(source_root, item['sourcePath']), item['imageId']) as image:
            if list(image.size) != item['originalSize']:
                raise ValueError('Original dimensions changed')
    if args.check:
        print(json.dumps({'preflight': 'ok', 'images': 12, 'planSha256': sha(plan_bytes)}), flush=True)
        return 0

    args.output.mkdir(parents=True)
    (args.output/'inputs').mkdir()
    (args.output/'protocol.json').write_bytes(plan_bytes)
    report = {'completed': False, 'model': spec.model_id, 'revision': spec.revision,
              'planSha256': sha(plan_bytes), 'startedAt': datetime.now(timezone.utc).isoformat(),
              'records': [], 'warmups': [], 'protocol': plan}
    try:
        for key in ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'HF_HUB_DISABLE_TELEMETRY', 'DO_NOT_TRACK'):
            os.environ[key] = '1'
        os.environ['TOKENIZERS_PARALLELISM'] = 'false'
        sys.addaudithook(worker.deny_external_connections)
        import torch
        import PIL
        from PIL import Image
        import transformers
        report['versions'] = {'torch': torch.__version__, 'pillow': PIL.__version__, 'transformers': transformers.__version__}
        torch.manual_seed(42)
        started = time.perf_counter()
        engine = candidate.Engine(args.root/'models'/spec.directory,
                                  direct_gpu_load=loading_mode == 'direct_cuda')
        report['loadingMode'] = loading_mode
        report['modelLoadSeconds'] = time.perf_counter()-started
        report['device'] = engine.device
        with Image.new('RGB', (640, 480), 'white') as white, Image.new('RGB', (320, 240), 'white') as detail:
            for name, call in [('A', lambda: engine.infer(white)), ('B', lambda: engine.infer(detail)),
                               ('C', lambda: engine.infer_detail(white, detail, [250,250,750,750]))]:
                report['warmups'].append({'variant': name, **measure(engine, call)})
        eligible_index = 0
        for item in plan['images']:
            row = {'index': item['index'], 'imageId': item['imageId'], 'originalSize': item['originalSize']}
            # Keep the successful first pass even if a later CUDA call fails.
            report['records'].append(row)
            with ExitStack() as stack:
                started = time.perf_counter()
                original = stack.enter_context(crop.open_original(crop.source_file(source_root, item['sourcePath']), item['imageId']))
                overview, raw = prepare_image(original, crop)
                stack.enter_context(overview)
                row['firstPreparationSeconds'] = time.perf_counter()-started
                row['overviewInput'] = {'path': f"inputs/{item['index']:02d}-overview.jpg", 'sha256': sha(raw), 'size': list(overview.size)}
                (args.output/row['overviewInput']['path']).write_bytes(raw)
                row['A'] = measure(engine, lambda: engine.infer(overview))
                row['singleServiceSeconds'] = row['firstPreparationSeconds'] + row['A']['seconds']
                started = time.perf_counter()
                roi = crop.crop_plan(row['A'].get('result', {}), original.size)
                row['cropPlanningSeconds'] = time.perf_counter()-started
                row['crop'] = roi
                if roi['eligible']:
                    row['cropBoxOverview1000'] = normalized_crop_box(roi, original.size)
                    row['order'] = ['B', 'C'] if eligible_index % 2 == 0 else ['C', 'B']
                    eligible_index += 1
                    for variant in row['order']:
                        started = time.perf_counter()
                        with original.crop(tuple(roi['pixels'])) as region:
                            detail, detail_raw = prepare_image(region, crop)
                        prep_seconds = time.perf_counter()-started
                        detail_meta = {'path': f"inputs/{item['index']:02d}-crop.jpg", 'sha256': sha(detail_raw), 'size': list(detail.size)}
                        if 'cropInput' in row and row['cropInput'] != detail_meta:
                            raise ValueError('B/C crop bytes differ')
                        row['cropInput'] = detail_meta
                        (args.output/detail_meta['path']).write_bytes(detail_raw)
                        with detail:
                            call = (lambda: engine.infer(detail)) if variant == 'B' else (
                                lambda: engine.infer_detail(overview, detail, row['cropBoxOverview1000']))
                            row[variant] = measure(engine, call)
                        row[variant]['cropPreparationSeconds'] = prep_seconds
                        row[variant]['fullServiceSeconds'] = (row['singleServiceSeconds'] + row['cropPlanningSeconds']
                            + prep_seconds + row[variant]['seconds'])
                        if variant == 'B' and row[variant]['success']:
                            row[variant]['boxOnOverview'] = crop.map_crop_box(row[variant]['result'].get('box'), roi, original.size)
                        elif variant == 'C':
                            # Never repair crop-relative coordinates or clamp invalid outputs.
                            row[variant]['boxOnOverview'] = row[variant].get('result', {}).get('box')
                else:
                    row['order'] = []
                write_json(args.output/'report.json', report)
                print(json.dumps({'processed': len(report['records']), 'index': item['index'],
                                  'eligible': roi['eligible'], 'valid': {k: row[k]['success'] for k in ('A','B','C') if k in row}}), flush=True)
        report['completed'] = True
    except BaseException as error:
        report['fatalError'] = {'type': type(error).__name__, 'message': str(error)[:500]}
        raise
    finally:
        report['finishedAt'] = datetime.now(timezone.utc).isoformat()
        write_json(args.output/'report.json', report)
    return 0 if all(row[k]['success'] for row in report['records'] for k in ('A','B','C') if k in row) else 2


if __name__ == '__main__':
    raise SystemExit(main())
