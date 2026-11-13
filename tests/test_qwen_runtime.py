from contextlib import nullcontext
from hashlib import sha1, sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, Mock, patch

from vision_runtime import qwen_download, qwen_worker, worker


OBSERVATION = {'component': 'pump', 'condition': 'corrosion', 'box': [100, 200, 700, 800],
               'observation': 'На корпусе видны следы коррозии.'}


class QwenSnapshotTest(unittest.TestCase):
    def test_pins_match_and_native_files_exclude_executable_content(self):
        self.assertEqual((qwen_worker.MODEL, qwen_worker.REVISION),
                         (qwen_download.REPOSITORY, qwen_download.REVISION))
        for name in ('config.json', 'model-00001-of-00002.safetensors', 'chat_template.json'):
            self.assertTrue(qwen_download.allowed_file(name))
        for name in ('modeling_qwen.py', 'weights.bin', '../config.json', r'..\config.json', '.hidden.json', None):
            self.assertFalse(qwen_download.allowed_file(name))

    def test_verifies_official_sha_and_git_blob_before_accepting_file(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            data = b'{"model_type":"qwen3_vl"}'
            path.write_bytes(data)
            entry = {'size': len(data), 'blobId': sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()}
            self.assertEqual(qwen_download.verify_file(path, entry)['sha256'], sha256(data).hexdigest())
            entry = {'size': len(data), 'lfs': {'sha256': sha256(data).hexdigest()}}
            self.assertEqual(qwen_download.verify_file(path, entry)['bytes'], len(data))
            for bad in ({'size': 2}, {'size': len(data)}, {'size': len(data), 'lfs': {'sha256': '0'*64}}):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    qwen_download.verify_file(path, bad)

    def test_refuses_tls_downgrade_redirect(self):
        with self.assertRaises(ValueError):
            qwen_download.HttpsOnlyRedirect().redirect_request(None, None, 302, '', {}, 'http://example.invalid/model')


class QwenEngineTest(unittest.TestCase):
    def make_engine(self):
        engine = qwen_worker.Engine.__new__(qwen_worker.Engine)
        engine.torch = Mock()
        engine.torch.inference_mode.side_effect = nullcontext
        for name in ('memory_allocated', 'memory_reserved', 'max_memory_allocated', 'max_memory_reserved'):
            getattr(engine.torch.cuda, name).return_value = 1024*1024
        inputs = MagicMock(spec=['to', 'keys', '__getitem__'])
        grid = Mock()
        grid.tolist.return_value = [[1, 40, 64]]
        tensors = {'input_ids': Mock(shape=(1, 900)), 'pixel_values': Mock(shape=(2560, 1536)), 'image_grid_thw': grid}
        inputs.__getitem__.side_effect = tensors.__getitem__
        inputs.keys.return_value = tensors.keys()
        inputs.to.return_value = inputs
        engine.processor = Mock(return_value=inputs)
        engine.processor.image_processor.merge_size = 2
        engine.processor.decode.return_value = json.dumps(OBSERVATION)
        tokens = MagicMock()
        tokens.__getitem__.return_value = Mock(shape=(70,))
        engine.model = Mock()
        engine.model.generate.return_value = tokens
        engine.prompt, engine.compact_prompt = 'standard', 'compact'
        return engine

    def test_native_offline_constructor_uses_pinned_manifest_and_no_remote_code(self):
        torch, transformers = Mock(), Mock()
        torch.cuda.is_available.return_value = True
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'cyber102-manifest.json').write_text(json.dumps({'repository': qwen_worker.MODEL, 'revision': qwen_worker.REVISION}))
            with patch.dict('sys.modules', {'torch': torch, 'transformers': transformers}):
                engine = qwen_worker.Engine(root)
            kwargs = transformers.Qwen3VLForConditionalGeneration.from_pretrained.call_args.kwargs
            self.assertIs(kwargs['local_files_only'], True)
            self.assertIs(kwargs['trust_remote_code'], False)
            self.assertIs(kwargs['use_safetensors'], True)
            self.assertEqual(kwargs['attn_implementation'], 'sdpa')
            self.assertEqual(engine.model_id, qwen_worker.MODEL)
            self.assertEqual(engine.revision, qwen_worker.REVISION)
            processor = transformers.AutoProcessor.from_pretrained.return_value
            self.assertEqual(processor.apply_chat_template.call_count, 2)
            for call in processor.apply_chat_template.call_args_list:
                self.assertEqual(call.args[0][0]['content'], worker.SYSTEM_PROMPT)

    def test_photo_bounds_native_pixels_and_matches_existing_generation_limits(self):
        engine, image = self.make_engine(), object()
        with patch.object(qwen_worker.time, 'perf_counter', side_effect=[1, 1.1, 2.1, 2.2]):
            self.assertEqual(engine.infer(image), OBSERVATION)
        engine.processor.assert_called_once_with(text='standard', images=image, return_tensors='pt',
            images_kwargs={'size': {'shortest_edge': 65536, 'longest_edge': 1048576}})
        options = engine.model.generate.call_args.kwargs
        self.assertEqual((options['max_new_tokens'], options['max_time']), (180, 85))
        self.assertIs(options['do_sample'], False)
        self.assertEqual(engine.last_metrics['patches'], 2560)
        self.assertEqual(engine.last_input_metadata['visionTokens'], 640)
        self.assertEqual(engine.last_input_metadata['imageGridTHW'], [[1, 40, 64]])
        self.assertEqual(engine.last_memory_metrics['peakAllocatedMiB'], 1)

    def test_video_uses_one_context_exact_indices_and_shared_grounded_prompt(self):
        engine = self.make_engine()
        rows = [dict(OBSERVATION, frameIndex=index) for index in range(2)]
        engine.processor.decode.return_value = json.dumps({'observations': rows, 'summary': 'Видны признаки коррозии.'})
        result = engine.infer_video([object(), object()], [0.5, 4.5])
        content = engine.processor.apply_chat_template.call_args.args[0][-1]['content']
        self.assertEqual([item['type'] for item in content], ['text', 'image', 'text', 'image', 'text'])
        self.assertIn(worker.VIDEO_PROMPT, content[-1]['text'])
        self.assertIn('EXACTLY 2 observations', content[-1]['text'])
        self.assertIn('[0, 1]', content[-1]['text'])
        self.assertEqual([row['timestampSeconds'] for row in result['observations']], [0.5, 4.5])
        self.assertEqual(engine.last_metrics['frames'], 2)
        self.assertEqual(engine.last_input_metadata['maxPixels'], qwen_worker.VIDEO_MAX_PIXELS)
        engine.model.generate.assert_called_once()

    def test_invalid_model_json_is_not_repaired_and_raw_is_available_only_on_engine(self):
        engine = self.make_engine()
        engine.last_metrics = {'old': True}
        engine.processor.decode.return_value = '{bad output'
        with self.assertRaises(ValueError):
            engine.infer(object())
        self.assertEqual(engine.last_output, '{bad output')
        self.assertIsNone(engine.last_metrics)
        self.assertEqual(engine.last_memory_metrics['peakReservedMiB'], 1)

    def test_unsupported_profile_and_oversized_video_never_reach_processor(self):
        engine = self.make_engine()
        with self.assertRaises(ValueError):
            engine.infer(object(), 'custom')
        with self.assertRaises(ValueError):
            engine.infer_video([object()]*9, list(range(9)))
        engine.processor.assert_not_called()


if __name__ == '__main__':
    unittest.main()
