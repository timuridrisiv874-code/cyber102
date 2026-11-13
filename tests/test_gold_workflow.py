"""Functional boundaries for guided, evidence-backed inspections."""
import json
import sqlite3
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from server import assessment, customer, inspections, vision
from server.app import initialize
from server.demo_data import PASSPORT


class GoldWorkflowTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / 'gold.sqlite3'
        initialize(self.database, customer_root=customer.ROOT)
        self.connection = sqlite3.connect(self.database)
        self.addCleanup(self.connection.close)
        with self.connection:
            self.analysis = inspections.store_analysis(self.connection, b'normalized-jpeg', {
                'source': 'local_vision', 'status': 'defect', 'model': 'test-local',
                'media': {'captureSource': 'uploaded_video', 'videoTimeSeconds': 4.5, 'declaredBy': 'client'}})

    def payload(self, **overrides):
        return {'serial': 'P-102', 'analysisId': self.analysis['analysisId'], 'confirmedVision': {},
                'telemetrySource': {'kind': 'manual', 'values': {'vibration': 'rising', 'temperature': 'rising'}},
                'selectedComponent': 'pump', 'requestId': 'gold-test-one', **overrides}

    def test_next_measurement_closes_rule_and_preserves_manual_source(self):
        data = self.payload()
        initial = inspections.preview(self.connection, data)
        self.assertEqual(initial['findings'], [])
        self.assertEqual(initial['guidance']['nextChecks'][0]['field'], 'telemetry.current')
        data['telemetrySource']['values']['current'] = 'normal'
        result = inspections.preview(self.connection, data)
        self.assertEqual([r['id'] for r in result['findings']], ['bearing_lubrication'])
        self.assertEqual(result['provenance']['telemetry'], 'manual')
        self.assertEqual(result['provenance']['captureSource'], 'uploaded_video')
        with self.connection:
            record, created = inspections.save(self.connection, data)
        self.assertTrue(created)
        self.assertEqual(record['provenance']['guidance'], result['guidance'])
        self.assertEqual(record['evidence']['result']['media']['videoTimeSeconds'], 4.5)

    def test_real_passport_references_do_not_activate_demo_rules(self):
        data = self.payload(serial='21031004-1', selectedComponent='gearbox', confirmedVision={'guard_issue': True})
        data['telemetrySource']['values']['current'] = 'normal'
        result = inspections.preview(self.connection, data)
        self.assertEqual(result['findings'], [])
        self.assertFalse(result['guidance']['applicable'])
        reference = result['manufacturer']['recommendations'][0]
        self.assertEqual(reference['target']['kind'], 'gearbox')
        self.assertEqual(reference['target']['serial'], 'SAL023020084_001002')
        self.assertEqual(reference['sources'][0]['page'], 1)
        with self.connection:
            record, _ = inspections.save(self.connection, data)
        self.assertEqual(record['provenance']['manufacturer'], result['manufacturer'])
        with patch.object(assessment, 'references', side_effect=AssertionError('must use saved snapshot')):
            with self.connection:
                again, created = inspections.save(self.connection, data)
        self.assertFalse(created)
        self.assertEqual(record, again)

    def test_registered_equipment_persists_existing_records(self):
        count = self.connection.execute('SELECT count(*) FROM pumps').fetchone()[0]
        self.assertEqual(count, 5)
        initialize(self.database, customer_root=customer.ROOT)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM pumps').fetchone()[0], 5)
        self.assertEqual(json.loads(self.connection.execute("SELECT passport_json FROM pumps WHERE serial='P-102'").fetchone()[0]), PASSPORT)

    def test_manual_values_are_strict_and_unknown_is_not_normal(self):
        for values in ({'current': 'unknown'}, {'hours': True}, {'hours': -1}, {'hours': float('inf')},
                       {'discharge_pressure': '0.8'}, {'unexpected': 2}, {'gravel_noise': 1},
                       {'previous_hours': 100, 'hours': 99}, [], {'temperature': []}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                inspections.validate_telemetry(values)
        self.assertEqual(inspections.validate_telemetry({'current': None}), {})
        self.assertEqual(inspections.validate_telemetry({'gravel_noise': False, 'discharge_pressure': 0}),
                         {'gravel_noise': False, 'discharge_pressure': 0})
        with self.assertRaises(ValueError):
            inspections.preview(self.connection, self.payload(selectedComponent='unknown'))


class MediaMetadataTest(unittest.TestCase):
    @staticmethod
    def form(extras):
        from PIL import Image
        image = BytesIO()
        Image.new('RGB', (12, 12), 'blue').save(image, 'PNG')
        parts = [('file', image.getvalue()), *extras]
        # Build each form part explicitly so binary payloads stay unchanged.
        body = b''
        for name, value in parts:
            body += b'--test\r\nContent-Disposition: form-data; name="' + name.encode() + b'"\r\n\r\n'
            body += value.encode() if isinstance(value, str) else value
            body += b'\r\n'
        return 'multipart/form-data; boundary=test', body + b'--test--\r\n'

    def test_camera_photo_and_video_metadata(self):
        for extras, expected in (([], {'captureSource': 'camera'}),
                                 ([('captureSource', 'uploaded_photo')], {'captureSource': 'uploaded_photo'}),
                                 ([('captureSource', 'uploaded_video'), ('videoTimeSeconds', '4.25')],
                                  {'captureSource': 'uploaded_video', 'videoTimeSeconds': 4.25})):
            normalized, profile, metadata = vision.extract_request(*self.form(extras), include_metadata=True)
            self.assertTrue(normalized.startswith(b'\xff\xd8'))
            self.assertEqual(profile, 'standard')
            self.assertEqual(metadata, {**expected, 'declaredBy': 'client'})

    def test_invalid_or_duplicate_metadata_rejected(self):
        for extras in ([('captureSource', 'fake')], [('videoTimeSeconds', '1')],
                       [('captureSource', 'uploaded_video')],
                       [('captureSource', 'camera'), ('captureSource', 'camera')],
                       *[[('captureSource', 'uploaded_video'), ('videoTimeSeconds', value)]
                         for value in ('NaN', 'Infinity', '-1', '121', 'nope')]):
            with self.subTest(extras=extras), self.assertRaises(ValueError):
                vision.extract_request(*self.form(extras), include_metadata=True)
