import base64
from contextlib import closing
from io import BytesIO
import json
import threading
import time
import unittest
import uuid
from unittest.mock import patch

from server import video_review, vision
from server.app import connect
from tests import test_api as api_helpers


class QueueApiTest(unittest.TestCase):
    public_url = api_helpers.ApiTest.public_url
    serial = api_helpers.ApiTest.serial
    setUp = api_helpers.ApiTest.setUp
    request = api_helpers.ApiTest.request
    json_request = api_helpers.ApiTest.json_request

    def fixture(self):
        from PIL import Image
        self.handler.keywords.update(vision_enabled=True, vision_backend='internvl3')
        jpeg = BytesIO()
        Image.new('RGB', (32, 24), 'red').save(jpeg, 'JPEG')
        self.jpeg = jpeg.getvalue()
        self.client, self.request_id = uuid.uuid4().hex, str(uuid.uuid4())
        self.headers = {'Content-Type': 'multipart/form-data; boundary=frame', 'Origin': self.public_url,
                        'Prefer': 'respond-async', 'X-Analysis-Client': self.client,
                        'X-Analysis-Request': self.request_id}
        self.body = (b'--frame\r\nContent-Disposition: form-data; name="file"; filename="frame.jpg"\r\n'
                     b'Content-Type: image/jpeg\r\n\r\n' + self.jpeg + b'\r\n--frame--\r\n')
        observation = {'component': 'pump', 'condition': 'corrosion',
                       'box': [100, 100, 900, 900], 'observation': 'На корпусе видны следы коррозии.'}
        self.result = vision.result_from_observation(observation, model=vision.INTERNVL_MODEL)

    def wait_job(self, job, status):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            value = self.json_request('GET', '/api/analysis-jobs/' + job['id'],
                                      headers={'X-Analysis-Client': self.client})['job']
            if value['status'] == status:
                return value
            time.sleep(.005)
        self.fail(f'Expected {status}, got {value}')

    def evidence_count(self):
        with closing(connect(self.handler.keywords['database'])) as connection:
            return connection.execute('SELECT COUNT(*) FROM analysis_evidence').fetchone()[0]

    def test_async_response_releases_http_thread_and_idempotent_retry_stores_once(self):
        self.fixture()
        entered, gate = threading.Event(), threading.Event()
        self.addCleanup(gate.set)
        def slow(*_, **__):
            entered.set(); gate.wait(3); return self.result
        with patch.object(vision, 'analyze', side_effect=slow) as run:
            job = self.json_request('POST', '/analyze', 202, body=self.body, headers=self.headers)['job']
            self.assertTrue(entered.wait(1))
            self.assertEqual(self.json_request('GET', '/api/health')['status'], 'ok')
            retry = self.json_request('POST', '/analyze', 202, body=self.body, headers=self.headers)['job']
            self.assertEqual(retry['id'], job['id'])
            self.assertEqual(self.evidence_count(), 0)
            gate.set()
            done = self.wait_job(job, 'succeeded')
            run.assert_called_once()
        self.assertIn('analysisId', done['result'])
        self.assertIn('imageUrl', done['result'])
        self.assertEqual(done['result']['media']['captureSource'], 'camera')
        self.assertEqual(self.evidence_count(), 1)
        changed = self.body.replace(b'\r\n--frame--\r\n',
            b'\r\n--frame\r\nContent-Disposition: form-data; name="profile"\r\n\r\nfast\r\n--frame--\r\n')
        failure = self.json_request('POST', '/analyze', 409, body=changed, headers=self.headers)
        self.assertEqual(failure['code'], 'analysis_request_conflict')

    def test_running_cancel_does_not_persist_and_foreign_owner_cannot_observe(self):
        self.fixture()
        entered, gate = threading.Event(), threading.Event()
        self.addCleanup(gate.set)
        def slow(*_, **__):
            entered.set(); gate.wait(3); return self.result
        with patch.object(vision, 'analyze', side_effect=slow):
            job = self.json_request('POST', '/analyze', 202, body=self.body, headers=self.headers)['job']
            self.assertTrue(entered.wait(1))
            for method, suffix in [('GET', ''), ('POST', '/cancel')]:
                self.json_request(method, '/api/analysis-jobs/' + job['id'] + suffix, 404,
                                  headers={'X-Analysis-Client': uuid.uuid4().hex})
            cancelled = self.json_request('POST', '/api/analysis-jobs/' + job['id'] + '/cancel',
                                         headers={'X-Analysis-Client': self.client})['job']
            self.assertEqual(cancelled['status'], 'cancelling')
            legacy_headers = {'Content-Type': self.headers['Content-Type']}
            busy = self.json_request('POST', '/analyze', 503, body=self.body, headers=legacy_headers)
            self.assertEqual(busy['code'], 'vision_busy')
            gate.set(); self.wait_job(job, 'cancelled')
        self.assertEqual(self.evidence_count(), 0)

    def test_cancel_before_submission_blocks_delayed_post_and_rejects_cross_origin(self):
        self.fixture()
        route = '/api/analysis-jobs/by-request/' + self.request_id + '/cancel'
        self.json_request('POST', route, 403,
                          headers={'X-Analysis-Client': self.client, 'Origin': 'https://foreign.test'})
        stopped = self.json_request('POST', route, headers={'X-Analysis-Client': self.client})['job']
        with patch.object(vision, 'analyze') as analyze:
            job = self.json_request('POST', '/analyze', 202, body=self.body, headers=self.headers)['job']
            self.assertEqual((job['id'], job['status']), (stopped['id'], 'cancelled'))
            analyze.assert_not_called()
        self.assertEqual(self.evidence_count(), 0)

    def test_video_is_validated_before_queue_and_shares_photo_fifo(self):
        self.fixture()
        entered, gate = threading.Event(), threading.Event()
        self.addCleanup(gate.set)
        def photo(*_, **__):
            entered.set(); gate.wait(3); return self.result
        video = {'frames': [{'image': base64.b64encode(self.jpeg).decode(), 'timestampSeconds': 0}],
                 'durationSeconds': 1}
        video_headers = {**self.headers, 'Content-Type': 'application/json', 'X-Analysis-Request': uuid.uuid4().hex}
        output = {'summary': 'Виден корпус насоса.', 'observations': [], 'source': 'local_vision'}
        with patch.object(vision, 'analyze', side_effect=photo), \
                patch.object(video_review, '_analyze_prepared', return_value=output) as analyze:
            first = self.json_request('POST', '/analyze', 202, body=self.body, headers=self.headers)['job']
            self.assertTrue(entered.wait(1))
            second = self.json_request('POST', '/api/video-review', 202, body=video, headers=video_headers)['job']
            self.assertEqual((second['type'], second['status'], second['position']), ('video', 'queued', 1))
            analyze.assert_not_called()
            gate.set(); self.wait_job(first, 'succeeded')
            self.assertEqual(self.wait_job(second, 'succeeded')['result'], output)
            self.assertEqual(analyze.call_args.args[0]['frames'][0]['timestampSeconds'], 0)
        self.assertEqual(self.evidence_count(), 1)

    def test_review_snapshot_survives_reload_bound_to_exact_frame(self):
        from tests.test_video_review import encoded_image, request_data, worker_response
        self.fixture()
        video = request_data()
        video['frames'][1]['image'] = encoded_image((48, 32), 'PNG')
        envelope = worker_response(video)
        envelope['observations'][0]['condition'] = 'no_visible_defect'
        headers = {**self.headers, 'Content-Type': 'application/json'}
        with patch.object(vision, 'request_json', return_value=envelope) as worker:
            job = self.json_request('POST', '/api/video-review', 202, body=video, headers=headers)['job']
            result = self.wait_job(job, 'succeeded')['result']
            frame = result['representativeFrame']
            self.assertEqual(frame['media'], {'captureSource': 'uploaded_video', 'frameIndex': 1,
                                               'videoTimeSeconds': 8.75})
            self.assertEqual(frame['status'], 'defect')
            status, _, jpeg = self.request('GET', frame['imageUrl'])
            self.assertEqual(status, 200)
            exact_input = worker.call_args.args[1]['frames'][1]['image']
            self.assertEqual(jpeg, base64.b64decode(exact_input))
            retry = self.json_request('POST', '/api/video-review', 202, body=video, headers=headers)['job']
            self.assertEqual(retry['result']['representativeFrame']['analysisId'], frame['analysisId'])
            worker.assert_called_once()
        self.assertEqual(self.evidence_count(), 1)
        self.assertEqual(self.json_request('GET', '/api/history?serial=P-102')['records'], [])
        inspection = {'serial': 'P-102', 'telemetrySource': {'kind': 'none'}, 'confirmedVision': {},
                      'requestId': 'clip-once', 'analysisId': frame['analysisId']}
        self.json_request('POST', '/api/inspections', 400, body={**inspection, 'videoReview': result})
        record = self.json_request('POST', '/api/inspections', 201, body=inspection)['record']
        self.assertFalse(self.json_request('POST', '/api/inspections', body=inspection)['created'])
        loaded = self.json_request('GET', '/api/history?serial=P-102')['records']
        self.assertEqual(loaded, [record])
        snapshot = record['evidence']['result']['videoReview']
        self.assertEqual(snapshot['summary'], envelope['summary'])
        self.assertEqual([(row['condition'], row['timestampSeconds']) for row in snapshot['observations']],
                         [('no_visible_defect', 1.25), ('corrosion', 8.75)])
        self.assertEqual(snapshot, frame['videoReview'])

    def test_bad_tokens_nested_json_and_upload_saturation_never_enqueue(self):
        self.fixture()
        with patch.object(self.server.analysis_queue, 'submit') as submit:
            for bad in ({'X-Analysis-Client': 'guessable'}, {'X-Analysis-Request': 'not-a-uuid'}):
                self.json_request('POST', '/analyze', 400, body=self.body, headers={**self.headers, **bad})
            self.json_request('POST', '/api/video-review', 400,
                              body=b'['*10000+b'0'+b']'*10000,
                              headers={**self.headers, 'Content-Type': 'application/json'})
            self.json_request('POST', '/api/video-review', 400, body={'frames': [], 'durationSeconds': 1},
                              headers={**self.headers, 'Content-Type': 'application/json'})
            self.server.analysis_upload_slots.acquire()
            self.server.analysis_upload_slots.acquire()
            try:
                payload = self.json_request('POST', '/analyze', 503, body=self.body, headers=self.headers)
                self.assertEqual(payload['code'], 'analysis_upload_busy')
            finally:
                self.server.analysis_upload_slots.release(); self.server.analysis_upload_slots.release()
            submit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
