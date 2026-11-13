import base64
from copy import deepcopy
from email.message import Message
import hashlib
from io import BytesIO
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from server import detector, vision
from vision_runtime import detector_worker as worker


def envelope(detections=None):
    return {'schemaVersion': 1, 'model': 'team-detector', 'modelVersion': 'team-20261113-v1',
            'detections': [detection()] if detections is None else detections}


def detection(**changes):
    return {'class': 'corrosion', 'score': .8, 'component': 'pipe',
            'box': {'x': .1, 'y': .2, 'width': .5, 'height': .6}, **changes}


def jpeg(fmt='JPEG'):
    from PIL import Image
    with Image.new('RGB', (64, 48), 'white') as image:
        output = BytesIO()
        image.save(output, format=fmt)
    return output.getvalue()


class DetectorContractTest(unittest.TestCase):
    def test_multiple_boxes_keep_primary_highest_score_and_unknown_component(self):
        data = envelope([detection(), detection(score=.95, component='unknown', **{'class': 'wet_patch'})])
        before = deepcopy(data)
        result = detector.result_from_envelope(data)
        self.assertEqual(result['status'], 'defect')
        self.assertEqual(result['source'], 'local_vision')
        self.assertEqual(result['modelVersion'], 'team-20261113-v1')
        self.assertEqual(result['detections'][0]['class'], 'wet_patch')
        self.assertEqual(result['component'], 'Узел не определён')
        self.assertEqual(result['box'], data['detections'][1]['box'])
        self.assertEqual(result['scoreMeaning'], 'uncalibrated_detection_score')
        self.assertTrue(result['preliminary'])
        self.assertNotIn('confidence', result)
        self.assertEqual(data, before)

    def test_no_boxes_is_unusable_never_clear_or_healthy(self):
        result = detector.result_from_envelope(envelope([]))
        self.assertEqual(result['status'], 'unusable')
        self.assertEqual(result['reason'], 'no_supported_detections')
        self.assertEqual(result['detections'], [])
        self.assertNotIn('box', result)
        self.assertNotIn('finding', result)

    def test_trained_defect_labels_preserve_specific_findings(self):
        for label, wording in [('crack', 'трещина'), ('terminal_damage', 'клеммы'),
                               ('weld_porosity', 'пористость')]:
            with self.subTest(label=label):
                result = detector.result_from_envelope(envelope([detection(**{'class': label})]))
                self.assertEqual(result['detections'][0]['class'], label)
                self.assertIn(wording, result['finding'])

    def test_envelope_class_version_count_and_keys_are_strict(self):
        for changes in ({'schemaVersion': True}, {'schemaVersion': 2}, {'model': 'other'},
                        {'modelVersion': ''}, {'modelVersion': 'version with spaces'}, {'modelVersion': 'x' * 81},
                        {'modelVersion': None}, {'extra': 'not accepted'}, {'detections': {}},
                        {'detections': [detection()] * 65}, {'detections': [detection(**{'class': 'no_visible_defect'})]},
                        {'detections': [detection(component='none')]}, {'detections': [detection(extra=True)]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                detector.validate_envelope(envelope() | changes)
        self.assertEqual(len(detector.validate_envelope(envelope([detection()] * 64))['detections']), 64)

    def test_all_scores_and_coordinates_must_be_finite_valid_real_values(self):
        for score in (True, False, None, '0.8', float('inf'), float('nan'), -0.1, 1.01, 10 ** 999):
            with self.subTest(score=type(score).__name__), self.assertRaises(ValueError):
                detector.validate_envelope(envelope([detection(score=score)]))
        for change in ({'x': -.1}, {'x': 1}, {'y': 1}, {'width': 0}, {'height': -.1},
                       {'width': .91}, {'height': .81}, {'x': True}, {'y': '0.2'},
                       {'width': float('nan')}, {'height': float('inf')}, {'z': 0}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                detector.validate_envelope(envelope([detection(box=detection()['box'] | change)]))
        valid = detection(box={'x': 0, 'y': 0, 'width': 1, 'height': 1}, score=0)
        self.assertEqual(detector.validate_envelope(envelope([valid]))['detections'], [valid])

    def test_response_json_rejects_duplicates_and_nonfinite_literals(self):
        for body in (b'{"image":"one","image":"two"}', b'{"x":NaN}', b'{"x":Infinity}'):
            with self.assertRaises(ValueError):
                detector.strict_json(body)

    def test_request_has_one_frame_fixed_loopback_no_proxies_or_redirects(self):
        with patch.object(detector, 'build_opener') as build:
            build.return_value.open.return_value = BytesIO(json.dumps(envelope()).encode())
            result = detector.analyze(b'sanitized JPEG')
            request, = build.return_value.open.call_args.args
            self.assertEqual(request.full_url, 'http://127.0.0.1:11437/analyze')
            self.assertEqual(request.get_method(), 'POST')
            self.assertEqual(json.loads(request.data), {'image': base64.b64encode(b'sanitized JPEG').decode()})
            self.assertEqual(build.return_value.open.call_args.kwargs, {'timeout': 30})
            self.assertEqual(build.call_args.args[0].proxies, {})
            with self.assertRaises(URLError):
                build.call_args.args[1].redirect_request(request, None, 307, 'Redirect', {}, 'https://example.test')
        self.assertGreaterEqual(result['durationSeconds'], 0)

    def test_input_and_response_are_bounded_and_failure_releases_slot(self):
        with patch.object(detector, 'build_opener') as build:
            for invalid in (b'', 'string', b'x' * (detector.MAX_IMAGE_BYTES + 1)):
                with self.assertRaises(ValueError):
                    detector.analyze(invalid)
            build.assert_not_called()
            build.return_value.open.side_effect = [BytesIO(b'x' * (detector.MAX_RESPONSE_BYTES + 1)),
                                                  BytesIO(json.dumps(envelope()).encode())]
            with self.assertRaises(vision.VisionUnavailable):
                detector.analyze(b'frame')
            self.assertEqual(detector.analyze(b'frame')['status'], 'defect')

    def test_busy_is_typed_but_not_every_503_is_busy(self):
        for body, busy in (({'code': 'vision_busy'}, True), ({'code': 'detector_unavailable'}, False), ({}, False)):
            error = HTTPError(detector.ENDPOINT, 503, 'busy', {}, BytesIO(json.dumps(body).encode()))
            with patch.object(detector, 'request_envelope', side_effect=error), self.assertRaises(vision.VisionUnavailable) as caught:
                detector.analyze(b'frame')
            self.assertEqual(isinstance(caught.exception, vision.VisionBusy), busy)
        self.assertTrue(detector._slot.acquire(blocking=False))
        try:
            with patch.object(detector, 'request_envelope') as request, self.assertRaises(vision.VisionBusy):
                detector.analyze(b'frame')
            request.assert_not_called()
        finally:
            detector._slot.release()

    def test_invalid_worker_output_and_transport_failure_do_not_create_cards(self):
        for response in ([], None, envelope([detection(score=True)])):
            with patch.object(detector, 'request_envelope', return_value=response), self.assertRaises(vision.VisionUnavailable):
                detector.analyze(b'frame')
        with patch.object(detector, 'request_envelope', side_effect=URLError('offline')), self.assertRaises(vision.VisionUnavailable):
            detector.analyze(b'frame')

    def test_existing_vision_dispatch_uses_detector_and_rejects_fast_profile(self):
        with patch.object(detector, 'request_envelope', return_value=envelope()) as request:
            result = vision.analyze(b'frame', backend='detector')
            self.assertEqual(result['model'], 'team-detector')
            self.assertEqual(result['profile'], 'standard')
            self.assertEqual(len(result['detections']), 1)
            request.assert_called_once_with(b'frame')
        with patch.object(detector, 'request_envelope') as request, self.assertRaises(ValueError):
            vision.analyze(b'frame', backend='detector', profile='fast')
        request.assert_not_called()
        with patch.object(detector, 'request_envelope', side_effect=vision.VisionBusy('busy')), self.assertRaises(vision.VisionBusy):
            vision.analyze(b'frame', backend='detector')
        with patch.object(detector, 'request_envelope', return_value=envelope([])):
            self.assertEqual(vision.analyze(b'frame', backend='detector')['status'], 'unusable')


class WorkerContractTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='cyber102-detector-test-')
        self.addCleanup(self.directory.cleanup)
        self.weights = Path(self.directory.name) / 'best.pt'
        self.weights.write_bytes(b'fake weights: not loaded by any GPU library')
        self.manifest = {'schemaVersion': 1, 'model': 'team-detector', 'modelVersion': 'candidate-1',
                         'weightsSha256': hashlib.sha256(self.weights.read_bytes()).hexdigest(),
                         'classMap': {'0': {'sourceName': 'rust', 'class': 'corrosion', 'component': 'unknown'}},
                         'minimumScore': .4, 'imageSize': 640}
        self.manifest_path = Path(self.directory.name) / 'manifest.json'
        self.write_manifest()

    def write_manifest(self, changes=None):
        self.manifest_path.write_text(json.dumps(self.manifest | (changes or {})))

    def test_explicit_trust_and_correct_hash_required_before_loading(self):
        loader = Mock()
        with self.assertRaisesRegex(ValueError, 'trusted-local-weights'):
            worker.Engine(self.weights, self.manifest_path, loader=loader)
        self.write_manifest({'weightsSha256': '0' * 64})
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            worker.Engine(self.weights, self.manifest_path, trusted_local_weights=True, loader=loader)
        loader.assert_not_called()

    def test_missing_non_pt_or_remote_model_never_reaches_loader(self):
        loader = Mock()
        for path in (Path(self.directory.name) / 'missing.pt', 'https://example.test/best.pt', self.manifest_path):
            with self.subTest(path=path), self.assertRaises((ValueError, FileNotFoundError)):
                worker.Engine(path, self.manifest_path, trusted_local_weights=True, loader=loader)
        loader.assert_not_called()

    def test_manifest_rejects_missing_unknown_classes_ids_and_runtime_limits(self):
        invalid = [
            {'schemaVersion': True}, {'weightsSha256': 'bad'}, {'modelVersion': '../bad'}, {'classMap': {}},
            {'minimumScore': 0}, {'minimumScore': True}, {'minimumScore': float('inf')},
            {'imageSize': 4096}, {'imageSize': 650}, {'imageSize': True},
            {'classMap': {'00': self.manifest['classMap']['0']}},
            {'classMap': {'0': self.manifest['classMap']['0'] | {'class': 'healthy'}}},
            {'classMap': {'0': self.manifest['classMap']['0'] | {'component': 'boiler'}}},
            {'classMap': {'0': self.manifest['classMap']['0'] | {'sourceName': ''}}},
        ]
        for changes in invalid:
            self.write_manifest(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                worker.load_manifest(self.manifest_path)

    def test_class_map_is_checked_against_actual_weight_labels(self):
        worker.validate_model_names({0: 'rust'}, self.manifest['classMap'])
        worker.validate_model_names(['rust'], self.manifest['classMap'])
        for names in ({0: 'wrong'}, {1: 'rust'}, {0: 'rust', 1: 'damage'}, {False: 'rust'}):
            with self.assertRaises(ValueError):
                worker.validate_model_names(names, self.manifest['classMap'])

    def test_xyxy_conversion_is_top_left_and_low_scores_do_not_become_healthy(self):
        result = worker.envelope_from_rows([[.1, .2, .6, .8]], [.9], [0.0], self.manifest)
        self.assertEqual(result['detections'][0]['box']['x'], .1)
        self.assertEqual(result['detections'][0]['box']['y'], .2)
        self.assertAlmostEqual(result['detections'][0]['box']['width'], .5)
        self.assertAlmostEqual(result['detections'][0]['box']['height'], .6)
        self.assertEqual(result['detections'][0]['component'], 'unknown')
        low = worker.envelope_from_rows([[.1, .2, .6, .8]], [.39], [0], self.manifest)
        self.assertEqual(detector.result_from_envelope(low)['status'], 'unusable')
        for coords, scores, ids in (([[.1, .2, .6, .8]], [], [0]), ([[.1, .2, .6, .8]], [.8], [1]),
                                   ([[.1, .2, .6, .8]], [.8], [True]), ([[.1, .2, .6, .8]], [.8], [.5]),
                                   ([[.1, .2, .6, .8]], [.8], [float('nan')]), ([[.9, .2, 1.1, .8]], [.8], [0])):
            with self.assertRaises(ValueError):
                worker.envelope_from_rows(coords, scores, ids, self.manifest)

    def test_fake_engine_warms_up_and_reports_hash_without_gpu_imports(self):
        boxes = Mock()
        boxes.__len__ = Mock(return_value=1)
        boxes.cpu.return_value = boxes
        boxes.xyxyn.tolist.return_value = [[.1, .2, .6, .8]]
        boxes.conf.tolist.return_value = [.9]
        boxes.cls.tolist.return_value = [0.0]
        model = SimpleNamespace(task='detect', names={0: 'rust'}, predict=Mock(return_value=[SimpleNamespace(boxes=boxes)]))
        loader = Mock(return_value=model)
        engine = worker.Engine(self.weights, self.manifest_path, trusted_local_weights=True, device='cpu', loader=loader)
        self.assertFalse(engine.health()['ready'])
        engine.warmup()
        health = engine.health()
        self.assertTrue(health['ready'])
        self.assertEqual(health['weightsSha256'], self.manifest['weightsSha256'])
        self.assertEqual(health['modelVersion'], 'candidate-1')
        kwargs = model.predict.call_args.kwargs
        self.assertEqual(kwargs['max_det'], 64)
        self.assertEqual(kwargs['conf'], .4)
        self.assertFalse(kwargs['save'])
        self.assertFalse(kwargs['half'])
        self.assertEqual(kwargs['device'], 'cpu')
        boxes.xywhn.tolist.assert_not_called()

    def test_worker_jpeg_input_rejects_urls_extra_fields_png_and_nonimages(self):
        payload = {'image': base64.b64encode(jpeg()).decode()}
        with worker.read_image(payload) as image:
            self.assertEqual(image.mode, 'RGB')
            self.assertEqual(image.size, (64, 48))
        for invalid in ({'image': 'https://example.test/photo.jpg'}, payload | {'url': 'ignored?'},
                        {'image': base64.b64encode(jpeg('PNG')).decode()}, {'image': ''},
                        {'image': base64.b64encode(b'not an image').decode()}, {'image': 123}):
            with self.assertRaises(ValueError):
                worker.read_image(invalid)

    def test_offline_guard_refuses_external_connections_and_public_bind(self):
        worker.deny_external_connections('socket.connect', (None, ('127.0.0.1', 11437)))
        worker.deny_external_connections('socket.getaddrinfo', ('localhost',))
        for event, args in (('socket.connect', (None, ('94.41.87.102', 443))),
                            ('socket.connect', (None, ('external.example', 443))),
                            ('socket.getaddrinfo', ('example.test',))):
            with self.assertRaises(OSError):
                worker.deny_external_connections(event, args)
        with self.assertRaises(ValueError):
            worker.Server(('0.0.0.0', 11437), Mock())

    def handler(self, body=None, headers=None, engine=None):
        body = body if body is not None else json.dumps({'image': base64.b64encode(jpeg()).decode()}).encode()
        handler = worker.Handler.__new__(worker.Handler)
        handler.path = '/analyze'
        handler.headers = Message()
        for key, value in (headers if headers is not None else [('Content-Length', str(len(body))), ('Content-Type', 'application/json')]):
            handler.headers[key] = value
        handler.rfile = BytesIO(body)
        handler.server = SimpleNamespace(engine=engine or SimpleNamespace(ready=True, lock=threading.Lock(), infer=Mock(return_value=envelope())))
        handler.reply = Mock()
        return handler

    def test_http_worker_success_busy_not_ready_and_model_failure_release_lock(self):
        handler = self.handler()
        handler.do_POST()
        self.assertEqual(handler.reply.call_args.args[0], 200)
        self.assertFalse(handler.server.engine.lock.locked())
        handler.server.engine.lock.acquire()
        handler.do_POST()
        self.assertEqual(handler.reply.call_args.args[0], 503)
        self.assertEqual(handler.reply.call_args.args[1]['code'], 'vision_busy')
        handler.server.engine.lock.release()
        handler.server.engine.ready = False
        handler.do_POST()
        self.assertEqual(handler.reply.call_args.args[1]['code'], 'detector_not_ready')
        handler.server.engine.ready = True
        handler.server.engine.infer.return_value = envelope([detection(score=True)])
        handler.do_POST()
        # Reset consumed input to verify invalid inference, rather than incomplete body.
        handler = self.handler(engine=handler.server.engine)
        handler.do_POST()
        self.assertEqual(handler.reply.call_args.args[0], 502)
        self.assertFalse(handler.server.engine.lock.locked())

    def test_http_worker_rejects_browser_chunked_duplicate_length_and_bad_json(self):
        for headers, status in (([('Origin', 'https://example.test')], 400),
                                ([('Transfer-Encoding', 'chunked')], 400), ([], 413),
                                ([('Content-Length', '5'), ('Content-Length', '5')], 413),
                                ([('Content-Length', str(worker.MAX_BODY + 1))], 413),
                                ([('Content-Length', '5'), ('Content-Type', 'text/plain')], 400)):
            handler = self.handler(headers=headers)
            handler.do_POST()
            self.assertEqual(handler.reply.call_args.args[0], status)
            handler.server.engine.infer.assert_not_called()
        for body in (b'{}', b'{"image":"one","image":"two"}', b'not json'):
            handler = self.handler(body=body)
            handler.do_POST()
            self.assertEqual(handler.reply.call_args.args[0], 400)
            self.assertFalse(handler.server.engine.lock.locked())


if __name__ == '__main__':
    unittest.main()
