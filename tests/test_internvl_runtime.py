import argparse
import base64
import json
import os
import threading
import unittest
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, Mock, patch

from server import vision
from vision_runtime import worker
from vision_runtime import benchmark_profiles
from vision_runtime import download


OBSERVATION = {
    "component": "pump",
    "condition": "corrosion",
    "box": [200, 300, 800, 900],
    "observation": "На корпусе заметны следы коррозии.",
}


def encoded_image(image_format="JPEG", size=(32, 16)):
    from PIL import Image

    image = BytesIO()
    Image.new("L", size).save(image, image_format)
    return base64.b64encode(image.getvalue()).decode("ascii")


class RuntimeValidationTest(unittest.TestCase):
    def test_downloader_and_runtime_pin_the_same_native_release(self):
        self.assertEqual((download.REPOSITORY, download.REVISION), (worker.MODEL, worker.REVISION))
        self.assertEqual(worker.MODEL, 'OpenGVLab/InternVL3_5-2B-HF')
        self.assertEqual(len(worker.REVISION), 40)

    def test_accepts_exact_json_and_optional_single_fence(self):
        document = json.dumps(OBSERVATION, ensure_ascii=False)
        for output in (document, f"\n```json\n{document}\n```\n", f"```\n{document}\n```"):
            with self.subTest(output=output):
                self.assertEqual(worker.parse_observation(output), OBSERVATION)
        abstention = {"component": "none", "condition": "uncertain", "box": [0, 0, 0, 0], "observation": ""}
        self.assertEqual(worker.parse_observation(json.dumps(abstention)), abstention)

    def test_rejects_prose_invalid_fields_and_trailing_content(self):
        document = json.dumps(OBSERVATION)
        for output in (
            "{", "null", "[]", f"Result: {document}", f"{document} trailing text",
            f"```json\n{document}\n```\nSecond paragraph",
            json.dumps(OBSERVATION | {"analysis": "unexpected field"}),
            json.dumps({key: value for key, value in OBSERVATION.items() if key != "observation"}),
            json.dumps(OBSERVATION | {"box": [0, 0, 1001, 800]}),
            json.dumps(OBSERVATION | {"condition": "operational"}),
        ):
            with self.subTest(output=output), self.assertRaises(ValueError):
                worker.parse_observation(output)

    def test_compact_classification_uses_only_label_templates(self):
        compact = {key: value for key, value in OBSERVATION.items() if key != 'observation'}
        for output in (json.dumps(compact), f'```json\n{json.dumps(compact)}\n```'):
            result = worker.parse_compact(output)
            self.assertEqual(result, compact | {'observation': f'Корпус насоса: {vision.CONDITIONS["corrosion"][0].lower()}.'})
            self.assertNotIn('На корпусе', result['observation'])
        for fields in ({'component': 'none', 'condition': 'uncertain', 'box': [0, 0, 0, 0]},
                       compact | {'condition': 'uncertain'}):
            self.assertEqual(worker.parse_compact(json.dumps(fields)), fields | {'observation': ''})
        clean = worker.parse_compact(json.dumps(compact | {'condition': 'no_visible_defect'}))
        self.assertEqual(clean['observation'], 'Корпус насоса: явных внешних дефектов не замечено.')

    def test_compact_rejects_generated_prose_unknown_labels_and_invalid_coordinates(self):
        compact = {key: value for key, value in OBSERVATION.items() if key != 'observation'}
        for value in (None, [], {}, OBSERVATION, compact | {'explanation': 'invented'},
                      compact | {'component': []}, compact | {'condition': 'overheating'},
                      compact | {'box': [0, 0, 1001, 500]}, compact | {'box': [False, 1, 10, 10]}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                worker.parse_compact(json.dumps(value))
        with self.assertRaises(ValueError):
            worker.parse_compact('Description: ' + json.dumps(compact))

    def test_image_rejects_urls_extra_fields_and_invalid_base64(self):
        for payload in (
            {"image": "https://example.invalid/frame.jpg"},
            {"image": "data:image/jpeg;base64,YWJj"},
            {"image": "file:///tmp/frame.jpg"},
            {"image": "YWJj", "prompt": "override"},
            {"image": "YW Jj"}, {"image": 1}, [], {},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                worker.read_image(payload)

    def test_image_checks_encoded_decoded_format_and_pixel_limits(self):
        with patch.object(worker, "MAX_BODY", 8), self.assertRaisesRegex(ValueError, "too large"):
            worker.read_image({"image": "A" * 12})
        with patch.object(worker, "MAX_IMAGE_BYTES", 2), self.assertRaisesRegex(ValueError, "Invalid image size"):
            worker.read_image({"image": "YWJj"})
        for image in (encoded_image("GIF"), encoded_image("PNG", size=(3000, 3000))):
            with self.assertRaisesRegex(ValueError, "8 megapixels"):
                worker.read_image({"image": image})
        with worker.read_image({"image": encoded_image()}) as image:
            self.assertEqual((image.mode, image.size), ("RGB", (32, 16)))

    def test_profiles_are_optional_bounded_and_do_not_accept_client_generation_options(self):
        image = encoded_image()
        self.assertEqual(worker.request_profile({'image': image}), 'standard')
        for profile in ('standard', 'fast'):
            payload = {'image': image, 'profile': profile}
            self.assertEqual(worker.request_profile(payload), profile)
            with worker.read_image(payload) as decoded:
                self.assertEqual(decoded.size, (32, 16))
        for profile in ('', 'FAST', 'custom', None, True, 4, [], {}):
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                worker.read_image({'image': image, 'profile': profile})
        for extra in ({'max_patches': 1}, {'max_new_tokens': 1}, {'prompt': 'override'}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                worker.read_image({'image': image, **extra})

    def test_network_guard_allows_local_addresses_and_rejects_external_dns_and_connect(self):
        for host in ("127.0.0.1", "::1"):
            with self.subTest(host=host):
                worker.deny_external_connections("socket.connect", (None, (host, 11436)))
        for host in ("localhost", "127.0.0.1", "::1", None):
            with self.subTest(host=host):
                worker.deny_external_connections("socket.getaddrinfo", (host,))
        for event, arguments in (
            ("socket.connect", (None, ("192.0.2.1", 443))),
            ("socket.connect", (None, ("2001:db8::1", 443, 0, 0))),
            ("socket.connect", (None, "/tmp/worker.sock")),
            ("socket.getaddrinfo", ("example.invalid",)),
        ):
            with self.subTest(event=event, arguments=arguments), self.assertRaises(OSError):
                worker.deny_external_connections(event, arguments)

    def test_startup_installs_offline_guard_before_engine_load(self):
        offline_keys = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY", "DO_NOT_TRACK")

        with (patch.dict(os.environ, {key: "0" for key in offline_keys}),
              patch.object(worker.sys, "argv", ["worker", "--port", "12000", "--model-directory", "model"]),
              patch.object(worker.sys, "addaudithook") as audit,
              patch.object(worker, "Engine") as engine,
              patch.object(worker, "Server") as server,
              patch.object(worker, "print", create=True)):
            def load(directory):
                self.assertEqual(directory, Path("model"))
                audit.assert_called_once_with(worker.deny_external_connections)
                self.assertEqual([os.environ[key] for key in offline_keys], ["1"] * len(offline_keys))
                return engine.return_value

            engine.return_value.device = "fake GPU"
            engine.side_effect = load
            worker.main()
        server.assert_called_once_with(("127.0.0.1", 12000), engine.return_value)
        engine.return_value.warmup.assert_not_called()
        server.return_value.__enter__.return_value.serve_forever.assert_called_once()

    def test_optional_warmup_completes_before_http_server_is_created(self):
        events = []
        with (patch.object(worker.sys, 'argv', ['worker', '--warmup']),
              patch.object(worker.sys, 'addaudithook'),
              patch.object(worker, 'Engine') as engine,
              patch.object(worker, 'Server') as server,
              patch.object(worker, 'print', create=True)):
            engine.return_value.device = 'fake GPU'
            engine.return_value.warmup.side_effect = lambda: events.append('warmup') or {'totalMs': 10}
            server.side_effect = lambda *args: events.append('server') or MagicMock()
            worker.main()
        self.assertEqual(events, ['warmup', 'server'])


class EngineProfilingTest(unittest.TestCase):
    def setUp(self):
        self.engine = worker.Engine.__new__(worker.Engine)
        self.engine.torch = Mock()
        self.engine.torch.inference_mode.side_effect = nullcontext
        self.engine.prompt = 'invariant prompt'
        self.engine.compact_prompt = 'invariant compact prompt'
        inputs = MagicMock(spec=['to', 'keys', '__getitem__'])
        tensors = {'input_ids': Mock(shape=(1, 350)), 'pixel_values': Mock(shape=(5, 3, 448, 448))}
        inputs.__getitem__.side_effect = tensors.__getitem__
        inputs.keys.return_value = tensors.keys()
        inputs.to.return_value = inputs
        self.engine.processor = Mock(return_value=inputs)
        self.engine.processor.decode.return_value = json.dumps(OBSERVATION)
        tokens = MagicMock()
        tokens.__getitem__.return_value = Mock(shape=(97,))
        self.engine.model = Mock()
        self.engine.model.generate.return_value = tokens

    def test_fast_uses_compact_output_without_reducing_image_or_generation_budget(self):
        image = object()
        compact = {key: value for key, value in OBSERVATION.items() if key != 'observation'}
        for profile in ('standard', 'fast'):
            self.engine.processor.decode.return_value = json.dumps(compact if profile == 'fast' else OBSERVATION)
            with self.subTest(profile=profile), patch.object(worker.time, 'perf_counter', side_effect=[1, 1.2, 4.2, 4.25]):
                result = self.engine.infer(image, profile)
                self.assertEqual({key: result[key] for key in compact}, compact)
                self.assertEqual(result['observation'], OBSERVATION['observation'] if profile == 'standard'
                                     else f'Корпус насоса: {vision.CONDITIONS["corrosion"][0].lower()}.')
                expected_prompt = 'invariant compact prompt' if profile == 'fast' else 'invariant prompt'
                self.engine.processor.assert_called_with(text=expected_prompt, images=image, return_tensors='pt',
                                                         crop_to_patches=True, max_patches=4)
                options = self.engine.model.generate.call_args.kwargs
                self.assertEqual(options['max_new_tokens'], 180)
                self.assertEqual(options['max_time'], 85)
                self.assertIs(options['do_sample'], False)
                self.assertIs(options['use_cache'], True)
                self.assertEqual(self.engine.last_metrics, {
                    'preprocessMs': 200, 'generateMs': 3000, 'decodeMs': 50, 'totalMs': 3250,
                    'inputTokens': 350, 'outputTokens': 97, 'patches': 5,
                })
        self.engine.processor.apply_chat_template.assert_not_called()
        self.assertEqual(self.engine.torch.cuda.synchronize.call_count, 6)

    def test_failed_output_does_not_keep_previous_request_metrics(self):
        self.engine.last_metrics = {'totalMs': 123}
        self.engine.processor.decode.return_value = '{'
        with self.assertRaises(ValueError):
            self.engine.infer(object())
        self.assertIsNone(self.engine.last_metrics)

    def test_unknown_profile_never_reaches_processor(self):
        with self.assertRaises(ValueError):
            self.engine.infer(object(), profile='custom')
        self.engine.processor.assert_not_called()

    def test_constructor_precomputes_template_once_with_offline_loading(self):
        torch = Mock()
        torch.cuda.is_available.return_value = True
        transformers = Mock()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'cyber102-manifest.json').write_text(json.dumps({'repository': worker.MODEL, 'revision': worker.REVISION}))
            with patch.dict(worker.sys.modules, {'torch': torch, 'transformers': transformers}):
                engine = worker.Engine(root)
        processor = transformers.AutoProcessor.from_pretrained.return_value
        self.assertEqual(processor.apply_chat_template.call_count, 2)
        prompts = [call.args[0][-1]['content'][1]['text'] for call in processor.apply_chat_template.call_args_list]
        self.assertEqual(prompts, [worker.PROMPT, worker.COMPACT_PROMPT])
        for call in processor.apply_chat_template.call_args_list:
            self.assertEqual(call.args[0][0], {'role': 'system', 'content': worker.SYSTEM_PROMPT})
        self.assertEqual(engine.prompt, processor.apply_chat_template.return_value)
        self.assertIsNone(engine.last_metrics)
        transformers.AutoProcessor.from_pretrained.assert_called_once_with(root, local_files_only=True, trust_remote_code=False)
        self.assertIs(transformers.AutoModelForImageTextToText.from_pretrained.call_args.kwargs['local_files_only'], True)
        self.assertIs(transformers.AutoModelForImageTextToText.from_pretrained.call_args.kwargs['trust_remote_code'], False)
        self.assertIs(transformers.AutoModelForImageTextToText.from_pretrained.call_args.kwargs['use_safetensors'], True)

    def test_video_is_one_multimodal_generation_with_ordered_image_placeholders(self):
        images, timestamps = [object(), object()], [0, 2.5]
        model_rows = [dict(OBSERVATION, frameIndex=index) for index in range(2)]
        self.engine.processor.decode.return_value = json.dumps({'observations': model_rows, 'summary': 'На двух кадрах видны признаки коррозии.'})
        with patch.object(worker.time, 'perf_counter', side_effect=[1, 1.2, 4.2, 4.25]):
            result = self.engine.infer_video(images, timestamps)
        content = self.engine.processor.apply_chat_template.call_args.args[0][-1]['content']
        self.assertEqual([item['type'] for item in content], ['text', 'image', 'text', 'image', 'text'])
        self.assertIn('Frame 1, time 2.5', content[2]['text'])
        self.assertIn('EXACTLY 2 observations', content[-1]['text'])
        self.assertIn('[0, 1]', content[-1]['text'])
        self.assertIn('NOT frame indices', content[-1]['text'])
        self.engine.processor.assert_called_once_with(text=self.engine.processor.apply_chat_template.return_value,
            images=images, return_tensors='pt', crop_to_patches=True, max_patches=worker.VIDEO_MAX_PATCHES)
        self.engine.model.generate.assert_called_once()
        options = self.engine.model.generate.call_args.kwargs
        self.assertEqual((options['max_new_tokens'], options['max_time']), (1800, 110))
        self.assertEqual([row['timestampSeconds'] for row in result['observations']], timestamps)
        self.assertEqual(self.engine.last_metrics['frames'], 2)


class VideoValidationTest(unittest.TestCase):
    def test_bounds_timestamps_fields_and_frame_count_before_decoding(self):
        frame = {'image': encoded_image(), 'timestampSeconds': 0}
        self.assertEqual(worker.video_request({'frames': [frame], 'durationSeconds': 5}), ([frame], 5))
        bad = [None, {}, {'frames': []}, {'frames': [frame] * 9},
               {'frames': [frame, frame]}, {'frames': [frame], 'prompt': 'override'},
               {'frames': [dict(frame, timestampSeconds=6)], 'durationSeconds': 5},
               {'frames': [dict(frame, timestampSeconds=-1)]},
               {'frames': [dict(frame, timestampSeconds=True)]},
               {'frames': [dict(frame, timestampSeconds=float('nan'))]},
               {'frames': [dict(frame, timestampSeconds=float('inf'))]},
               {'frames': [dict(frame, timestampSeconds=10**1000)]},
               {'frames': [dict(frame, timestampSeconds=121)]},
               {'frames': [frame], 'durationSeconds': 0},
               {'frames': [frame], 'durationSeconds': True},
               {'frames': [frame], 'durationSeconds': 10**1000},
               {'frames': [dict(frame, url='https://example.invalid')]},
               {'frames': [dict(frame, image=123)]}]
        for payload in bad:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                worker.video_request(payload)

    def test_output_requires_each_frame_once_and_preserves_input_timestamps(self):
        rows = [dict(OBSERVATION, frameIndex=index) for index in range(2)]
        data = {'observations': rows, 'summary': 'Видны признаки коррозии.'}
        self.assertEqual([row['timestampSeconds'] for row in worker.parse_video_observations(json.dumps(data), [0, 2.5])['observations']], [0, 2.5])
        invalid = [dict(data, observations=[]), dict(data, observations=rows[:1]),
                   dict(data, observations=rows[::-1]), dict(data, observations=[rows[0], rows[0]]),
                   dict(data, observations=[dict(rows[0], frameIndex=False), rows[1]]),
                   dict(data, observations=[dict(rows[0], timestampSeconds=99), rows[1]]),
                   dict(data, observations=[dict(rows[0], box=[0, 0, 1001, 800]), rows[1]]),
                   dict(data, observations=[dict(rows[0], condition='safe'), rows[1]]),
                   dict(data, summary=''), dict(data, summary='x' * 1501), dict(data, diagnosis='invented')]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                worker.parse_video_observations(json.dumps(value), [0, 2.5])

    def test_abstention_is_allowed_but_duplicate_json_and_nonfinite_fields_are_rejected(self):
        none = {'component': 'none', 'condition': 'uncertain', 'box': [0, 0, 0, 0], 'observation': '', 'frameIndex': 0}
        result = worker.parse_video_observations(json.dumps({'observations': [none], 'summary': 'Недостаточно видимых данных.'}), [0])
        self.assertEqual(result['observations'][0]['component'], 'none')
        for text in ('{"observations":[],"summary":"a","summary":"b"}', '{"observations":[],"summary":NaN}'):
            with self.assertRaises(ValueError):
                worker.parse_video_observations(text, [0])


class FakeEngine:
    device = "fake GPU"

    def __init__(self):
        self.lock = threading.Lock()
        self.started = threading.Event()
        self.finish = threading.Event()
        self.finish.set()
        self.images = []
        self.profiles = []
        self.last_metrics = None

    def infer(self, image, profile='standard'):
        self.images.append((image.mode, image.size))
        self.profiles.append(profile)
        self.started.set()
        if not self.finish.wait(3):
            raise RuntimeError("Test did not release inference")
        self.last_metrics = {'preprocessMs': 1, 'generateMs': 10, 'decodeMs': 1, 'totalMs': 12,
                             'inputTokens': 400, 'outputTokens': 100, 'patches': worker.PROFILES[profile]}
        return OBSERVATION

    def infer_video(self, images, timestamps):
        self.images.extend((image.mode, image.size) for image in images)
        self.last_metrics = {'frames': len(images)}
        return {'observations': [dict(OBSERVATION, frameIndex=index, timestampSeconds=timestamp)
                                 for index, timestamp in enumerate(timestamps)],
                'summary': 'На выбранных кадрах видны признаки коррозии.'}


class RuntimeHTTPTest(unittest.TestCase):
    def setUp(self):
        self.engine = FakeEngine()
        self.server = worker.Server(("127.0.0.1", 0), self.engine)
        self.addCleanup(self.server.server_close)
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        self.addCleanup(thread.join, 3)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, payload=None, headers=None, expected=200):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=4)
        request_headers = {"Content-Type": "application/json", **(headers or {})}
        try:
            connection.request(method, path, json.dumps(payload).encode() if payload is not None else None, request_headers)
            response = connection.getresponse()
            data = json.loads(response.read())
            self.assertEqual(response.status, expected, data)
            self.assertEqual(response.getheader("Cache-Control"), "no-store")
            return data
        finally:
            connection.close()

    def test_analyze_and_health_with_no_model_loaded(self):
        health = self.request("GET", "/health")
        self.assertEqual((health["model"], health["revision"], health["device"]),
                         (worker.MODEL, worker.REVISION, self.engine.device))
        self.assertIs(health["offline"], True)
        self.assertIs(health["busy"], False)
        payload = {"image": encoded_image()}
        data = self.request("POST", "/analyze", payload)
        self.assertEqual(data["observation"], OBSERVATION)
        self.assertEqual(data["model"], worker.MODEL)
        self.assertGreaterEqual(data["durationSeconds"], 0)
        self.assertEqual(data['profile'], 'standard')
        self.assertEqual(data['observationKind'], 'model')
        self.assertEqual(data['metrics'], self.engine.last_metrics)
        self.assertEqual(health['profiles'], ['standard', 'fast'])
        self.assertEqual(health['defaultProfile'], 'standard')
        self.assertEqual(self.engine.images, [("RGB", (32, 16))])

    def test_bad_uploads_and_browser_origin_do_not_reach_engine(self):
        payload = {"image": encoded_image()}
        for headers, status in (
            ({"Origin": "http://127.0.0.1"}, 400),
            ({"Content-Type": "text/plain"}, 400),
            ({"Content-Length": str(worker.MAX_BODY + 1)}, 413),
            ({"Transfer-Encoding": "chunked", "Content-Length": "2"}, 400),
        ):
            with self.subTest(headers=headers):
                self.assertIn("error", self.request("POST", "/analyze", payload, headers, expected=status))
        self.assertIn("error", self.request("POST", "/analyze", {"image": "https://example.invalid/pump.jpg"}, expected=400))
        self.assertEqual(self.engine.images, [])

    def test_video_endpoint_returns_frame_grounded_observations(self):
        frames = [{'image': encoded_image(), 'timestampSeconds': timestamp} for timestamp in (0, 3)]
        result = self.request('POST', '/analyze-video', {'frames': frames, 'durationSeconds': 5})
        self.assertEqual(result['model'], worker.MODEL)
        self.assertEqual(result['revision'], worker.REVISION)
        self.assertEqual(result['clipDurationSeconds'], 5)
        self.assertEqual([row['timestampSeconds'] for row in result['observations']], [0, 3])
        self.assertEqual(self.engine.images, [('RGB', (32, 16))] * 2)

    def test_video_errors_do_not_infer_partial_decoded_clips(self):
        frame = {'image': encoded_image(), 'timestampSeconds': 0}
        for payload in ({'frames': [frame, dict(frame, timestampSeconds=1, image='bad')]},
                        {'frames': [frame, frame]}, {'frames': [frame], 'prompt': 'override'}):
            self.request('POST', '/analyze-video', payload, expected=400)
        self.assertEqual(self.engine.images, [])
        with patch.object(self.engine, 'infer_video', side_effect=ValueError('invalid model JSON')):
            self.request('POST', '/analyze-video', {'frames': [frame]}, expected=502)
        self.assertIs(self.engine.lock.locked(), False)

    def test_video_and_photo_share_the_same_busy_lock(self):
        with self.engine.lock:
            failure = self.request('POST', '/analyze-video', {'frames': [{'image': encoded_image(), 'timestampSeconds': 0}]}, expected=503)
        self.assertEqual(failure['code'], 'vision_busy')

    def test_explicit_profile_is_reported_and_invalid_profile_never_reaches_engine(self):
        payload = {'image': encoded_image()}
        for profile in (None, 'unknown', {}, 1):
            self.request('POST', '/analyze', payload | {'profile': profile}, expected=400)
        self.assertEqual(self.engine.images, [])
        result = self.request('POST', '/analyze', payload | {'profile': 'fast'})
        self.assertEqual(result['profile'], 'fast')
        self.assertEqual(result['observationKind'], 'template')
        self.assertEqual(result['metrics']['patches'], 4)
        self.assertEqual(result['observation'], OBSERVATION)
        self.assertEqual(self.engine.profiles, ['fast'])

    def test_busy_rejects_second_request_then_accepts_next_frame(self):
        payload = {"image": encoded_image()}
        self.engine.finish.clear()
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(self.request, "POST", "/analyze", payload)
            try:
                self.assertTrue(self.engine.started.wait(2))
                self.assertIs(self.request("GET", "/health")["busy"], True)
                self.assertIn("busy", self.request("POST", "/analyze", payload, expected=503)["error"])
                self.assertEqual(len(self.engine.images), 1)
            finally:
                self.engine.finish.set()
            self.assertEqual(first.result(timeout=4)["model"], worker.MODEL)
        self.assertTrue(self.engine.lock.acquire(timeout=2))
        self.engine.lock.release()
        self.assertIs(self.request("GET", "/health")["busy"], False)
        self.assertEqual(self.request("POST", "/analyze", payload)["observation"], OBSERVATION)
        self.assertEqual(len(self.engine.images), 2)


class ProfileBenchmarkTest(unittest.TestCase):
    def test_only_loopback_endpoint_is_allowed(self):
        self.assertEqual(benchmark_profiles.local_endpoint('http://127.0.0.1:11436/analyze'), 'http://127.0.0.1:11436/analyze')
        for endpoint in ('https://example.com/analyze', 'http://192.168.1.1:80/analyze',
                         'http://user:pass@127.0.0.1:80/analyze', 'http://127.0.0.1:80/analyze?remote=1'):
            with self.subTest(endpoint=endpoint), self.assertRaises(argparse.ArgumentTypeError):
                benchmark_profiles.local_endpoint(endpoint)

    def test_warmup_is_excluded_but_errors_are_retained(self):
        records = [
            {'profile': 'fast', 'phase': 'warmup', 'seconds': 99, 'result': {}},
            {'profile': 'fast', 'phase': 'measured', 'seconds': 1, 'result': {}},
            {'profile': 'fast', 'phase': 'measured', 'seconds': 3, 'result': {}},
            {'profile': 'fast', 'phase': 'measured', 'seconds': 10, 'error': 'HTTP 502'},
        ]
        summary = benchmark_profiles.summarize(records, ['fast'])['fast']
        self.assertEqual(summary, {'requests': 3, 'successful': 2, 'errors': 1,
                                  'medianSeconds': 2, 'p95Seconds': 3, 'allRequestP95Seconds': 10})

    def test_comparison_reports_disagreement_not_accuracy(self):
        a = {'case': 'pump', 'repeat': 0, 'phase': 'measured', 'profile': 'standard', 'status': 'defect',
             'result': {'observation': OBSERVATION}}
        b = {**a, 'profile': 'fast', 'status': 'clear',
             'result': {'observation': OBSERVATION | {'condition': 'no_visible_defect'}}}
        result, = benchmark_profiles.compare([a, b])
        self.assertIs(result['conditionChanged'], True)
        self.assertIs(result['statusChanged'], True)
        self.assertIs(result['componentChanged'], False)
        self.assertEqual(result['boxIoU'], 1)


if __name__ == "__main__":
    unittest.main()
