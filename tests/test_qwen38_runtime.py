import base64
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.request import ProxyHandler, build_opener

from PIL import Image

from vision_runtime import qwen38_download, qwen38_worker, worker


class Qwen38RuntimeTest(unittest.TestCase):
    def test_verified_files_cannot_be_substituted_even_with_a_valid_manifest(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            content = b'gguf-data'
            files = {name: {'size': len(content), 'lfs': {'sha256': sha256(content).hexdigest()}}
                     for name in ('model.gguf', 'projector.gguf')}
            for name in files:
                (root / name).write_bytes(content)
            with patch.object(qwen38_download, 'FILES', files):
                qwen38_download.write_manifest(root)
                self.assertEqual(len(qwen38_download.verify_directory(root)), 2)
                (root / 'model.gguf').write_bytes(b'evil-data')
                with self.assertRaises(ValueError):
                    qwen38_download.verify_directory(root)
                (root / 'model.gguf').write_bytes(content)
                manifest = root / 'cyber102-manifest.json'
                data = json.loads(manifest.read_text())
                data['revision'] = 'changed'
                manifest.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    qwen38_download.verify_directory(root)

    def test_bounded_memory_recipe_keeps_one_gpu_slot_and_no_host_prompt_cache(self):
        args = qwen38_worker.runtime_arguments(Path('/models/qwen'))
        for flag, value in (('-ngl', '999'), ('--fit', 'off'), ('-np', '1'), ('-c', '8192'),
                            ('--cache-ram', '0'), ('--ctx-checkpoints', '1'),
                            ('--image-max-tokens', '768'), ('--host', '127.0.0.1')):
            self.assertEqual(args[args.index(flag) + 1], value)
        for flag in ('--offline', '--no-host', '--no-cache-idle-slots'):
            self.assertIn(flag, args)

    def test_image_area_preserves_aspect_and_does_not_upscale_small_details(self):
        for size in ((3000, 1000), (350, 150)):
            with Image.new('RGB', size) as image:
                content = qwen38_worker.image_content(image, 786432)
            raw = base64.b64decode(content['image_url']['url'].split(',', 1)[1])
            with Image.open(BytesIO(raw)) as resized:
                self.assertLessEqual(resized.width * resized.height, 786432)
                self.assertAlmostEqual(resized.width / resized.height, size[0] / size[1], delta=.01)
                if size[0] == 350:
                    self.assertEqual(resized.size, size)

    def engine(self):
        engine = qwen38_worker.Engine.__new__(qwen38_worker.Engine)
        engine.http = build_opener(ProxyHandler({}))
        engine.process = Mock()
        engine.process.poll.return_value = None
        engine.job = engine.log = None
        engine.lock = threading.Lock()
        engine.prompt = engine._prompt([{'type': 'image'}, {'type': 'text', 'text': worker.PROMPT}])
        engine.compact_prompt = engine._prompt([{'type': 'image'}, {'type': 'text', 'text': worker.COMPACT_PROMPT}])
        return engine

    def test_grounded_video_keeps_evidence_attached_to_its_submitted_frame(self):
        rows = [{'frameIndex': 0, 'component': 'other', 'condition': 'corrosion',
                 'box': [100, 200, 500, 700], 'observation': 'На опоре видны пятна ржавчины.'},
                {'frameIndex': 1, 'component': 'motor', 'condition': 'no_visible_defect',
                 'box': [150, 300, 700, 900], 'observation': 'Видимых повреждений корпуса двигателя нет.'}]
        result = qwen38_worker.parse_grounded_video(json.dumps(rows), [.4, 1.2])
        self.assertEqual([row['timestampSeconds'] for row in result], [.4, 1.2])
        self.assertEqual([row['condition'] for row in result], ['corrosion', 'no_visible_defect'])
        self.assertEqual(result[0]['observation'], rows[0]['observation'])

    def test_grounded_video_rejects_missing_reordered_or_malformed_evidence(self):
        row = {'frameIndex': 0, 'component': 'other', 'condition': 'corrosion',
               'box': [100, 200, 500, 700], 'observation': 'Видна ржавчина.'}
        for rows in ([], [row, row], [row | {'frameIndex': True}], [row | {'frameIndex': 1}],
                     [row | {'box': [900, 200, 100, 700]}], [row | {'extra': 'untrusted'}],
                     [row | {'component': 'unknown'}]):
            with self.subTest(rows=rows), self.assertRaises(qwen38_worker.InvalidVideoOutput):
                qwen38_worker.parse_grounded_video(json.dumps(rows), [.4])

    def test_invalid_pair_falls_back_to_proven_compact_single_frame_contract(self):
        engine = self.engine()
        compact = {'component': 'pipe', 'condition': 'corrosion', 'box': [100, 200, 500, 700]}
        def infer(images, prompt, *, parse, **kwargs):
            engine.last_metrics = {'outputTokens': 40, 'frames': kwargs['frame_count']}
            return parse('[]' if isinstance(images, list) and len(images) > 1 else json.dumps(compact))
        engine._run = Mock(side_effect=infer)
        with patch.dict(os.environ, {'CYBER102_VIDEO_DEFECT_DETAILS': '0'}), Image.new('RGB', (100, 80)) as image:
            result = engine.infer_video([image, image], [.4, 1.2])
        self.assertEqual(engine._run.call_count, 3)
        self.assertEqual([row['frameIndex'] for row in result['observations']], [0, 1])
        self.assertEqual([row['timestampSeconds'] for row in result['observations']], [.4, 1.2])
        self.assertTrue(all(row['condition'] == 'corrosion' and row['observation'] for row in result['observations']))

    def test_real_loopback_protocol_preserves_photo_video_and_detail_contracts_without_torch(self):
        received = []

        class NativeHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                received.append(payload)
                compact = dict(component='pipe', condition='corrosion', box=[100, 100, 800, 800])
                content = payload['messages'][1]['content']
                images = [item for item in content if item['type'] == 'image_url']
                prompt = content[-1]['text']
                if 'one object per frame' in prompt:
                    answer = [dict(compact, frameIndex=i, observation='На трубе видна коррозия.') for i in range(len(images))]
                elif 'exactly two frames' in prompt:
                    answer = [dict(compact, frameIndex=i) for i in range(len(images))]
                elif 'exactly three keys' in prompt:
                    answer = compact
                else:
                    answer = dict(compact, observation='На трубе видна коррозия.')
                raw = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(answer)}}],
                                  'usage': {'prompt_tokens': 700, 'completion_tokens': 40}}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        with ThreadingHTTPServer(('127.0.0.1', 0), NativeHandler) as native:
            thread = threading.Thread(target=native.serve_forever, daemon=True)
            thread.start()
            try:
                engine = self.engine()
                with patch.object(qwen38_worker, 'NATIVE_PORT', native.server_port), \
                        patch.dict(os.environ, {'CYBER102_VIDEO_DEFECT_DETAILS': '0'}), \
                        Image.new('RGB', (100, 80)) as image:
                    self.assertEqual(engine.infer(image)['condition'], 'corrosion')
                    self.assertEqual(engine.infer(image, profile='fast')['component'], 'pipe')
                    self.assertEqual(engine.infer_detail(image, image, [0, 0, 800, 800])['condition'], 'corrosion')
                    result = engine.infer_video([image] * 8, list(range(8)))
                self.assertEqual([row['frameIndex'] for row in result['observations']], list(range(8)))
                self.assertEqual(engine.last_metrics['frames'], 8)
                self.assertEqual(engine.last_metrics['outputTokens'], 160)
                self.assertEqual(len(received), 7)
                self.assertFalse(hasattr(engine, 'torch'))
                self.assertEqual(result['observations'][0]['observation'], 'На трубе видна коррозия.')
                self.assertTrue(all(row['model'] == qwen38_worker.ALIAS for row in received))
                self.assertTrue(all(row['cache_prompt'] is False for row in received))
            finally:
                native.shutdown()
                thread.join(timeout=3)

    def test_timeout_ends_child_before_releasing_lock_for_the_next_job(self):
        engine = self.engine()
        engine.close = Mock()
        engine._json = Mock(side_effect=TimeoutError())
        with Image.new('RGB', (40, 40)) as image, self.assertRaises(RuntimeError):
            engine.infer(image)
        engine.close.assert_called_once()

    def test_native_crash_is_not_reported_as_a_healthy_worker(self):
        engine = self.engine()
        engine.process.poll.return_value = 1
        self.assertFalse(engine.is_healthy())
        with worker.Server(('127.0.0.1', 0), engine) as server, self.assertRaises(RuntimeError):
            server.service_actions()


if __name__ == '__main__':
    unittest.main()
