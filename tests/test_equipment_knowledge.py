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
        for item in result['matches']:
            expected = result['target']['serial'] if item['applicability'] == 'passport' else None
            self.assertEqual(item['targetSerial'], expected)
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
        self.assertIn('одновинтовой', context['text'])
        self.assertIn('Привязанных показаний датчиков нет', context['text'])
        self.assertEqual([item['applicability'] for item in context['references']],
                         ['passport', 'passport', 'technical_reference'])
        self.assertIn('NETZSCH, справка по типу оборудования', context['text'])
        for item in context['references']:
            self.assertIn(item['text'], context['text'])
        with self.assertRaises(ValueError):
            request_profile({'image': 'placeholder', 'context': 'x' * (knowledge.MAX_CONTEXT + 1)})

    def test_reference_queries_from_external_inspection(self):
        cases = (
            ({'serial': '21031004-1', 'component': 'seal'}, 'из сальника потекло', 'Одинарное торцевое уплотнение'),
            ({'serial': '21031004-1'}, 'как устроены ротор и статор', 'Ротор и статор винтового насоса'),
            ({'serial': '21031004-1', 'component': 'foundation'}, 'перекос мягкая лапа', 'Мягкая лапа и лазерная центровка'),
            ({'serial': '21031004-1', 'component': 'shaft_joints'}, 'нет ограждения на муфте', 'Защитное ограждение вращающихся частей'),
            ({'serial': '21031004-1', 'component': 'motor'}, 'почернел клеммник', 'Локальный нагрев электрического соединения'),
            ({'serial': '21031004-1', 'component': 'temperature_transmitter'}, 'защита от сухого хода', 'Защита от сухого хода и контроль обмотки'),
            ({'model': 'knz-4d200-90'}, 'шум гравия и кавитация', 'Кавитация центробежного насоса'),
            ({'model': 'knz-km80-65-160'}, 'упал кпд внутренний износ', 'Зазоры и внутренние перетоки'),
            ({'section': 'pressure'}, 'сверка манометра и показания SCADA', 'Расхождение давления и класс точности'),
            ({'section': 'valve'}, 'потекло из сальника задвижки', 'Арматура: сальник штока'),
            ({'section': 'pipe'}, 'ржавая труба и глубина трещины', 'Коррозия трубы и потеря толщины стенки'),
            ({'section': 'motor'}, 'грязь забила вентилятор', 'Охлаждение двигателя и вентиляция'),
            ({'section': 'motor'}, 'проверить изоляцию обмотки мегомметром', 'Сопротивление изоляции двигателя'),
            ({'section': 'gearbox'}, 'из сапуна гонит масло', 'Масло на сапуне, крышке или у вала редуктора'),
            ({'section': 'temperature_transmitter'}, 'Pt100 3 провода', 'Pt100 и сопротивление проводов'),
            ({'section': 'valve'}, 'команда есть шток не ходит', 'Команда и фактическое положение клапана'),
            ({'section': 'pipe'}, 'толщиномер ультразвук стенка', 'Ультразвуковой контроль толщины стенки'),
            ({'section': 'drive'}, 'OCF при разгоне двигателя', 'Сверхток OCF у приводов Altivar Process'),
            ({'section': 'drive'}, 'пропала фаза OPF1', 'Потеря выходной фазы преобразователя'),
            ({'section': 'level'}, 'радар скачет отражение от мешалки', 'Ложные отражения радарного уровнемера'),
        )
        for target, query, title in cases:
            with self.subTest(query=query):
                matches = knowledge.lookup(**target, query=query)['matches']
                self.assertIn(title, [match['title'] for match in matches[:2]])
                if 'section' in target:
                    self.assertTrue(all(match['targetSerial'] is None for match in matches))

    def test_model_context_uses_the_kind_of_the_selected_passport(self):
        cases = (
            ('230201068', 'ABB', 1),
            ('SAL023020084_001002', 'SEW‑EURODRIVE', 3),
            ('10096664', 'WIKA', 6),
        )
        for serial, publisher, page in cases:
            with self.subTest(serial=serial):
                context = knowledge.photo_context(serial)
                self.assertIn(serial, context['text'])
                self.assertIn(publisher, context['text'])
                self.assertNotIn('NETZSCH', context['text'])
                self.assertEqual(context['references'][0]['source']['documentId'], 'drive-1-passports')
                self.assertEqual(context['references'][0]['source']['page'], page)
                self.assertEqual(context['references'][-1]['applicability'], 'technical_reference')
                self.assertIsNone(context['references'][-1]['targetSerial'])
        gearbox = knowledge.photo_context('SAL023020084_001002')['text']
        self.assertIn('VG220 / Synoil, 3,7 л', gearbox)

    def test_unsupported_rules_do_not_become_passport_requirements(self):
        result = knowledge.lookup(serial='21031004-1', query='4000 8000 моточасов')
        self.assertEqual(result['matches'], [])
        result = knowledge.lookup(serial='21031004-1', query='кавитация и крыльчатка')
        self.assertTrue(result['matches'])
        self.assertTrue(all(item['source'].get('publisher') != 'KSB' for item in result['matches']))

    def test_public_corpus_has_cited_scoped_entries(self):
        corpus = customer.load('inspection-knowledge')
        sections = {item['id'] for item in corpus['sections']}
        scopes = sections | set(knowledge.recommendations.COMPONENT_LABELS) | {'progressive_cavity', 'centrifugal'}
        self.assertEqual({item['source'] for item in corpus['entries']}, set(corpus['sources']))
        for item in corpus['entries']:
            self.assertTrue(item['scopes'])
            self.assertLessEqual(set(item['scopes']), scopes)
            self.assertTrue(corpus['sources'][item['source']]['url'].startswith('https://'))

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
