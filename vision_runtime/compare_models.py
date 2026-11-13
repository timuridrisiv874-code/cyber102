"""Run one isolated model over identical verified previews; this does not measure accuracy.

Run each engine in a fresh process on an otherwise idle GPU. The caller owns stopping
and restarting services. Outputs contain customer images' model responses; keep local.
Exit codes: 0 complete/valid, 2 complete with inference failures, 1 setup/fatal, 130 interrupted.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import importlib
from importlib.metadata import PackageNotFoundError, version
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time


ROOT = Path(__file__).resolve().parents[1]
HEX = re.compile(r'^[0-9a-f]{64}$')
RAW_LIMIT = 16000


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_preview(path):
    from PIL import Image, ImageOps
    with Image.open(path) as original:
        if original.format != 'JPEG' or original.width * original.height > 8_000_000:
            raise ValueError('Expected a bounded normalized JPEG preview')
        return ImageOps.exif_transpose(original).convert('RGB')


def load_dataset(path, expected_count=53):
    path = path / 'materials.json' if path.is_dir() else path
    raw = path.read_bytes()
    catalog = json.loads(raw)
    entries = catalog.get('images')
    if not isinstance(entries, list) or not entries or len(entries) != expected_count:
        raise ValueError(f'Expected exactly {expected_count} organizer images')
    records, seen = [], set()
    for entry in entries:
        identifier = entry.get('id')
        if not isinstance(identifier, str) or not HEX.fullmatch(identifier) or identifier in seen:
            raise ValueError('Image IDs must be unique SHA-256 identifiers')
        seen.add(identifier)
        preview = path.parent / 'previews' / f'{identifier}.jpg'
        content = preview.read_bytes()
        if (digest(content) != entry.get('previewSha256') or len(content) != entry.get('previewBytes')
                or not 0 < len(content) <= 3 * 1024 * 1024):
            raise ValueError(f'Preview integrity mismatch: {identifier}')
        with read_preview(preview) as image:
            if image.size != (entry.get('width'), entry.get('height')) or max(image.size) > 1280:
                raise ValueError(f'Preview dimensions mismatch: {identifier}')
            records.append({'imageId': identifier, 'previewPath': str(preview.resolve()),
                            'previewSha256': digest(content), 'previewBytes': len(content),
                            'rgbSha256': digest(image.tobytes()), 'width': image.width, 'height': image.height,
                            'category': entry.get('category', 'unknown'),
                            'inspectionScope': entry.get('inspectionScope', {}).get('id', 'unreviewed'),
                            'sourcePath': entry.get('sourcePath'), 'expertLabel': None})
    return {'manifestSha256': digest(raw), 'images': records, 'count': len(records)}


def make_plan(records, seed=42, repeat_ids=None, repeat_rounds=3):
    """Category-balanced order, independent of model outputs and model identity."""
    generator = random.Random(seed)
    groups = defaultdict(list)
    for record in sorted(records, key=lambda item: item['imageId']):
        groups[record['category']].append(record['imageId'])
    for group in groups.values():
        generator.shuffle(group)
    order = []
    while any(groups.values()):
        for category in sorted(groups):
            if groups[category]:
                order.append(groups[category].pop())
    panel = list(repeat_ids) if repeat_ids is not None else order[:min(8, len(order))]
    if not panel or len(panel) != len(set(panel)) or set(panel) - set(order):
        raise ValueError('Repeat IDs must be distinct IDs present in the dataset')
    if not 0 <= repeat_rounds <= 10:
        raise ValueError('Repeat rounds must be between 0 and 10')
    plan = [{'phase': 'primary', 'round': 0, 'imageId': identifier} for identifier in order]
    for round_index in range(1, repeat_rounds + 1):
        plan.extend({'phase': 'repeat', 'round': round_index, 'imageId': identifier} for identifier in panel)
    return {'seed': seed, 'primaryOrder': order, 'repeatIds': panel, 'repeatRounds': repeat_rounds,
            'requests': plan, 'planSha256': digest(json.dumps(plan, sort_keys=True).encode())}


class RecordingProcessor:
    """Observe native processor IO without changing images, prompts, tensors or decoding."""
    def __init__(self, processor):
        self.processor, self.capture = processor, {}

    def __getattr__(self, name):
        return getattr(self.processor, name)

    def __call__(self, *args, **kwargs):
        inputs = self.processor(*args, **kwargs)
        for name in ('input_ids', 'pixel_values', 'image_grid_thw'):
            if name in inputs:
                self.capture[name + 'Shape'] = list(inputs[name].shape)
        if 'image_grid_thw' in inputs:
            self.capture['imageGridTHW'] = inputs['image_grid_thw'].tolist()
        self.capture['processorOptions'] = {
            name: kwargs[name] for name in ('crop_to_patches', 'max_patches', 'min_pixels', 'max_pixels')
            if name in kwargs}
        return inputs

    def decode(self, tokens, *args, **kwargs):
        self.capture['outputTokens'] = int(tokens.shape[0])
        text = self.processor.decode(tokens, *args, **kwargs)
        self.capture['rawOutput'] = text[:RAW_LIMIT]
        self.capture['rawOutputTruncated'] = len(text) > RAW_LIMIT
        return text

    def batch_decode(self, tokens, *args, **kwargs):
        texts = self.processor.batch_decode(tokens, *args, **kwargs)
        self.capture['outputTokens'] = int(tokens[0].shape[0])
        self.capture['rawOutput'] = texts[0][:RAW_LIMIT]
        self.capture['rawOutputTruncated'] = len(texts[0]) > RAW_LIMIT
        return texts


def torch_memory(torch):
    cuda = torch.cuda
    return {'allocatedBytes': int(cuda.memory_allocated()), 'reservedBytes': int(cuda.memory_reserved()),
            'peakAllocatedBytes': int(cuda.max_memory_allocated()),
            'peakReservedBytes': int(cuda.max_memory_reserved())}


class GPUPoller:
    """Optional physical-device and process samples, distinct from PyTorch allocations."""
    def __init__(self, output, gpu='0', enabled=True):
        self.output, self.gpu = output, gpu
        self.executable = shutil.which('nvidia-smi') if enabled else None
        self.samples, self.errors = [], []
        self.stopping = threading.Event()
        self.thread = None

    def query(self, fields, kind='gpu'):
        command = [self.executable, f'--query-{kind}={fields}', '--format=csv,noheader,nounits', '-i', self.gpu]
        return subprocess.run(command, capture_output=True, text=True, check=True, timeout=3).stdout.strip()

    def sample(self):
        try:
            raw = self.query('uuid,name,memory.used,memory.total,utilization.gpu,temperature.gpu')
            row = [part.strip() for part in raw.split(',')]
            if len(row) != 6:
                raise ValueError('Expected one selected physical GPU')
            numeric = lambda value: float(value) if re.fullmatch(r'\d+(\.\d+)?', value) else None
            data = {'monotonicSeconds': time.monotonic(), 'uuid': row[0], 'name': row[1],
                    'deviceUsedMiB': numeric(row[2]), 'deviceTotalMiB': numeric(row[3]),
                    'utilizationPercent': numeric(row[4]), 'temperatureC': numeric(row[5]),
                    'processUsedMiB': None}
            try:
                for process in self.query('pid,used_memory', 'compute-apps').splitlines():
                    pid, memory = [value.strip() for value in process.split(',')]
                    if pid == str(os.getpid()):
                        data['processUsedMiB'] = numeric(memory)
            except (OSError, subprocess.SubprocessError, ValueError):
                pass  # WDDM and some drivers cannot report per-process GPU memory.
            self.samples.append(data)
            self.output.write(json.dumps(data) + '\n')
            self.output.flush()
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            if len(self.errors) < 8:
                self.errors.append(f'{type(error).__name__}: {error}'[:500])

    def start(self):
        if self.executable:
            self.sample()
            self.thread = threading.Thread(target=self.loop, daemon=True)
            self.thread.start()

    def loop(self):
        while not self.stopping.wait(0.2):
            self.sample()

    def stop(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=7)

    def window(self, start, end):
        rows = [row for row in self.samples if start <= row['monotonicSeconds'] <= end]
        def maximum(name):
            values = [row[name] for row in rows if row[name] is not None]
            return max(values) if values else None
        return {'samples': len(rows), 'peakDeviceUsedMiB': maximum('deviceUsedMiB'),
                'peakProcessUsedMiB': maximum('processUsedMiB')}


def measure(engine, image, request, poller):
    engine.processor.capture = {}
    engine.last_metrics = None
    engine.torch.cuda.synchronize()
    engine.torch.cuda.reset_peak_memory_stats()
    started = time.monotonic()
    record = dict(request)
    try:
        record['result'] = engine.infer(image, profile='standard')
        engine.torch.cuda.synchronize()
        record['success'] = True
    except Exception as error:
        record.update(success=False, errorType=type(error).__name__, error=str(error)[:1200])
        try:
            engine.torch.cuda.synchronize()
        except Exception:
            pass
    ended = time.monotonic()
    record.update(seconds=round(ended - started, 6), metrics=engine.last_metrics,
                  nativeInput=dict(engine.processor.capture), torchMemory=torch_memory(engine.torch),
                  sampledGPU=poller.window(started, ended))
    if record.get('errorType') == 'OutOfMemoryError':
        engine.torch.cuda.empty_cache()
        record['cacheClearedAfterOOM'] = True
    return record


def percentile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    index = (len(values) - 1) * fraction
    low, high = math.floor(index), math.ceil(index)
    return values[low] + (values[high] - values[low]) * (index - low)


def summarize(records, repeat_ids):
    primary = [row for row in records if row['phase'] == 'primary']
    def timing(rows):
        values = [row['seconds'] for row in rows]
        return {'count': len(values), 'p50Seconds': statistics.median(values) if values else None,
                'p95Seconds': percentile(values, 0.95), 'totalSeconds': sum(values)}
    def subset(rows):
        valid = [row for row in rows if row['success']]
        return {'requests': len(rows), 'valid': len(valid), 'failures': len(rows) - len(valid),
                'latencyAll': timing(rows), 'latencyValidOnly': timing(valid),
                'conditions': dict(Counter(row['result']['condition'] for row in valid))}
    agreement = []
    for identifier in repeat_ids:
        rows = [row for row in records if row['imageId'] == identifier]
        valid = [row['result'] for row in rows if row['success']]
        pairs = len(rows) * (len(rows) - 1) // 2
        matching = sum(first == second for i, first in enumerate(valid) for second in valid[i + 1:])
        agreement.append({'imageId': identifier, 'runs': len(rows), 'valid': len(valid),
                          'exactMatchingPairs': matching, 'totalPairsIncludingFailures': pairs,
                          'allValidAndExactMatch': bool(rows) and len(valid) == len(rows)
                          and len({json.dumps(row, sort_keys=True) for row in valid}) == 1,
                          'allValidAndSameLabels': bool(rows) and len(valid) == len(rows)
                          and len({(row['component'], row['condition']) for row in valid}) == 1})
    return {'primary': subset(primary),
            'externalPrimary': subset([row for row in primary if row.get('inspectionScope') == 'external']),
            'repeats': subset([row for row in records if row['phase'] == 'repeat']),
            'repeatAgreement': agreement,
            'interpretation': 'Agreement is repeatability, valid JSON is format compliance; neither measures correctness.'}


def package_versions():
    result = {}
    for package in ('torch', 'transformers', 'Pillow', 'accelerate'):
        try:
            result[package] = version(package)
        except PackageNotFoundError:
            result[package] = None
    return result


def run(args):
    dataset = load_dataset(args.dataset, args.expected_count)
    plan = make_plan(dataset['images'], args.seed, args.repeat_ids, args.repeat_rounds)
    args.output.mkdir(parents=True, exist_ok=False)
    report = {'schemaVersion': 1, 'startedAt': datetime.now(timezone.utc).isoformat(),
              'engine': args.engine, 'completed': False, 'dataset': dataset, 'plan': plan,
              'pid': os.getpid(), 'versions': package_versions(), 'records': [],
              'scope': 'Matched offline functional and latency experiment on unlabelled organizer photographs. '
                       'No accuracy or operational-safety claim. No automatic winner.',
              'protocol': {'profile': 'standard', 'precision': 'bfloat16', 'attention': 'sdpa',
                           'doSample': False, 'maxNewTokens': 180, 'maxTimeSeconds': 85,
                           'warmupRequests': 1, 'GPUIsolation': 'caller must stop competing GPU workloads',
                           'nativeBudgetNote': 'InternVL tiles and Qwen grid cells are not equivalent units; '
                           'approximately matched pixel budgets, actual input shapes and tokens recorded.',
                           'cudaVisibleDevices': os.environ.get('CUDA_VISIBLE_DEVICES'),
                           'nvidiaSmiIndex': args.gpu_index}}
    (args.output / 'plan.json').write_text(json.dumps(plan, indent=2) + '\n', encoding='utf-8')
    poller = None
    try:
        for key in ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'HF_HUB_DISABLE_TELEMETRY', 'DO_NOT_TRACK'):
            os.environ[key] = '1'
        os.environ['TOKENIZERS_PARALLELISM'] = 'false'
        module = importlib.import_module('vision_runtime.worker' if args.engine == 'internvl' else 'vision_runtime.qwen_worker')
        from vision_runtime.worker import SYSTEM_PROMPT, PROMPT, deny_external_connections
        if module.SYSTEM_PROMPT != SYSTEM_PROMPT or module.PROMPT != PROMPT:
            raise ValueError('Model prompts do not match the shared comparison protocol')
        sys.addaudithook(deny_external_connections)
        report.update(model=module.MODEL, revision=module.REVISION,
                      systemPromptSha256=digest(SYSTEM_PROMPT.encode()), promptSha256=digest(PROMPT.encode()),
                      workerSha256=digest(Path(module.__file__).read_bytes()))
        report['nativeProcessorBudget'] = (
            {'cropToPatches': True, 'maxPatchesExcludingThumbnail': module.PROFILES['standard'],
             'patchUnit': '448x448 InternVL image tile, optional thumbnail counted in actual input'}
            if args.engine == 'internvl' else
            {'minPixels': module.MIN_PIXELS, 'maxPixels': module.PHOTO_MAX_PIXELS,
             'patchUnit': 'native Qwen vision grid cells; see imageGridTHW and pixel_valuesShape'})
        with (args.output / 'gpu-memory.jsonl').open('w', encoding='utf-8') as gpu_log, \
                (args.output / 'records.jsonl').open('w', encoding='utf-8') as records_log:
            poller = GPUPoller(gpu_log, args.gpu_index, not args.no_nvidia_smi)
            poller.start()
            try:
                started = time.monotonic()
                engine = module.Engine(args.model_directory)
                engine.torch.cuda.synchronize()
                report['modelLoadSeconds'] = time.monotonic() - started
                report['device'] = engine.device
                report['memoryAfterLoad'] = torch_memory(engine.torch)
                report['modelLoadSampledGPU'] = poller.window(started, time.monotonic())
                engine.processor = RecordingProcessor(engine.processor)
                from PIL import Image
                with Image.new('RGB', (640, 480), 'white') as image:
                    report['warmup'] = measure(engine, image, {'phase': 'warmup', 'round': 0,
                                                            'imageId': 'synthetic-white-640x480'}, poller)
                report['memoryAfterWarmup'] = torch_memory(engine.torch)
                by_id = {record['imageId']: record for record in dataset['images']}
                for request in plan['requests']:
                    source = by_id[request['imageId']]
                    with read_preview(source['previewPath']) as image:
                        if digest(image.tobytes()) != source['rgbSha256']:
                            raise ValueError('Preview changed after preflight')
                        record = measure(engine, image, {**request, 'inspectionScope': source['inspectionScope']}, poller)
                    report['records'].append(record)
                    records_log.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n')
                    records_log.flush()
                    print(json.dumps({'processed': len(report['records']), 'total': len(plan['requests']),
                                      'imageId': record['imageId'], 'success': record['success'],
                                      'seconds': record['seconds']}), flush=True)
                report['completed'] = True
            finally:
                poller.stop()
    except BaseException as error:
        report['fatalError'] = {'type': type(error).__name__, 'message': str(error)[:1200]}
        raise
    finally:
        report['finishedAt'] = datetime.now(timezone.utc).isoformat()
        report['summary'] = summarize(report['records'], plan['repeatIds'])
        report['nvidiaSmi'] = {'available': bool(poller and poller.executable),
                               'samplingErrors': poller.errors if poller else [],
                               'baseline': poller.samples[0] if poller and poller.samples else None,
                               'note': 'Sampled peaks can miss brief spikes; process memory may be unavailable on WDDM.'}
        (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return 2 if any(not row['success'] for row in report['records']) else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', choices=('internvl', 'qwen'), required=True)
    parser.add_argument('--model-directory', type=Path, required=True)
    parser.add_argument('--dataset', type=Path, default=ROOT / 'data/customer/materials.json')
    parser.add_argument('--expected-count', type=int, default=53)
    parser.add_argument('--output', type=Path, required=True, help='new local report directory')
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--repeat-ids', nargs='+', help='fixed shared panel; defaults to first two category-balanced cycles')
    parser.add_argument('--repeat-rounds', type=int, default=3, help='additional panel passes after the primary pass')
    parser.add_argument('--gpu-index', default='0', help='physical nvidia-smi index, matching the engine CUDA device')
    parser.add_argument('--no-nvidia-smi', action='store_true')
    args = parser.parse_args(argv)
    try:
        return run(args)
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        print(f'{type(error).__name__}: {str(error)[:1200]}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
