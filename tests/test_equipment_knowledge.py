from contextlib import closing
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from server import app, customer, emulator, knowledge
from vision_runtime.worker import request_profile


class EquipmentKnowledgeTest(unittest.TestCase):
    def test_document_group_is_scoped_to_exact_serial(self):
        result = knowledge.lookup(serial='21031004-3', component='gearbox', query='масло')
        self.assertEqual(result['target']['serial'], 'SAL023020084_001004')
        self.assertTrue(result['matches'])
        self.assertEqual({item['targetSerial'] for item in result['matches']}, {result['target']['serial']})
        self.assertEqual(result['telemetry']['status'], 'unlinked')
        self.assertEqual(result['matches'][0]['source']['url'], '/api/customer/documents/lubricants/pdf#page=1')

    def test_manufacturer_model_resolves_without_a_serial(self):
        result = knowledge.lookup(model='knz-4d200-90', query='пуск')
        self.assertEqual(result['scope'], 'manufacturer_model')
        self.assertIsNone(result['matches'][0]['targetSerial'])
        self.assertTrue(result['matches'][0]['source']['url'].endswith('#page=9'))
        self.assertIsNone(knowledge.photo_context('unknown'))

    def test_model_context_is_bounded_and_for_the_selected_serial(self):
        context = knowledge.photo_context('21031004-2')
        self.assertEqual(context['serial'], '21031004-2')
        self.assertLessEqual(len(context['text']), knowledge.MAX_CONTEXT)
        self.assertEqual(request_profile({'image': 'placeholder', 'context': context['text']}), 'standard')
        self.assertNotIn('21031004-3', context['text'])
        with self.assertRaises(ValueError):
            request_profile({'image': 'placeholder', 'context': 'x' * (knowledge.MAX_CONTEXT + 1)})

    def test_emulator_persists_distinct_samples_by_pump(self):
        with TemporaryDirectory() as temporary:
            database = Path(temporary) / 'telemetry.db'
            app.initialize(database, customer_root=customer.ROOT)
            with closing(sqlite3.connect(database)) as connection, connection:
                reference = emulator.replay_template(customer.ROOT)
                self.assertEqual(len(reference), 500)
                for instant in (2000, 2000, 2005):
                    emulator.record(connection, now=instant, reference=reference)
            with closing(sqlite3.connect(database)) as connection:
                first = emulator.snapshot(connection, '21031004-1')
                next_ = emulator.snapshot(connection, '21031004-2')
                self.assertEqual(first['source'], 'simulated')
                self.assertEqual(len(first['samples']), 12)
                self.assertNotEqual(first['latest']['values'], next_['latest']['values'])
                self.assertEqual(connection.execute('SELECT count(*) FROM simulated_telemetry').fetchone()[0], 52)
                self.assertIsNone(emulator.snapshot(connection, '230201068'))
