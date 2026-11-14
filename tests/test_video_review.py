import base64
import json
import unittest
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from PIL import Image, PngImagePlugin

from server import video_review, vision
from vision_runtime.video_defects import selected_frames


def encoded_image(size=(32, 24), format='JPEG'):
    output = BytesIO()
    image = Image.new('RGB', size, 'navy')
    options = {}
    if format == 'PNG':
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text('private-note', 'must not reach the worker')
        options['pnginfo'] = metadata
    image.save(output, format=format, **options)
    return base64.b64encode(output.getvalue()).decode('ascii')


def request_data():
    image = encoded_image()
    return {'durationSeconds': 10, 'frames': [
        {'image': image, 'timestampSeconds': 1.25},
        {'image': image, 'timestampSeconds': 8.75},
    ]}


def worker_response(data, *, condition='corrosion'):
    return {'model': vision.INTERNVL_MODEL, 'revision': vision.INTERNVL_REVISION,
            'summary': 'На выбранных ракурсах виден бурый налёт.', 'durationSeconds': 2.5,
            'clipDurationSeconds': data['durationSeconds'],
            'observations': [
                {'frameIndex': index, 'timestampSeconds': frame['timestampSeconds'],
                 'component': 'pipe', 'condition': condition,
                 'box': [100, 200, 600, 800], 'observation': 'На фланце виден бурый налёт.'}
                for index, frame in enumerate(data['frames'])]}


class VideoRequestValidationTests(unittest.TestCase):
    def test_png_is_downscaled_and_metadata_removed_before_relay(self):
        data = request_data()
        data['frames'][0]['image'] = encoded_image((2000, 1000), 'PNG')
        prepared = video_review.prepare_request(data)
        self.assertEqual(set(prepared), {'frames', 'durationSeconds'})
        self.assertEqual(prepared['frames'][0]['timestampSeconds'], 1.25)
        self.assertEqual(prepared['durationSeconds'], 10)
        with Image.open(BytesIO(base64.b64decode(prepared['frames'][0]['image']))) as image:
            self.assertEqual(image.format, 'JPEG')
            self.assertEqual(image.size, (1280, 640))
            self.assertEqual(image.mode, 'RGB')
            self.assertNotIn('private-note', image.info)
            self.assertFalse(image.getexif())

    def test_rejects_invalid_duration_frame_counts_and_extra_instructions(self):
        for duration in (0, -1, 120.01, True, '10', float('nan'), float('inf'), 10 ** 500):
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                video_review.prepare_request(request_data() | {'durationSeconds': duration})
        for frames in ([], None, 'frames', request_data()['frames'] * 5):
            with self.subTest(frames=frames), self.assertRaises(ValueError):
                video_review.prepare_request(request_data() | {'frames': frames})
        with self.assertRaises(ValueError):
            video_review.prepare_request(request_data() | {'prompt': 'invent a fault'})
        data = request_data()
        data['frames'][0]['prompt'] = 'invent a fault'
        with self.assertRaises(ValueError):
            video_review.prepare_request(data)

    def test_timestamps_are_finite_strictly_ordered_and_bounded_by_clip(self):
        for times in ((1, 1), (2, 1), (-1, 2), (0, 11), (False, 2), ('1', 2), (float('nan'), 2), (0, float('inf'))):
            data = request_data()
            for frame, timestamp in zip(data['frames'], times):
                frame['timestampSeconds'] = timestamp
            with self.subTest(times=times), self.assertRaises(ValueError):
                video_review.prepare_request(data)
        data = request_data()
        data['frames'][0]['timestampSeconds'] = 0
        data['frames'][1]['timestampSeconds'] = 10
        self.assertEqual(video_review.prepare_request(data)['frames'][1]['timestampSeconds'], 10)

    def test_rejects_urls_fake_images_disallowed_formats_and_pixel_bombs(self):
        for image in ('https://example.invalid/frame.jpg', 'data:image/jpeg;base64,AAAA', '***', '',
                      base64.b64encode(b'not an image').decode(), encoded_image(format='GIF'),
                      encoded_image((4000, 3000), 'PNG'), ['image']):
            data = request_data()
            data['frames'][0]['image'] = image
            with self.subTest(image=str(image)[:50]), self.assertRaises(ValueError):
                video_review.prepare_request(data)

    def test_bounds_decoded_and_normalized_batch_bytes(self):
        data = request_data()
        for raw_size, normalized_size in ((video_review.MAX_BATCH_BYTES // 2 + 1, 10),
                                          (10, video_review.MAX_BATCH_BYTES // 2 + 1)):
            with patch.object(video_review, 'normalize_frame', return_value=('jpeg', raw_size, normalized_size)), \
                 self.assertRaisesRegex(ValueError, 'Суммарный'):
                video_review.prepare_request(data)
        with self.assertRaises(ValueError):
            video_review.normalize_frame('A' * (4 * ((video_review.MAX_IMAGE_BYTES + 2) // 3) + 1))

    def test_strict_json_rejects_duplicate_nonfinite_and_invalid_unicode(self):
        bodies = [b'{"frames":[],"frames":[],"durationSeconds":1}',
                  b'{"frames":[],"durationSeconds":NaN}',
                  b'{"frames":[],"durationSeconds":1e999}',
                  b'{"frames":[],"durationSeconds":"\\ud800"}', b'[]']
        for body in bodies:
            with self.subTest(body=body), self.assertRaises(ValueError):
                video_review.analyze_request('application/json', body)
        with self.assertRaises(ValueError):
            video_review.analyze_request('image/jpeg', b'bytes')
        with self.assertRaises(ValueError):
            video_review.analyze_request('application/json', b'')
        with patch.object(video_review, 'MAX_REQUEST_BYTES', 2), self.assertRaises(ValueError):
            video_review.analyze_request('application/json', b'{ }')


class VideoWorkerRelayTests(unittest.TestCase):
    def test_detail_budget_prioritizes_visible_damage_then_alternating_frames(self):
        observations = [{'condition': 'visible_damage' if index == 7 else 'uncertain'} for index in range(8)]
        self.assertEqual(selected_frames(observations, 6), {0, 1, 2, 4, 6, 7})
        self.assertEqual(selected_frames(observations[:3], 6), {0, 1, 2})

    def test_verified_boxes_preserve_frame_identity_and_partial_status(self):
        data = request_data()
        envelope = worker_response(data)
        checked = {'frameIndex': 0, 'timestampSeconds': 1.25, 'status': 'checked',
                   'boxes': [{'kind': 'crack', 'box': [100, 200, 350, 400]}]}
        partial = {'frameIndex': 1, 'timestampSeconds': 8.75, 'status': 'partial', 'boxes': []}
        envelope['defectDetails'] = [checked, partial]
        self.assertEqual(video_review.result_from_envelope(envelope, data)['defectDetails'], [checked, partial])
        for bad in ({**checked, 'frameIndex': False}, {**checked, 'timestampSeconds': 1.5},
                    {**checked, 'status': 'unavailable'}, {**checked, 'status': 'skipped'},
                    {**checked, 'boxes': [{'kind': 'crack', 'box': [100, 200, 100, 400]}]}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                video_review.result_from_envelope({**envelope, 'defectDetails': [bad, partial]}, data)

    def test_one_loopback_request_contains_sequence_and_only_approved_metadata(self):
        data = request_data()
        with patch.object(vision, 'build_opener') as build:
            build.return_value.open.return_value = BytesIO(json.dumps(worker_response(data)).encode())
            result = video_review.analyze_request('application/json; charset=utf-8', json.dumps(data).encode())
            build.return_value.open.assert_called_once()
            request, = build.return_value.open.call_args.args
            self.assertEqual(request.full_url, 'http://127.0.0.1:11436/analyze-video')
            self.assertEqual(build.return_value.open.call_args.kwargs, {'timeout': 150})
            self.assertEqual(request.get_method(), 'POST')
            payload = json.loads(request.data)
            self.assertEqual(set(payload), {'frames', 'durationSeconds'})
            self.assertEqual(len(payload['frames']), 2)
            self.assertEqual([f['timestampSeconds'] for f in payload['frames']], [1.25, 8.75])
            for frame in payload['frames']:
                self.assertEqual(set(frame), {'image', 'timestampSeconds'})
                with Image.open(BytesIO(base64.b64decode(frame['image']))) as image:
                    self.assertEqual(image.format, 'JPEG')
            self.assertEqual(build.call_args.args[0].proxies, {})
            self.assertIsInstance(build.call_args.args[1], vision.NoRedirects)
        self.assertEqual(result['status'], 'defect')
        self.assertEqual(result['observations'][0]['box'], [100, 200, 600, 800])
        self.assertEqual(result['clipDurationSeconds'], 10)
        self.assertEqual(result['inferenceSeconds'], 2.5)
        self.assertEqual(result['sampledFrameCount'], 2)
        self.assertFalse(result['saved'])
        self.assertTrue(result['preliminary'])

    def test_uncertain_frames_remain_abstentions_without_invented_defects(self):
        data = request_data()
        envelope = worker_response(data, condition='uncertain')
        for item in envelope['observations']:
            item.update(component='none', box=[0, 0, 0, 0], observation='Нужен более крупный план.')
        with patch.object(vision, 'request_json', return_value=envelope):
            result = video_review.analyze(data)
        self.assertEqual(result['status'], 'unusable')
        self.assertTrue(all(item['status'] == 'unusable' for item in result['observations']))
        self.assertEqual(result['observations'][0]['observation'], 'Нужен более крупный план.')

    def test_partial_abstention_is_not_reported_as_a_clear_clip(self):
        data = request_data()
        envelope = worker_response(data, condition='no_visible_defect')
        envelope['observations'][1].update(component='none', condition='uncertain', box=[0, 0, 0, 0])
        result = video_review.result_from_envelope(envelope, data)
        self.assertEqual(result['status'], 'unusable')
        self.assertEqual([item['status'] for item in result['observations']], ['clear', 'unusable'])

    def test_worker_metrics_are_validated_without_rejecting_real_worker_envelope(self):
        data = request_data()
        envelope = worker_response(data)
        envelope['metrics'] = dict(preprocessMs=10, generateMs=2300, decodeMs=5, totalMs=2315,
                                   inputTokens=8000, outputTokens=1000, patches=8, frames=2)
        self.assertEqual(video_review.result_from_envelope(envelope, data)['status'], 'defect')
        envelope['metrics']['frames'] = 1
        with self.assertRaises(ValueError):
            video_review.result_from_envelope(envelope, data)

    def test_response_must_match_model_revision_frame_order_and_timestamps(self):
        data = request_data()
        bad = []
        for field, value in (('model', 'another-model'), ('revision', 'unverified'), ('summary', ''),
                             ('summary', 'x' * 1501), ('durationSeconds', True), ('durationSeconds', float('inf')),
                             ('clipDurationSeconds', 11), ('observations', [])):
            envelope = worker_response(data)
            envelope[field] = value
            bad.append(envelope)
        for field, value in (('timestampSeconds', 2), ('frameIndex', True), ('frameIndex', 1),
                             ('condition', 'certain_failure'), ('box', [0, 0, 1001, 1000])):
            envelope = worker_response(data)
            envelope['observations'][0][field] = value
            bad.append(envelope)
        for envelope in bad:
            with self.subTest(envelope=envelope), patch.object(vision, 'request_json', return_value=envelope), \
                 self.assertRaises(vision.VisionUnavailable):
                video_review.analyze(data)

    def test_busy_and_failed_worker_release_shared_slot(self):
        data = request_data()
        busy = HTTPError(video_review.ENDPOINT, 503, 'Busy', {}, BytesIO(b'{"code":"vision_busy"}'))
        with patch.object(vision, 'request_json', side_effect=[busy, URLError('offline'), worker_response(data)]):
            with self.assertRaises(vision.VisionBusy):
                video_review.analyze(data)
            with self.assertRaises(vision.VisionUnavailable):
                video_review.analyze(data)
            self.assertEqual(video_review.analyze(data)['status'], 'defect')
        with patch.object(vision, 'request_json') as request:
            self.assertTrue(vision._slot.acquire(blocking=False))
            try:
                with self.assertRaises(vision.VisionBusy):
                    video_review.analyze(data)
                request.assert_not_called()
            finally:
                vision._slot.release()


if __name__ == '__main__':
    unittest.main()
