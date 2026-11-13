import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from server import telemetry


class TelemetryReplayTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        first = datetime(2026, 11, 10, 8)
        self.rows = []
        for index in range(500):
            disagrees = index >= 482
            self.rows.append({
                'timestamp': (first + timedelta(hours=index)).isoformat(),
                'rpm': 2963,
                'vibration': {'rms_mm_s': 5.36, 'one_x_mm_s': 0.84, 'two_x_mm_s': 0.22,
                              'high_frequency_g': 4.0 if disagrees else 0.1,
                              'source_label': 'Тревога: Дефект подшипника' if disagrees else 'Норма'},
                'temperature': {'bearing_c': 98.5, 'winding_c': 64.3, 'source_label': 'Норма'},
                'assessments': {'vibration': {'rms_overall': {'level': 'unknown', 'value': 5.36, 'unit': 'mm/s'},
                                              'computed_label': 'Пред: Износ/Шум' if disagrees else 'Норма',
                                              'disagreement': disagrees}, 'temperature': {'disagreement': False}},
                'provenance': {'vibration': {'sourceId': 'vibration_data', 'sheet': 'Вибрация', 'row': index + 2},
                               'temperature': {'sourceId': 'temperature_data', 'sheet': 'Термометрия', 'row': index + 2}},
            })
        self.dataset = {
            'schemaVersion': 1,
            'dataset': {'id': 'customer-sensors-example', 'source': 'customer_example',
                        'equipmentSerial': None, 'timezone': None, 'intervalHours': 1},
            'thresholds': {'vibration': {'rms_overall': {'warning': None, 'alarm': None}}},
            'sources': [{'id': 'vibration_data'}, {'id': 'temperature_data'}],
            'summary': {'rowCount': len(self.rows), 'vibration': {'labelDisagreements': 18}},
            'rows': self.rows,
        }
        self.write_data()

    def write_data(self):
        (self.root / 'sensors.json').write_text(json.dumps(self.dataset, ensure_ascii=False), encoding='utf-8')

    def test_catalog_exposes_range_thresholds_and_original_dataset_provenance(self):
        catalog = telemetry.catalog(self.root)
        self.assertEqual(catalog['source'], 'customer_replay')
        self.assertIsNone(catalog['equipmentSerial'])
        self.assertEqual((catalog['firstIndex'], catalog['lastIndex'], catalog['rowCount']), (0, 499, 500))
        for key in ('dataset', 'sources', 'thresholds', 'summary'):
            self.assertEqual(catalog[key], self.dataset[key])
        self.assertNotIn('rows', catalog)
        self.assertEqual(catalog['channels']['vibration.high_frequency_g'], 'g')

    def test_snapshots_are_stateless_and_keep_source_time_separate_from_emission(self):
        before = datetime.now(timezone.utc)
        last = telemetry.snapshot(499, root=self.root)
        first = telemetry.snapshot(0, root=self.root)
        same = telemetry.snapshot(499, root=self.root)
        after = datetime.now(timezone.utc)
        for snapshot, index in ((first, 0), (last, 499), (same, 499)):
            self.assertEqual(snapshot['index'], index)
            self.assertEqual(snapshot['row'], self.rows[index])
            self.assertEqual(snapshot['sourceTimestamp'], self.rows[index]['timestamp'])
            self.assertIsNone(snapshot['sourceTimezone'])
            self.assertIsNone(snapshot['equipmentSerial'])
            self.assertEqual(snapshot['datasetId'], self.dataset['dataset']['id'])
            self.assertEqual(snapshot['source'], 'customer_replay')
            emitted = datetime.fromisoformat(snapshot.pop('emittedAt'))
            self.assertEqual(emitted.utcoffset(), timedelta(0))
            self.assertLessEqual(before - timedelta(milliseconds=1), emitted)
            self.assertLessEqual(emitted, after)
        self.assertEqual(last, same)

    def test_row_preserves_unknown_rms_and_disagreement_without_inventing_diagnosis(self):
        snapshot = telemetry.snapshot(482, root=self.root)
        vibration = snapshot['row']['assessments']['vibration']
        self.assertEqual(vibration['rms_overall']['level'], 'unknown')
        self.assertEqual(vibration['rms_overall']['value'], 5.36)
        self.assertIs(vibration['disagreement'], True)
        self.assertEqual(snapshot['row']['vibration']['source_label'], 'Тревога: Дефект подшипника')
        self.assertEqual(vibration['computed_label'], 'Пред: Износ/Шум')
        self.assertEqual(snapshot['quality']['measurementQuality'], 'unknown')
        self.assertLessEqual({'current', 'inlet_pressure', 'discharge_pressure'}, set(snapshot['quality']['unavailableTelemetry']))
        self.assertNotIn('telemetry', snapshot)
        self.assertNotIn('findings', snapshot)

    def test_missing_measurements_are_reported_without_treating_zero_as_missing(self):
        self.rows[0]['temperature']['bearing_c'] = None
        self.rows[0]['vibration']['high_frequency_g'] = 0
        del self.rows[0]['vibration']['one_x_mm_s']
        self.write_data()
        snapshot = telemetry.snapshot(0, root=self.root)
        self.assertEqual(snapshot['quality']['missingMeasurements'], ['vibration.one_x_mm_s', 'temperature.bearing_c'])
        self.assertEqual(snapshot['row']['vibration']['high_frequency_g'], 0)
        self.assertIsNone(snapshot['row']['temperature']['bearing_c'])

    def test_invalid_index_and_missing_dataset(self):
        for index in (-1, True, 1.0, '1', None):
            with self.subTest(index=index), self.assertRaises(ValueError):
                telemetry.snapshot(index, root=self.root)
        with self.assertRaises(IndexError):
            telemetry.snapshot(500, root=self.root)
        (self.root / 'sensors.json').unlink()
        with self.assertRaises(FileNotFoundError):
            telemetry.catalog(self.root)

    def test_rejects_bad_dataset_identity_timestamp_and_measurement(self):
        self.dataset['dataset']['equipmentSerial'] = 'P-102'
        self.write_data()
        with self.assertRaises(ValueError):
            telemetry.catalog(self.root)
        self.dataset['dataset']['equipmentSerial'] = None
        self.rows[0]['timestamp'] += '+03:00'
        self.write_data()
        with self.assertRaises(ValueError):
            telemetry.snapshot(0, root=self.root)
        self.rows[0]['timestamp'] = self.rows[0]['timestamp'][:-6]
        for value in (False, '98.5', float('inf')):
            self.rows[0]['temperature']['bearing_c'] = value
            self.write_data()
            with self.subTest(value=value), self.assertRaises(ValueError):
                telemetry.snapshot(0, root=self.root)


if __name__ == '__main__':
    unittest.main()
