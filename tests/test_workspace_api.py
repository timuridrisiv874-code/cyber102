import json
import socket
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from functools import partial
from hashlib import sha256
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

from server import app


def empty_page_pdf():
    objects = (
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Resources << >> >>',
    )
    pdf = bytearray(b'%PDF-1.4\n')
    offsets = []
    for index, body in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf.extend(f'{index} 0 obj\n'.encode() + body + b'\nendobj\n')
    xref = len(pdf)
    pdf.extend(b'xref\n0 4\n0000000000 65535 f \n')
    for offset in offsets:
        pdf.extend(f'{offset:010d} 00000 n \n'.encode())
    pdf.extend(f'trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
    return bytes(pdf)


class WorkspaceApiTest(unittest.TestCase):
    document_id = 'pump-manual'
    pdf_url = f'/api/customer/documents/{document_id}/pdf'
    snapshot_url = '/api/telemetry/snapshot'

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.customer = self.root / 'customer'
        self.documents = self.customer / 'documents'
        self.documents.mkdir(parents=True)
        self.database = self.root / 'unused.sqlite3'
        handler = partial(app.Handler, database=self.database, public_url='https://cyber102.example.test',
                          customer_root=self.customer)
        self.server = app.BoundedHTTPServer(('127.0.0.1', 0), handler)
        self.addCleanup(self.server.server_close)
        thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01})
        thread.start()
        self.addCleanup(thread.join, 3)
        self.addCleanup(self.server.shutdown)

    def write_dataset(self, name, data):
        (self.customer / f'{name}.json').write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')

    def index_pdf(self, content):
        digest = sha256(content).hexdigest()
        index = {'source': 'customer_provided', 'documents': [
            {'id': self.document_id, 'title': 'Руководство насоса', 'sha256': digest, 'url': self.pdf_url},
        ]}
        self.write_dataset('documents', index)
        return index, self.documents / f'{digest}.pdf'

    def get(self, path, expected=200, *, request_headers=()):
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            connection.putrequest('GET', path)
            for name, value in request_headers:
                connection.putheader(name, value)
            connection.endheaders()
            response = connection.getresponse()
            body = response.read()
            self.assertEqual(response.status, expected, body[:200])
            self.assertEqual(response.getheader('Cache-Control'), 'no-store')
            self.assertEqual(response.getheader('X-Content-Type-Options'), 'nosniff')
            self.assertEqual(int(response.getheader('Content-Length')), len(body))
            return response.headers, body
        finally:
            connection.close()

    def get_json(self, path, expected=200):
        headers, body = self.get(path, expected)
        self.assertEqual(headers.get_content_type(), 'application/json')
        payload = json.loads(body)
        if expected >= 400:
            self.assertTrue(payload.get('error'))
        return payload

    def link(self, path, target, *, directory=False):
        try:
            path.symlink_to(target, target_is_directory=directory)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f'Creating symlinks is unavailable: {error}')

    def test_document_index_passports_and_hash_verified_original(self):
        pdf = empty_page_pdf()
        index, path = self.index_pdf(pdf)
        path.write_bytes(pdf)
        passports = {'source': 'customer_provided', 'equipment': [{'serial': None, 'model': 'Д160–112б'}]}
        self.write_dataset('equipment-passports', passports)
        self.assertEqual(self.get_json('/api/customer/documents'), index)
        self.assertEqual(self.get_json('/api/customer/equipment-passports'), passports)
        headers, body = self.get(self.pdf_url)
        self.assertEqual(headers.get_content_type(), 'application/pdf')
        self.assertEqual(sha256(body).hexdigest(), index['documents'][0]['sha256'])
        self.assertEqual(body, pdf)
        self.assertFalse(self.database.exists())

    def test_missing_unknown_and_invalid_document_ids_return_404(self):
        self.get_json('/api/customer/documents', 404)
        self.get_json('/api/customer/equipment-passports', 404)
        pdf = empty_page_pdf()
        _, path = self.index_pdf(pdf)
        self.get_json(self.pdf_url, 404)
        path.write_bytes(pdf)
        for document_id in ('unknown', '', 'Pump-manual', 'x' * 81, 'pump-manual.pdf', '../documents',
                            '%2e%2e%2fdocuments', f'{self.document_id}/../{self.document_id}'):
            with self.subTest(document_id=document_id):
                self.get_json(f'/api/customer/documents/{document_id}/pdf', 404)

    def test_hash_mismatch_and_non_pdf_with_matching_hash_return_503(self):
        pdf = empty_page_pdf()
        _, path = self.index_pdf(pdf)
        path.write_bytes(pdf + b'changed')
        self.get_json(self.pdf_url, 503)
        non_pdf = b'not a PDF'
        _, path = self.index_pdf(non_pdf)
        path.write_bytes(non_pdf)
        self.get_json(self.pdf_url, 503)
        self.write_dataset('documents', {'documents': [None]})
        self.get_json(self.pdf_url, 503)

    def test_pdf_accepts_one_bounded_suffix_open_or_clipped_range(self):
        pdf = empty_page_pdf()
        _, path = self.index_pdf(pdf)
        path.write_bytes(pdf)
        length = len(pdf)
        for value, start, end in (
            ('bytes=0-4', 0, 4), ('bytes=-5', length - 5, length - 1),
            ('bytes=5-', 5, length - 1), (f'bytes={length - 5}-{length + 99}', length - 5, length - 1),
        ):
            with self.subTest(value=value):
                headers, body = self.get(self.pdf_url, 206, request_headers=(('Range', value),))
                self.assertEqual(headers.get_content_type(), 'application/pdf')
                self.assertEqual(headers.get('Accept-Ranges'), 'bytes')
                self.assertEqual(headers.get('Content-Range'), f'bytes {start}-{end}/{length}')
                self.assertEqual(body, pdf[start:end + 1])

    def test_pdf_rejects_invalid_and_multiple_ranges(self):
        pdf = empty_page_pdf()
        _, path = self.index_pdf(pdf)
        path.write_bytes(pdf)
        for values in (
            ('bytes=0-4,5-9',), ('bytes=0-4', 'bytes=5-9'), ('',), ('bytes=-',), ('bytes=-0',),
            (f'bytes={len(pdf)}-',), ('bytes=6-5',), ('characters=0-4',),
        ):
            with self.subTest(values=values):
                headers, body = self.get(self.pdf_url, 416, request_headers=tuple(('Range', value) for value in values))
                self.assertEqual(headers.get('Content-Range'), f'bytes */{len(pdf)}')
                self.assertEqual(body, b'')

    def test_pdf_head_reports_full_length_without_sending_bytes(self):
        pdf = empty_page_pdf()
        _, path = self.index_pdf(pdf)
        path.write_bytes(pdf)
        with socket.create_connection(('127.0.0.1', self.server.server_port), timeout=3) as connection:
            connection.sendall(f'HEAD {self.pdf_url} HTTP/1.0\r\nRange: bytes=0-4\r\n\r\n'.encode('ascii'))
            with connection.makefile('rb') as response:
                header, separator, body = response.read().partition(b'\r\n\r\n')
        self.assertEqual(separator, b'\r\n\r\n')
        self.assertTrue(header.startswith(b'HTTP/1.0 200 '))
        self.assertIn(f'Content-Length: {len(pdf)}'.encode('ascii'), header.splitlines())
        self.assertIn(b'Content-Type: application/pdf', header.splitlines())
        self.assertIn(b'Accept-Ranges: bytes', header.splitlines())
        self.assertIn(b'Cache-Control: no-store', header.splitlines())
        self.assertEqual(body, b'')

    def test_pdf_file_and_pdf_directory_links_outside_customer_root_return_404(self):
        pdf = empty_page_pdf()
        _, path = self.index_pdf(pdf)
        outside = self.root / 'outside'
        outside.mkdir()
        target = outside / path.name
        target.write_bytes(pdf)
        self.link(path, target)
        self.get_json(self.pdf_url, 404)
        path.unlink()
        self.documents.rmdir()
        self.link(self.documents, outside, directory=True)
        self.get_json(self.pdf_url, 404)

    def test_looping_dataset_symlink_returns_controlled_error(self):
        path = self.customer / 'documents.json'
        self.link(path, path)
        try:
            path.resolve()
        except (RuntimeError, OSError):
            pass
        else:
            self.skipTest('Non-strict Path.resolve on this Python does not report a symlink loop')
        self.get_json('/api/customer/documents', 503)
        self.get_json(self.pdf_url, 503)

    def test_nonfinite_customer_json_returns_503(self):
        for number in ('NaN', '1e999'):
            with self.subTest(number=number):
                (self.customer / 'equipment-passports.json').write_text(
                    '{"equipment": [{"numeric": ' + number + '}]}', encoding='utf-8',
                )
                self.get_json('/api/customer/equipment-passports', 503)

    def write_replay(self):
        first = datetime(2026, 11, 10, 8)
        rows = []
        for index in range(500):
            disagree = index == 499
            rows.append({
                'timestamp': (first + timedelta(hours=index)).isoformat(), 'rpm': 2960,
                'vibration': {'rms_mm_s': 5.36, 'high_frequency_g': 4.0,
                              'source_label': 'Тревога: Дефект подшипника' if disagree else 'Норма'},
                'temperature': {'bearing_c': 82, 'winding_c': 75},
                'assessments': {'vibration': {'rms_overall': {'level': 'unknown', 'value': 5.36},
                                              'disagreement': disagree}},
                'provenance': {'vibration': {'sheet': 'Вибрация', 'row': index + 2}},
            })
        data = {'schemaVersion': 1, 'dataset': {'id': 'fixture', 'source': 'customer_example',
                                                 'equipmentSerial': None, 'timezone': None},
                'summary': {'rowCount': len(rows)}, 'rows': rows, 'sources': [{'id': 'vibration_data'}],
                'thresholds': {'vibration': {'rms_overall': {'warning': None, 'alarm': None}}}}
        self.write_dataset('sensors', data)
        return data

    def test_replay_has_boundaries_and_preserves_provenance_without_running_rules(self):
        data = self.write_replay()
        with patch.object(app.assessment, 'diagnose') as evaluate:
            catalog = self.get_json('/api/telemetry/catalog')
            self.assertEqual((catalog['firstIndex'], catalog['lastIndex'], catalog['rowCount']), (0, 499, 500))
            self.assertEqual(catalog['sources'], data['sources'])
            self.assertEqual(catalog['thresholds'], data['thresholds'])
            for index in (499, 0, 499):
                with self.subTest(index=index):
                    snapshot = self.get_json(f'{self.snapshot_url}?index={index}')
                    self.assertEqual(snapshot['row'], data['rows'][index])
                    self.assertEqual(snapshot['source'], 'customer_replay')
                    self.assertEqual(snapshot['sourceTimestamp'], data['rows'][index]['timestamp'])
                    self.assertIsNone(snapshot['sourceTimezone'])
                    self.assertIsNone(snapshot['equipmentSerial'])
                    self.assertEqual(datetime.fromisoformat(snapshot['emittedAt']).utcoffset(), timedelta(0))
                    self.assertEqual(snapshot['quality']['measurementQuality'], 'unknown')
                    self.assertIn('current', snapshot['quality']['unavailableTelemetry'])
                    self.assertEqual(snapshot['row']['assessments']['vibration']['rms_overall']['level'], 'unknown')
                    self.assertNotIn('findings', snapshot)
            evaluate.assert_not_called()
        self.assertFalse(self.database.exists())

    def test_replay_invalid_query_and_past_end_return_400(self):
        self.write_replay()
        for query in ('', '?index=', '?index=-1', '?index=1.0', '?index=0&index=1', '?index=0&index=',
                      '?index=' + '9' * 120, '?index=' + quote('１２'), '?index=' + quote('²'), '?index=500'):
            with self.subTest(query=query):
                self.get_json(self.snapshot_url + query, 400)

    def test_replay_missing_and_corrupt_dataset_return_controlled_errors(self):
        for path in ('/api/telemetry/catalog', f'{self.snapshot_url}?index=0'):
            self.get_json(path, 404)
        self.write_dataset('sensors', {'rows': []})
        for path in ('/api/telemetry/catalog', f'{self.snapshot_url}?index=0'):
            self.get_json(path, 503)


if __name__ == '__main__':
    unittest.main()
