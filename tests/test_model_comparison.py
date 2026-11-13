import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

from vision_runtime.compare_models import (
    GPUPoller, RecordingProcessor, digest, load_dataset, make_plan, measure, run, summarize,
)
from vision_runtime import worker
from vision_runtime import compare_models


RESULT = {'component': 'pump', 'condition': 'uncertain', 'box': [1, 2, 900, 950],
          'observation': 'Узел виден, состояние неясно.'}


class Tensor:
    def __init__(self, shape, values=None):
        self.shape, self.values = shape, values

    def tolist(self):
        return self.values


class Processor:
    def __call__(self, *args, **kwargs):
        return {'input_ids': Tensor((1, 900)), 'pixel_values': Tensor((4, 3, 448, 448)),
                'image_grid_thw': Tensor((1, 3), [[1, 32, 32]])}

    def decode(self, *args, **kwargs):
        return 'malformed output retained for review'


class CUDA:
    def synchronize(self):
        pass

    def reset_peak_memory_stats(self):
        pass

    def memory_allocated(self):
        return 100

    def memory_reserved(self):
        return 200

    def max_memory_allocated(self):
        return 300

    def max_memory_reserved(self):
        return 400


class Engine:
    def __init__(self, directory=None):
        self.processor = Processor()
        self.torch = SimpleNamespace(cuda=CUDA())
        self.last_metrics, self.calls, self.device = None, 0, 'fake GPU'

    def infer(self, image, profile):
        assert profile == 'standard'
        self.calls += 1
        self.processor(text='same prompt', images=image, max_patches=4)
        self.processor.decode(Tensor((50,)), skip_special_tokens=True)
        if self.calls == 2:
            raise ValueError('bad JSON')
        self.last_metrics = {'inputTokens': 900, 'outputTokens': 50, 'patches': 4}
        return dict(RESULT)


class ComparisonTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def dataset(self, count=4):
        directory = self.root / 'dataset'
        (directory / 'previews').mkdir(parents=True)
        entries = []
        for index in range(count):
            identifier = f'{index + 1:064x}'
            path = directory / 'previews' / f'{identifier}.jpg'
            with Image.new('RGB', (16 + index, 12), (index * 20, 30, 40)) as image:
                image.save(path)
            content = path.read_bytes()
            entries.append({'id': identifier, 'previewSha256': digest(content), 'previewBytes': len(content),
                            'width': 16 + index, 'height': 12, 'category': 'pump' if index % 2 else 'motor',
                            'inspectionScope': {'id': 'external' if index % 2 else 'repair'}})
        manifest = directory / 'materials.json'
        manifest.write_text(json.dumps({'images': entries}), encoding='utf-8')
        return manifest, entries

    def test_preview_integrity_and_exact_dataset_size_are_required(self):
        manifest, entries = self.dataset()
        data = load_dataset(manifest, 4)
        self.assertEqual(data['count'], 4)
        self.assertEqual(data['manifestSha256'], digest(manifest.read_bytes()))
        self.assertEqual(len(data['images'][0]['rgbSha256']), 64)
        with self.assertRaisesRegex(ValueError, 'exactly 53'):
            load_dataset(manifest)
        path = manifest.parent / 'previews' / f"{entries[0]['id']}.jpg"
        path.write_bytes(path.read_bytes() + b'changed')
        with self.assertRaisesRegex(ValueError, 'integrity mismatch'):
            load_dataset(manifest, 4)

    def test_plan_is_model_independent_balanced_and_repeats_fixed_panel(self):
        records = [{'imageId': str(index), 'category': str(index % 4)} for index in range(53)]
        first = make_plan(records)
        second = make_plan(list(reversed(records)))
        self.assertEqual(first, second)
        self.assertEqual(len(first['requests']), 77)
        categories = {row['imageId']: row['category'] for row in records}
        self.assertEqual({categories[value] for value in first['primaryOrder'][:4]}, {'0', '1', '2', '3'})
        for round_index in (1, 2, 3):
            self.assertEqual([row['imageId'] for row in first['requests'] if row['round'] == round_index],
                             first['repeatIds'])
        with self.assertRaisesRegex(ValueError, 'distinct IDs'):
            make_plan(records, repeat_ids=['0', '0'])
        with self.assertRaisesRegex(ValueError, 'distinct IDs'):
            make_plan(records, repeat_ids=['not-present'])

    def test_failure_retains_raw_output_shapes_time_and_peak_memory(self):
        engine = Engine()
        engine.calls = 1
        engine.processor = RecordingProcessor(engine.processor)
        engine.last_metrics = {'stale': True}
        poller = GPUPoller(io.StringIO(), enabled=False)
        with Image.new('RGB', (16, 12)) as image:
            row = measure(engine, image, {'phase': 'primary', 'round': 0, 'imageId': 'one'}, poller)
        self.assertFalse(row['success'])
        self.assertEqual(row['errorType'], 'ValueError')
        self.assertIsNone(row['metrics'])
        self.assertEqual(row['nativeInput']['rawOutput'], 'malformed output retained for review')
        self.assertEqual(row['nativeInput']['input_idsShape'], [1, 900])
        self.assertEqual(row['nativeInput']['imageGridTHW'], [[1, 32, 32]])
        self.assertEqual(row['torchMemory']['peakAllocatedBytes'], 300)
        self.assertGreaterEqual(row['seconds'], 0)

    def test_summary_includes_failures_and_does_not_call_agreement_accuracy(self):
        rows = [dict(phase='primary', round=0, imageId='one', success=True, result=RESULT,
                     inspectionScope='external', seconds=1),
                dict(phase='primary', round=0, imageId='two', success=False, seconds=9),
                dict(phase='repeat', round=1, imageId='one', success=True, result=RESULT, seconds=2),
                dict(phase='repeat', round=2, imageId='one', success=False, seconds=8)]
        report = summarize(rows, ['one'])
        self.assertEqual(report['primary']['latencyAll']['p50Seconds'], 5)
        self.assertEqual(report['primary']['latencyValidOnly']['p50Seconds'], 1)
        self.assertEqual(report['externalPrimary']['requests'], 1)
        agreement = report['repeatAgreement'][0]
        self.assertEqual((agreement['exactMatchingPairs'], agreement['totalPairsIncludingFailures']), (1, 3))
        self.assertFalse(agreement['allValidAndExactMatch'])
        self.assertFalse(agreement['allValidAndSameLabels'])

    def test_nvidia_smi_tracks_global_and_process_memory_separately(self):
        poller = GPUPoller(io.StringIO(), enabled=False)
        with patch.object(poller, 'query', side_effect=['GPU-id, GPU name, 6000, 24000, 70, 50',
                                                       f'123, 500\n{os.getpid()}, 4200']):
            poller.sample()
        self.assertEqual(poller.samples[0]['deviceUsedMiB'], 6000)
        self.assertEqual(poller.samples[0]['processUsedMiB'], 4200)
        with patch.object(poller, 'query', side_effect=['GPU-id, GPU name, 6000, 24000, 70, 50',
                                                       f'{os.getpid()}, [N/A]']):
            poller.sample()
        self.assertIsNone(poller.samples[-1]['processUsedMiB'])

    def test_complete_run_persists_failed_rows_and_does_not_overwrite_output(self):
        manifest, _ = self.dataset(2)
        args = argparse.Namespace(dataset=manifest, expected_count=2, seed=42, repeat_ids=None,
                                  repeat_rounds=1, output=self.root / 'report', engine='internvl',
                                  gpu_index='0', no_nvidia_smi=True, model_directory=self.root / 'model')
        module = SimpleNamespace(Engine=Engine, SYSTEM_PROMPT=worker.SYSTEM_PROMPT, PROMPT=worker.PROMPT,
                                 MODEL=worker.MODEL, REVISION=worker.REVISION, __file__=worker.__file__,
                                 PROFILES=worker.PROFILES)
        with patch.object(compare_models, 'importlib', SimpleNamespace(import_module=lambda name: module)), \
                patch('sys.addaudithook'), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run(args), 2)
        report = json.loads((args.output / 'report.json').read_text())
        persisted = [json.loads(line) for line in (args.output / 'records.jsonl').read_text().splitlines()]
        self.assertTrue(report['completed'])
        self.assertEqual(report['records'], persisted)
        self.assertEqual(len(persisted), 4)
        self.assertEqual(report['summary']['primary']['failures'], 1)
        self.assertEqual(report['warmup']['phase'], 'warmup')
        with self.assertRaises(FileExistsError):
            run(args)


if __name__ == '__main__':
    unittest.main()
