from functools import partial
from http.client import HTTPConnection
from io import BytesIO
import base64
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch
import uuid

from PIL import Image
from server import app, vision


class ResultCacheApiTests(unittest.TestCase):
    def test_cached_video_bypasses_inference_and_creates_fresh_evidence(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / 'test.sqlite3'
            app.initialize(database)
            handler = partial(app.Handler, database=database, public_url='https://cache.test',
                              vision_enabled=True, vision_backend='internvl3', vision_knowledge=False)
            with app.BoundedHTTPServer(('127.0.0.1', 0), handler,
                                       result_cache_directory=root / 'cache') as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    image = BytesIO()
                    Image.new('RGB', (32, 24), 'blue').save(image, format='JPEG')
                    body = json.dumps({'frames': [{'image': base64.b64encode(image.getvalue()).decode(),
                                                  'timestampSeconds': 1}], 'durationSeconds': 3}).encode()
                    def upload():
                        connection = HTTPConnection('127.0.0.1', server.server_port, timeout=3)
                        try:
                            connection.request('POST', '/api/video-review', body,
                                               {'Content-Type': 'application/json'})
                            response = connection.getresponse()
                            return response.status, response.getheader('X-Analysis-Cache'), json.loads(response.read())
                        finally:
                            connection.close()
                    result = {'status': 'clear', 'model': vision.model_spec().model_id,
                              'observations': [], 'summary': 'Видимых повреждений нет.'}
                    with patch.object(app.video_review, '_analyze_prepared', return_value=result) as infer, \
                         patch.object(app.video_review, 'inspection_frame',
                                      return_value=(image.getvalue(), {'media': {'captureSource': 'uploaded_video'}})), \
                         patch.object(app.inspections, 'store_analysis',
                                      side_effect=[{'analysisId': 'first'}, {'analysisId': 'second'}]) as saved:
                        first_status, _, first = upload()
                        with server.analysis_queue.legacy():
                            second_status, cache_header, second = upload()
                    self.assertEqual((first_status, second_status, cache_header), (200, 200, 'hit'))
                    self.assertEqual(infer.call_count, 1)
                    self.assertEqual(saved.call_count, 2)
                    self.assertEqual(first['representativeFrame']['analysisId'], 'first')
                    self.assertEqual(second['representativeFrame']['analysisId'], 'second')
                finally:
                    server.shutdown()
                    thread.join(timeout=3)

    def test_cached_photo_bypasses_busy_queue_and_creates_new_evidence(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / 'test.sqlite3'
            app.initialize(database)
            handler = partial(app.Handler, database=database, public_url='https://cache.test',
                              vision_enabled=True, vision_backend='internvl3', vision_knowledge=False)
            with app.BoundedHTTPServer(('127.0.0.1', 0), handler,
                                       result_cache_directory=root / 'cache') as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    image = BytesIO()
                    Image.new('RGB', (32, 24), 'blue').save(image, format='JPEG')
                    def upload(source, timestamp=None, asynchronous=False):
                        boundary = 'cache-api-test'
                        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="frame.jpg"\r\n'
                                'Content-Type: image/jpeg\r\n\r\n').encode() + image.getvalue()
                        fields = {'profile': 'standard', 'captureSource': source}
                        if timestamp is not None:
                            fields['videoTimeSeconds'] = str(timestamp)
                        for name, value in fields.items():
                            body += f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}'.encode()
                        body += f'\r\n--{boundary}--\r\n'.encode()
                        headers = {'Content-Type': 'multipart/form-data; boundary=' + boundary}
                        if asynchronous:
                            headers.update({'Prefer': 'respond-async', 'X-Analysis-Client': str(uuid.uuid4()),
                                            'X-Analysis-Request': str(uuid.uuid4())})
                        connection = HTTPConnection('127.0.0.1', server.server_port, timeout=3)
                        try:
                            connection.request('POST', '/analyze', body, headers)
                            response = connection.getresponse()
                            return response.status, response.getheader('X-Analysis-Cache'), json.loads(response.read())
                        finally:
                            connection.close()
                    observation = vision.result_from_observation({'component': 'pump', 'condition': 'corrosion',
                                'box': [100, 100, 900, 900], 'observation': 'Видна ржавчина.'}, model=vision.model_spec().model_id)
                    with patch.object(vision, 'analyze', return_value=observation) as infer:
                        status, _, first = upload('uploaded_photo')
                        self.assertEqual(status, 200)
                        with server.analysis_queue.legacy():
                            status, cache_header, second = upload('uploaded_video', 4, asynchronous=True)
                        self.assertEqual(status, 200)
                        self.assertEqual(cache_header, 'hit')
                        self.assertEqual(infer.call_count, 1)
                        self.assertNotEqual(first['analysisId'], second['analysisId'])
                        self.assertEqual(second['media']['videoTimeSeconds'], 4)
                finally:
                    server.shutdown()
                    thread.join(timeout=3)


if __name__ == '__main__':
    unittest.main()
