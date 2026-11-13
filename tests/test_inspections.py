import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from functools import partial
from http.client import HTTPConnection
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from server import history, inspections, vision
from server.app import BoundedHTTPServer, Handler, initialize


RESULT = {
    'status': 'defect', 'source': 'local_vision', 'model': 'test-local-model',
    'component': 'Корпус насоса', 'finding': 'Возможная коррозия',
    'box': {'x': .1, 'y': .2, 'width': .4, 'height': .5}, 'observation': 'Ржавчина',
}


class InspectionsTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / 'inspections.sqlite3'
        initialize(self.database)
        self.connection = sqlite3.connect(self.database)
        self.addCleanup(self.connection.close)
        self.connection.execute('PRAGMA foreign_keys = ON')
        with self.connection:
            self.analysis = inspections.store_analysis(self.connection, b'normalized-jpeg', RESULT)

    def payload(self, **values):
        return {'serial': 'P-102', 'analysisId': self.analysis['analysisId'],
                'telemetrySource': {'kind': 'none'}, 'confirmedVision': {},
                'requestId': 'inspection-one', **values}

    def test_camera_corrosion_does_not_become_foundation_or_seal_diagnosis(self):
        preview = inspections.preview(self.connection, self.payload())
        self.assertEqual(preview['telemetry'], {})
        self.assertEqual(preview['findings'], [])
        self.assertIsNone(preview['vision']['foundation_issue'])
        self.assertIsNone(preview['vision']['seal_leak'])
        self.assertEqual(preview['analysis']['result'], RESULT)
        self.assertEqual(preview['analysis']['imageSha256'], hashlib.sha256(b'normalized-jpeg').hexdigest())
        self.assertEqual(history.list_records(self.connection, 'P-102'), [])

    def test_demo_vision_not_copied_but_engineer_confirmed_guard_triggers_without_sensors(self):
        data = self.payload(telemetrySource={'kind': 'demo_scenario', 'scenarioId': 'coupling_guard'})
        preview = inspections.preview(self.connection, data)
        self.assertNotIn('coupling_guard', [item['id'] for item in preview['findings']])
        self.assertEqual(preview['provenance']['telemetry'], 'demo_scenario')
        self.assertIsNone(preview['vision']['guard_issue'])
        confirmed = inspections.preview(self.connection, self.payload(confirmedVision={'guard_issue': True}))
        self.assertEqual([item['id'] for item in confirmed['findings']], ['coupling_guard'])
        self.assertEqual(confirmed['provenance']['telemetry'], 'none')
        self.assertEqual(confirmed['provenance']['vision'], 'engineer_confirmed')

    def test_confirmed_seal_plus_demo_telemetry_fuses_and_saves_provenance(self):
        data = self.payload(telemetrySource={'kind': 'demo_scenario', 'scenarioId': 'seal_leak'},
                            confirmedVision={'seal_leak': True}, note='Подтверждено у вала')
        with self.connection:
            record, created = inspections.save(self.connection, data)
        self.assertTrue(created)
        self.assertIn('seal_leak', [item['id'] for item in record['findings']])
        self.assertEqual(record['provenance']['telemetry'], 'demo_scenario')
        self.assertEqual(record['evidence']['analysisId'], self.analysis['analysisId'])
        self.assertEqual(history.get_record(self.connection, record['id']), record)

    def test_retry_uses_original_snapshot_after_scenario_changes(self):
        data = self.payload(telemetrySource={'kind': 'demo_scenario', 'scenarioId': 'seal_leak'},
                            confirmedVision={'seal_leak': True})
        with self.connection:
            record, _ = inspections.save(self.connection, data)
            self.connection.execute("UPDATE inspections SET telemetry_json = '{}' WHERE id = 'seal_leak'")
            retry, created = inspections.save(self.connection, data)
        self.assertFalse(created)
        self.assertEqual(retry, record)
        for changed in ({'note': 'changed'}, {'confirmedVision': {'seal_leak': False}},
                        {'telemetrySource': {'kind': 'none'}}):
            with self.subTest(changed=changed), self.assertRaises(history.IdempotencyConflict):
                with self.connection:
                    inspections.save(self.connection, data | changed)
        self.assertEqual(history.list_records(self.connection, 'P-102'), [record])

    def test_expired_unsaved_evidence_rejected_saved_evidence_retained(self):
        with self.connection:
            record, _ = inspections.save(self.connection, self.payload())
            old = inspections.store_analysis(self.connection, b'old-image', RESULT,
                now=datetime.now(timezone.utc) - timedelta(hours=3))
        with self.assertRaises(inspections.MissingEvidence):
            inspections.get_analysis(self.connection, old['analysisId'])
        future = datetime.now(timezone.utc) + timedelta(days=10)
        with self.connection:
            inspections.cleanup(self.connection, future)
        self.assertEqual(inspections.get_analysis(self.connection, record['evidence']['analysisId'],
                                                  include_image=True, now=future), b'normalized-jpeg')

    def test_orphan_count_bounded_without_removing_saved_image(self):
        with self.connection:
            inspections.save(self.connection, self.payload())
            with patch.object(inspections, 'MAX_ORPHANS', 2):
                for n in range(4):
                    inspections.store_analysis(self.connection, bytes([n + 1]), RESULT)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM analysis_evidence WHERE retained=0').fetchone()[0], 2)
        self.assertEqual(inspections.get_analysis(self.connection, self.analysis['analysisId'], include_image=True), b'normalized-jpeg')

    def test_strict_inputs_and_missing_data(self):
        invalid = (
            {'confirmedVision': {'seal_leak': 'true'}}, {'confirmedVision': {'seal_leak': 1}},
            {'confirmedVision': {'unexpected': True}}, {'confirmedVision': {'gauge_pressure': True}},
            {'confirmedVision': {'gauge_pressure': -1}}, {'confirmedVision': {'gauge_pressure': float('inf')}},
            {'confirmedVision': {'gauge_pressure': 10 ** 500}}, {'confirmedVision': []},
            {'telemetrySource': {'kind': 'customer_replay', 'index': 0}},
            {'telemetrySource': {'kind': 'none', 'telemetry': {}}}, {'telemetrySource': None},
            {'note': 4}, {'note': 'a' * 1001}, {'requestId': ''}, {'findings': []},
        )
        for fields in invalid:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                inspections.validate_request(self.payload(**fields), saving=True)
        with self.assertRaises(history.UnknownPump):
            inspections.preview(self.connection, self.payload(serial='unknown'))
        with self.assertRaises(inspections.MissingEvidence):
            inspections.preview(self.connection, self.payload(analysisId='unknown'))
        with self.assertRaises(inspections.MissingScenario):
            inspections.preview(self.connection, self.payload(telemetrySource={'kind': 'demo_scenario', 'scenarioId': 'unknown'}))
        with self.assertRaises(ValueError):
            inspections.store_analysis(self.connection, b'jpeg', {'status': 'unusable', 'source': 'local_vision'})

    def test_concurrent_retries_create_one_record(self):
        def once(_):
            with closing(sqlite3.connect(self.database)) as connection, connection:
                return inspections.save(connection, self.payload())
        with ThreadPoolExecutor(max_workers=2) as executor:
            records = list(executor.map(once, range(2)))
        self.assertEqual(sum(created for _, created in records), 1)
        self.assertEqual(records[0][0], records[1][0])

    def test_legacy_history_schema_migrates_without_data_loss(self):
        connection = sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        connection.executescript('''
            CREATE TABLE pumps(serial TEXT PRIMARY KEY, passport_json TEXT);
            INSERT INTO pumps VALUES('P-102','{}');
            CREATE TABLE inspection_records (
                id TEXT PRIMARY KEY, serial TEXT, scenario_id TEXT, captured_at TEXT,
                telemetry_json TEXT, vision_json TEXT, findings_json TEXT, note TEXT, request_id TEXT,
                UNIQUE(serial, request_id));
            INSERT INTO inspection_records VALUES('old','P-102','healthy','2026-11-13T00:00:00Z','{}','{}','[]','old note','old-key');
        ''')
        history.initialize(connection)
        history.initialize(connection)
        record = history.list_records(connection, 'P-102')[0]
        self.assertEqual(record['note'], 'old note')
        self.assertEqual(record['evidence'], {})
        self.assertEqual(record['provenance'], {})


class InspectionApiTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / 'api.sqlite3'
        initialize(self.database)
        self.handler = partial(Handler, database=self.database, public_url='https://cyber102.test', vision_enabled=True)
        self.server = BoundedHTTPServer(('127.0.0.1', 0), self.handler)
        self.addCleanup(self.server.server_close)
        thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01})
        thread.start()
        self.addCleanup(thread.join, 3)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None, headers=None):
        headers = dict(headers or {})
        if isinstance(body, dict):
            body = json.dumps(body).encode()
            headers['Content-Type'] = 'application/json'
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            connection.request(method, path, body, headers)
            response = connection.getresponse()
            return response.status, response.headers, response.read()
        finally:
            connection.close()

    def test_complete_analysis_preview_save_image_history_flow(self):
        with patch.object(vision, 'extract_request', return_value=(b'exact-normalized-jpeg', 'standard', {'captureSource': 'camera', 'declaredBy': 'client'})), \
             patch.object(vision, 'analyze', return_value=RESULT):
            status, _, raw = self.request('POST', '/analyze', b'upload')
        self.assertEqual(status, 200, raw)
        analyzed = json.loads(raw)
        status, headers, image = self.request('GET', analyzed['imageUrl'])
        self.assertEqual((status, headers.get_content_type(), image), (200, 'image/jpeg', b'exact-normalized-jpeg'))
        payload = {'serial': 'P-102', 'analysisId': analyzed['analysisId'],
                   'telemetrySource': {'kind': 'none'}, 'confirmedVision': {'guard_issue': True}, 'requestId': 'api-one'}
        status, _, raw = self.request('POST', '/api/inspections/preview', payload)
        self.assertEqual(status, 200, raw)
        self.assertEqual([r['id'] for r in json.loads(raw)['findings']], ['coupling_guard'])
        status, _, raw = self.request('POST', '/api/inspections', payload)
        self.assertEqual(status, 201, raw)
        record = json.loads(raw)['record']
        status, _, raw = self.request('GET', '/api/history?serial=P-102')
        self.assertEqual(json.loads(raw)['records'], [record])
        status, _, raw = self.request('GET', f"/api/inspections/{record['id']}")
        self.assertEqual(json.loads(raw)['record'], record)
        status, _, raw = self.request('POST', '/api/inspections', payload)
        self.assertEqual(status, 200, raw)
        self.assertFalse(json.loads(raw)['created'])
        self.assertEqual(self.request('POST', '/api/inspections', payload | {'note': 'changed'})[0], 409)
        self.assertEqual(self.request('POST', '/api/inspections', payload, {'Origin': 'https://evil.test'})[0], 403)
        self.assertEqual(self.request('POST', '/api/inspections', payload | {'analysisId': 'missing', 'requestId': 'new'})[0], 404)

    def test_unusable_frame_is_not_saveable_and_busy_has_retry_guidance(self):
        with patch.object(vision, 'extract_request', return_value=(b'jpeg', 'fast', {'captureSource': 'camera', 'declaredBy': 'client'})), \
             patch.object(vision, 'analyze', return_value={'status': 'unusable', 'source': 'local_vision'}):
            status, _, raw = self.request('POST', '/analyze', b'upload')
        self.assertEqual(status, 200)
        self.assertNotIn('analysisId', json.loads(raw))
        with patch.object(vision, 'extract_request', return_value=(b'jpeg', 'fast', {'captureSource': 'camera', 'declaredBy': 'client'})), \
             patch.object(vision, 'analyze', side_effect=vision.VisionBusy('busy')):
            status, headers, raw = self.request('POST', '/analyze', b'upload')
        self.assertEqual(status, 503)
        self.assertEqual(headers['Retry-After'], '2')
        self.assertEqual(json.loads(raw)['code'], 'vision_busy')


if __name__ == '__main__':
    unittest.main()
