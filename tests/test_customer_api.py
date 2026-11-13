import json
import tempfile
import threading
import unittest
from functools import partial
from http.client import HTTPConnection
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from server import app
from server.diagnostics import DEFAULT_THRESHOLDS, RULE_REQUIREMENTS


class CustomerApiTest(unittest.TestCase):
    image_id = '1' * 64
    unindexed_id = '2' * 64

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.customer = self.root / 'customer'
        self.previews = self.customer / 'previews'
        self.previews.mkdir(parents=True)
        handler = partial(app.Handler, database=self.root / 'unused.sqlite3',
                          public_url='https://cyber102.example.test', customer_root=self.customer)
        self.server = app.BoundedHTTPServer(('127.0.0.1', 0), handler)
        self.addCleanup(self.server.server_close)
        thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01})
        thread.start()
        self.addCleanup(thread.join, 3)
        self.addCleanup(self.server.shutdown)

    def write_dataset(self, name, data):
        (self.customer / f'{name}.json').write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')

    def get(self, endpoint, expected=200):
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            connection.request('GET', f'/api/customer/{endpoint}')
            response = connection.getresponse()
            body = response.read()
            self.assertEqual(response.status, expected, body[:150])
            self.assertEqual(response.getheader('Cache-Control'), 'no-store')
            self.assertEqual(response.getheader('X-Content-Type-Options'), 'nosniff')
            return response.headers, body
        finally:
            connection.close()

    def get_json(self, endpoint, expected=200):
        headers, body = self.get(endpoint, expected)
        self.assertEqual(headers.get_content_type(), 'application/json')
        return json.loads(body)

    def test_materials_and_sensor_data_preserve_provenance_without_running_diagnosis(self):
        materials = {'source': 'customer_examples', 'images': [{
            'id': self.image_id, 'source': 'customer_provided', 'annotationStatus': 'unreviewed',
            'equipmentSerial': None, 'defectLabel': None,
        }], 'documents': []}
        sensors = {'source': 'customer_sensor_examples', 'samples': [{
            'serial': None, 'bearing_temperature_c': 96,
            'provenance': {'sheet': 'Термометрия', 'row': 31},
        }]}
        report = {'source': 'customer_sensor_import', 'rows': 1}
        for endpoint, dataset in (('materials', materials), ('sensors', sensors), ('sensor-import-report', report)):
            self.write_dataset(endpoint, dataset)
        with patch.object(app.assessment, 'diagnose') as evaluate:
            self.assertEqual(self.get_json('materials'), materials)
            self.assertEqual(self.get_json('sensors'), sensors)
            self.assertEqual(self.get_json('sensor-import-report'), report)
            evaluate.assert_not_called()
        self.assertFalse((self.root / 'unused.sqlite3').exists())

    def test_rule_catalog_includes_every_field_and_demo_threshold(self):
        catalog = self.get_json('rules')
        self.assertEqual(catalog['profile']['source'], 'demo')
        self.assertEqual(catalog['thresholds'], json.loads(json.dumps(DEFAULT_THRESHOLDS)))
        definitions = {
            f'{source}.{field["key"]}': field
            for source, fields in catalog['fields'].items()
            for field in fields
        }
        threshold_keys = {f'thresholds.{key}' for key in catalog['thresholds']}
        self.assertEqual(len(catalog['rules']), 12)
        self.assertEqual({rule['id'] for rule in catalog['rules']}, set(RULE_REQUIREMENTS))
        for rule in catalog['rules']:
            with self.subTest(rule=rule['id']):
                self.assertTrue(rule['title'])
                self.assertTrue(rule['note'])
                self.assertEqual(rule['requirements'], [list(group) for group in RULE_REQUIREMENTS[rule['id']]])
                for group in rule['requirements']:
                    self.assertTrue(group)
                    self.assertLessEqual(set(group), definitions.keys() | threshold_keys)
        self.assertEqual(definitions['vision.gauge_pressure']['unit'], 'МПа')
        self.assertEqual(definitions['telemetry.sound_sensor_present']['type'], 'boolean')

    def test_missing_data_and_unknown_endpoints_return_404(self):
        for endpoint in ('materials', 'sensors', 'sensor-import-report', 'anything', f'images/{self.image_id}'):
            with self.subTest(endpoint=endpoint):
                self.assertTrue(self.get_json(endpoint, 404)['error'])

    def test_indexed_preview_is_jpeg_and_unindexed_files_are_not_served(self):
        from PIL import Image

        self.write_dataset('materials', {'images': [{'id': self.image_id}]})
        image_file = self.previews / f'{self.image_id}.jpg'
        Image.new('RGB', (16, 10), (12, 34, 56)).save(image_file, 'JPEG')
        (self.previews / f'{self.unindexed_id}.jpg').write_bytes(image_file.read_bytes())
        headers, body = self.get(f'images/{self.image_id}')
        self.assertEqual(headers.get_content_type(), 'image/jpeg')
        self.assertEqual(body, image_file.read_bytes())
        with Image.open(BytesIO(body)) as image:
            self.assertEqual((image.format, image.size), ('JPEG', (16, 10)))
        self.assertTrue(self.get_json(f'images/{self.unindexed_id}', 404)['error'])
        image_file.unlink()
        self.assertTrue(self.get_json(f'images/{self.image_id}', 404)['error'])

    def test_invalid_ids_and_path_traversal_are_rejected(self):
        self.write_dataset('materials', {'images': [{'id': self.image_id}]})
        for image_id in ('', 'A' * 64, 'a' * 63, f'{self.image_id}.jpg',
                         '../materials.json', '%2E%2E%2Fmaterials.json', f'{self.image_id}/../../materials.json'):
            with self.subTest(image_id=image_id):
                self.assertTrue(self.get_json(f'images/{image_id}', 404)['error'])

    def test_indexed_preview_symlink_to_external_file_is_rejected(self):
        self.write_dataset('materials', {'images': [{'id': self.image_id}]})
        outside = self.root / 'private.jpg'
        outside.write_bytes(b'private file outside customer dataset')
        try:
            (self.previews / f'{self.image_id}.jpg').symlink_to(outside)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f'Creating symlinks is unavailable: {error}')
        self.assertTrue(self.get_json(f'images/{self.image_id}', 404)['error'])

    def test_linked_preview_directory_outside_customer_root_is_rejected(self):
        self.write_dataset('materials', {'images': [{'id': self.image_id}]})
        self.previews.rmdir()
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / f'{self.image_id}.jpg').write_bytes(b'private file outside customer dataset')
        try:
            self.previews.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f'Creating symlinks is unavailable: {error}')
        self.assertTrue(self.get_json(f'images/{self.image_id}', 404)['error'])

    def test_dataset_symlink_to_external_json_is_rejected(self):
        outside = self.root / 'private.json'
        outside.write_text('{"private": true}', encoding='utf-8')
        try:
            (self.customer / 'sensors.json').symlink_to(outside)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f'Creating symlinks is unavailable: {error}')
        self.assertTrue(self.get_json('sensors', 404)['error'])

    def test_corrupt_dataset_and_image_index_return_a_controlled_error(self):
        (self.customer / 'sensors.json').write_text('{', encoding='utf-8')
        self.assertTrue(self.get_json('sensors', 503)['error'])
        self.write_dataset('sensors', [])
        self.assertTrue(self.get_json('sensors', 503)['error'])
        for images in (None, [None]):
            self.write_dataset('materials', {'images': images})
            self.assertTrue(self.get_json(f'images/{self.image_id}', 503)['error'])


if __name__ == '__main__':
    unittest.main()
