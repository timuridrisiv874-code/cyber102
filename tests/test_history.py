import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

from server import history


def _initialize(connection):
    connection.executescript('''
        PRAGMA foreign_keys = ON;
        CREATE TABLE pumps (serial TEXT PRIMARY KEY, passport_json TEXT NOT NULL);
        INSERT INTO pumps VALUES ('P-102', '{}'), ('P-103', '{}');
    ''')
    history.initialize(connection)


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.addCleanup(self.connection.close)
        _initialize(self.connection)

    def save(self, serial='P-102', scenario='healthy', telemetry=None, vision=None, findings=None, **kwargs):
        return history.save(self.connection, serial, scenario, telemetry if telemetry is not None else {},
                            vision if vision is not None else {}, findings if findings is not None else [], **kwargs)

    def test_saved_snapshot_survives_initialization_and_keeps_inputs_and_result(self):
        telemetry = {'vibration': 'rising'}
        vision = {'guard_issue': True}
        findings = [{'id': 'coupling_guard', 'title': 'Кожух муфты'}]
        record, created = self.save(scenario=None, telemetry=telemetry, vision=vision, findings=findings, note='Осмотр')
        self.assertTrue(created)
        self.assertEqual(UUID(record['id']).version, 4)
        self.assertEqual(datetime.fromisoformat(record['capturedAt']).utcoffset(), timedelta(0))
        self.assertIsNone(record['scenarioId'])
        self.assertIsNone(record['requestId'])
        telemetry['vibration'] = 'normal'
        findings.clear()
        history.initialize(self.connection)
        self.assertEqual(history.list_records(self.connection, 'P-102'), [record])
        self.assertEqual(record['telemetry'], {'vibration': 'rising'})
        self.assertEqual(record['vision'], {'guard_issue': True})
        self.assertEqual(record['findings'], [{'id': 'coupling_guard', 'title': 'Кожух муфты'}])

    def test_retry_returns_original_snapshot_and_conflicting_inputs_fail(self):
        first, _ = self.save(telemetry={'hours': 4000, 'current': 'normal'},
                             findings=[{'id': 'scheduled_maintenance'}], request_id='retry-key')
        retry, created = self.save(telemetry={'current': 'normal', 'hours': 4000}, request_id='retry-key')
        self.assertFalse(created)
        self.assertEqual(retry, first)

        with self.assertRaises(history.IdempotencyConflict):
            self.save(telemetry={'current': 'normal', 'hours': 8000}, request_id='retry-key')
        with self.assertRaises(history.IdempotencyConflict):
            self.save(telemetry={'current': 'normal', 'hours': 4000}, note='Другая заметка', request_id='retry-key')
        self.assertEqual(history.list_records(self.connection, 'P-102'), [first])
        second_pump, created = self.save(serial='P-103', request_id='retry-key')
        self.assertTrue(created)
        self.assertNotEqual(second_pump['id'], first['id'])

    def test_history_is_per_pump_and_newest_first(self):
        first, _ = self.save(note='Первый осмотр')
        self.save(serial='P-103')
        second, _ = self.save(note='Второй осмотр')
        self.assertEqual(history.list_records(self.connection, 'P-102'), [second, first])
        self.assertEqual(history.list_records(self.connection, 'P-102', limit=1), [second])

    def test_invalid_input_and_missing_pump(self):
        for arguments in (
            {'note': 'я' * 1001}, {'note': 17}, {'request_id': ''}, {'request_id': 'a' * 129},
            {'telemetry': []}, {'vision': {'gauge_pressure': float('nan')}}, {'findings': ['diagnosis']},
        ):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                self.save(**arguments)
        for limit in (0, 101, True, '20'):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                history.list_records(self.connection, 'P-102', limit)
        with self.assertRaises(history.UnknownPump):
            self.save(serial='missing')
        with self.assertRaises(history.UnknownPump):
            history.list_records(self.connection, 'missing')
        self.assertEqual(history.list_records(self.connection, 'P-102'), [])

    def test_concurrent_retries_create_one_persistent_record(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'history.sqlite3'
            with closing(sqlite3.connect(database)) as connection, connection:
                _initialize(connection)

            def save_once():
                with closing(sqlite3.connect(database)) as connection, connection:
                    return history.save(connection, 'P-102', 'healthy', {}, {}, [], request_id='same-key')

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _: save_once(), range(2)))
            self.assertEqual(sum(created for _, created in results), 1)
            self.assertEqual(results[0][0], results[1][0])

            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(history.list_records(connection, 'P-102'), [results[0][0]])


if __name__ == '__main__':
    unittest.main()
