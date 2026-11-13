from contextlib import nullcontext
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

from PIL import Image
from vision_runtime import qwen_worker as qwen, worker


OBSERVATION = {'component': 'valve', 'condition': 'corrosion', 'box': [120, 118, 810, 967],
               'observation': 'На корпусе видны следы коррозии.'}


class JointLoadingTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        (self.directory/'cyber102-manifest.json').write_text(json.dumps({
            'repository': qwen.MODEL, 'revision': qwen.REVISION}))
        self.torch, self.transformers = Mock(), Mock()
        self.torch.cuda.is_available.return_value = True
        self.model = self.transformers.Qwen3VLForConditionalGeneration.from_pretrained.return_value
        self.model.eval.return_value = self.model
        self.modules = {'torch': self.torch, 'transformers': self.transformers}

    def test_default_preserves_cpu_load_then_cuda_transfer(self):
        with patch.dict('sys.modules', self.modules):
            engine = qwen.Engine(self.directory)
        self.transformers.Qwen3VLForConditionalGeneration.from_pretrained.assert_called_once_with(
            self.directory, local_files_only=True, trust_remote_code=False, use_safetensors=True,
            torch_dtype=self.torch.bfloat16, attn_implementation='sdpa')
        self.model.to.assert_called_once_with('cuda')
        self.assertIs(engine.model, self.model.to.return_value)

    def test_direct_load_is_explicit_single_gpu_without_second_transfer(self):
        self.model.parameters.return_value = [SimpleNamespace(device=SimpleNamespace(type='cuda', index=0))]
        with patch.dict('sys.modules', self.modules):
            engine = qwen.Engine(self.directory, direct_gpu_load=True)
        self.transformers.Qwen3VLForConditionalGeneration.from_pretrained.assert_called_once_with(
            self.directory, local_files_only=True, trust_remote_code=False, use_safetensors=True,
            torch_dtype=self.torch.bfloat16, attn_implementation='sdpa', device_map={'': 'cuda:0'})
        self.model.to.assert_not_called()
        self.assertIs(engine.model, self.model)

    def test_direct_load_rejects_offloaded_or_wrong_gpu_parameters(self):
        for device_type, device_index in [('cpu', None), ('meta', None), ('cuda', 1)]:
            self.model.parameters.return_value = [
                SimpleNamespace(device=SimpleNamespace(type='cuda', index=0)),
                SimpleNamespace(device=SimpleNamespace(type=device_type, index=device_index))]
            with self.subTest(device=device_type, index=device_index), patch.dict('sys.modules', self.modules):
                with self.assertRaisesRegex(RuntimeError, 'every parameter'):
                    qwen.Engine(self.directory, direct_gpu_load=True)
        self.model.to.assert_not_called()


class JointPhotoTests(unittest.TestCase):
    def setUp(self):
        self.overview = Image.new('RGB', (640, 480))
        self.crop = Image.new('RGB', (320, 240))
        self.addCleanup(self.overview.close)
        self.addCleanup(self.crop.close)

    def engine(self):
        engine = qwen.Engine.__new__(qwen.Engine)
        engine.processor = Mock()
        engine.processor.apply_chat_template.return_value = 'compiled detail prompt'
        engine._run = Mock(return_value=OBSERVATION)
        return engine

    def test_two_views_one_context_with_overview_coordinates_and_fixed_budget(self):
        engine = self.engine()
        result = engine.infer_detail(self.overview, self.crop, [100.123456789, 200, 900, 950])
        self.assertEqual(result, OBSERVATION)
        args, kwargs = engine._run.call_args
        self.assertEqual(args, ([self.overview, self.crop], 'compiled detail prompt'))
        self.assertEqual(kwargs['max_pixels'], 1_048_576)
        self.assertEqual((kwargs['max_new_tokens'], kwargs['max_time']), (180, 85))
        self.assertEqual(qwen.DETAIL_TOTAL_MAX_PIXELS, 2_097_152)
        self.assertIs(kwargs['parse'], worker.parse_observation)
        messages = engine.processor.apply_chat_template.call_args.args[0]
        self.assertEqual(messages[0], {'role': 'system', 'content': worker.SYSTEM_PROMPT})
        content = messages[1]['content']
        self.assertEqual([item['type'] for item in content], ['text', 'image', 'text', 'image', 'text'])
        text = content[-1]['text']
        self.assertIn('[100.123457, 200.0, 900.0, 950.0]', text)
        self.assertIn('Never use crop-relative coordinates', text)
        self.assertIn('same component', text)
        self.assertNotIn(OBSERVATION['observation'], text)

    def test_invalid_crop_bounds_never_reach_processor_or_generation(self):
        invalid = [None, {}, [1, 2, 3], [False, 0, 500, 500], [0, 0, float('nan'), 500],
                   [0, 0, float('inf'), 500], [-1, 0, 500, 500], [0, 0, 1001, 500],
                   [500, 0, 100, 500], [0, 500, 500, 500], ['0', 0, 500, 500],
                   [1, 0, 1.00000001, 500]]
        for box in invalid:
            engine = self.engine()
            with self.subTest(box=box), self.assertRaises(ValueError):
                engine.infer_detail(self.overview, self.crop, box)
            engine.processor.apply_chat_template.assert_not_called()
            engine._run.assert_not_called()

    def test_requires_rgb_and_applied_exif_orientation(self):
        engine = self.engine()
        with Image.new('L', (20, 20)) as grayscale:
            with self.assertRaises(ValueError):
                engine.infer_detail(self.overview, grayscale, [0, 0, 500, 500])
        self.crop.getexif()[274] = 6
        with self.assertRaisesRegex(ValueError, 'EXIF'):
            engine.infer_detail(self.overview, self.crop, [0, 0, 500, 500])
        engine._run.assert_not_called()

    def native_engine(self, raw):
        engine = qwen.Engine.__new__(qwen.Engine)
        engine.torch = Mock()
        engine.torch.inference_mode.side_effect = nullcontext
        for name in ('memory_allocated', 'memory_reserved', 'max_memory_allocated', 'max_memory_reserved'):
            getattr(engine.torch.cuda, name).return_value = 1024
        inputs = MagicMock(spec=['to', 'keys', '__getitem__'])
        grid = Mock(); grid.tolist.return_value = [[1, 40, 64], [1, 24, 32]]
        tensors = {'input_ids': Mock(shape=(1, 1300)), 'pixel_values': Mock(shape=(3328, 1536)), 'image_grid_thw': grid}
        inputs.__getitem__.side_effect = tensors.__getitem__
        inputs.keys.return_value = tensors.keys(); inputs.to.return_value = inputs
        engine.processor = Mock(return_value=inputs)
        engine.processor.image_processor.merge_size = 2
        engine.processor.apply_chat_template.return_value = 'compiled detail prompt'
        engine.processor.decode.return_value = raw
        tokens = MagicMock(); tokens.__getitem__.return_value = Mock(shape=(80,))
        engine.model = Mock(); engine.model.generate.return_value = tokens
        return engine

    def test_native_call_records_both_grids_and_raw_overview_result(self):
        raw = json.dumps(OBSERVATION, ensure_ascii=False)
        engine = self.native_engine(raw)
        self.assertEqual(engine.infer_detail(self.overview, self.crop, [0, 0, 500, 500]), OBSERVATION)
        self.assertEqual(engine.last_output, raw)
        self.assertEqual(engine.last_input_metadata['imageGridTHW'], [[1, 40, 64], [1, 24, 32]])
        self.assertEqual(engine.last_input_metadata['nativeGridCells'], 3328)
        kwargs = engine.processor.call_args.kwargs
        self.assertEqual(kwargs['images'], [self.overview, self.crop])
        self.assertEqual(kwargs['images_kwargs']['size']['longest_edge'], 1_048_576)
        self.assertFalse(engine.model.generate.call_args.kwargs['do_sample'])

    def test_invalid_generated_coordinates_and_unrecognized_classes_are_not_repaired(self):
        for replacement in ({'box': [0, 0, 1001, 900]}, {'box': [0, 0, .5, 900]},
                            {'box': [0, 0, False, 900]}, {'condition': 'overheating'}, {'frameIndex': 0}):
            raw = json.dumps({**OBSERVATION, **replacement})
            engine = self.native_engine(raw)
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                engine.infer_detail(self.overview, self.crop, [0, 0, 500, 500])
            self.assertEqual(engine.last_output, raw)
            self.assertIsNone(engine.last_metrics)
            self.assertIsNotNone(engine.last_memory_metrics)


if __name__ == '__main__':
    unittest.main()
