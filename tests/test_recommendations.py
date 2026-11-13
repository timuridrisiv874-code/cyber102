from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from server import customer, recommendations


class ManufacturerRecommendationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.catalog = customer.load('equipment-passports')
        self.documents = customer.load('documents')
        self.write()

    def write(self):
        for name, data in [('equipment-passports', self.catalog), ('documents', self.documents)]:
            (self.root / f'{name}.json').write_text(json.dumps(data), encoding='utf-8')

    def query(self, serial='21031004-1', component=None, rule_ids=None):
        return recommendations.for_equipment(serial, component, rule_ids, root=self.root)

    def test_demo_has_no_manufacturer_source_even_without_customer_files(self):
        result = recommendations.for_equipment('P-102', component='gearbox', root=self.root / 'missing')
        self.assertEqual(result['applicability'], 'unverified')
        self.assertEqual(result['recommendations'], [])
        self.assertIsNone(result['equipment'])
        self.assertIn('не привязаны', result['limitations'][0])
        self.assertIsNone(recommendations.get_equipment('P-102', self.root / 'missing'))

    def test_unknown_serial_does_not_fall_back_to_pump(self):
        result = self.query('UNKNOWN')
        self.assertEqual(result['applicability'], 'unknown_equipment')
        self.assertEqual(result['recommendations'], [])
        self.assertIsNone(recommendations.get_equipment('UNKNOWN', self.root))

    def test_registry_preserves_exact_serial_identity_and_no_diagnostic_thresholds(self):
        records = recommendations.registry(self.root)
        self.assertEqual(len(records), 16)
        self.assertEqual(len({item['serial'] for item in records}), 16)
        self.assertEqual(sum(item['kind'] == 'pump' for item in records), 4)
        for item in records:
            self.assertEqual(item['applicableDiagnostics'], 'not_validated')
            self.assertEqual(item['source'], 'organizer_provided')
            self.assertNotIn('thresholds', item)
            self.assertNotIn('remaining_resource', item['fields'])
            self.assertEqual(item['fields']['serial']['value'], item['serial'])
        pump = recommendations.get_equipment('21031004-3', self.root)
        self.assertEqual(pump['equipmentType'], 'progressive_cavity')
        self.assertNotIn('pressure_max', pump['fields'])

    def test_pump_default_is_reference_only_without_gearbox_oil(self):
        result = self.query()
        self.assertEqual(result['applicability'], 'reference_only')
        self.assertEqual([item['id'] for item in result['recommendations']], ['routine_inspection'])
        self.assertNotIn('3,7', json.dumps(result['recommendations'], ensure_ascii=False))
        self.assertIn('не подтверждены', ' '.join(result['limitations']))
        self.assertEqual([item['key'] for item in result['availableComponents']], [
            'pump', 'gearbox', 'motor', 'temperature_transmitter', 'seal', 'foundation', 'shaft_joints', 'bearing'])

    def test_gearbox_oil_targets_the_correct_group_not_another_pump(self):
        serials = ['SAL023020084_001002', 'SAL023020084_001003',
                   'SAL023020084_001004', 'SAL023020084_001001']
        for number, serial in enumerate(serials, 1):
            with self.subTest(group=number):
                result = self.query(f'21031004-{number}', 'gearbox')
                oil, = result['recommendations']
                self.assertEqual(oil['id'], 'gearbox_oil')
                self.assertEqual(oil['target']['serial'], serial)
                self.assertEqual(oil['target']['groupNumber'], number)
                self.assertIn('3,7 л', oil['text'])
                self.assertIn(serial, oil['appliesTo'])
                self.assertTrue(any('Монтажная связь' in condition for condition in oil['conditions']))
                source, = oil['sources']
                self.assertEqual(source['url'], '/api/customer/documents/lubricants/pdf#page=1')
                self.assertRegex(source['documentSha256'], r'^[a-f0-9]{64}$')

    def test_group_three_gearbox_identity_uses_its_own_passport_page(self):
        equipment = recommendations.get_equipment('SAL023020084_001004', self.root)
        source = equipment['fields']['serial']['source']
        self.assertEqual((source['documentId'], source['page']), ('drive-3-passports', 1))

    def test_direct_gearbox_serial_has_oil_but_bearing_and_motor_never_do(self):
        self.assertEqual(self.query('SAL023020084_001002')['recommendations'][0]['id'], 'gearbox_oil')
        for serial, component in [('21031004-1', 'bearing'), ('SAL023020084_001002', 'bearing'),
                                  ('230201068', None), ('230201068', 'gearbox')]:
            with self.subTest(serial=serial, component=component):
                self.assertEqual(self.query(serial, component)['recommendations'], [])

    def test_incorrect_gearbox_model_does_not_inherit_oil_specification(self):
        gear = next(item for item in self.catalog['equipment'] if item['id'] == 'gearbox-1')
        gear['model'] = gear['fields']['model']['value'] = 'Different model'
        self.write()
        self.assertEqual(self.query(component='gearbox')['recommendations'], [])

    def test_seal_reference_is_conditional_not_installed_type_claim(self):
        result = self.query(rule_ids=['seal_leak'])
        seal, = result['recommendations']
        self.assertEqual(seal['id'], 'mechanical_seal')
        self.assertEqual(seal['component'], 'seal')
        self.assertEqual(seal['sources'][0]['page'], 8)
        self.assertIn('§ 8.1.5', seal['appliesTo'])
        self.assertTrue(any('тип установленного уплотнения' in condition for condition in seal['conditions']))
        self.assertEqual(self.query(component='seal')['recommendations'][0]['id'], 'mechanical_seal')

    def test_rule_filter_does_not_apply_bearing_lubrication_to_gearbox(self):
        self.assertEqual(self.query(component='gearbox', rule_ids=['bearing_lubrication'])['recommendations'], [])
        self.assertEqual(self.query(component='gearbox', rule_ids=['oil_degradation'])['recommendations'][0]['id'], 'gearbox_oil')
        self.assertEqual(self.query(component='shaft_joints')['recommendations'][0]['id'], 'shaft_joint_lubricants')

    def test_missing_and_invalid_citations_suppress_the_whole_recommendation(self):
        original = deepcopy(self.catalog)
        bad_references = [[], [{'documentId': 'missing', 'page': 1, 'quote': 'quote'}],
                          [{'documentId': '../lubricants', 'page': 1, 'quote': 'quote'}],
                          [{'documentId': 'lubricants', 'page': 0, 'quote': 'quote'}],
                          [{'documentId': 'lubricants', 'page': 2, 'quote': 'quote'}],
                          [{'documentId': 'lubricants', 'page': True, 'quote': 'quote'}],
                          [{'documentId': 'lubricants', 'page': '1', 'quote': 'quote'}],
                          [{'documentId': 'lubricants', 'page': 1, 'quote': '  '}],
                          [{'documentId': 'operation-manual', 'page': 7, 'quote': 'wrong scope'}]]
        for references in bad_references:
            with self.subTest(references=references):
                self.catalog = deepcopy(original)
                self.catalog['requirements'][2]['sources'] = references
                self.write()
                result = self.query(component='gearbox')
                self.assertEqual(result['recommendations'], [])
                self.assertTrue(any('рекомендация скрыта' in item for item in result['limitations']))

    def test_invalid_document_metadata_and_missing_page_fail_closed(self):
        original = deepcopy(self.documents)
        for change in ({'sha256': 'bad'}, {'pageCount': True}, {'pageCount': 0}, {'pages': []},
                       {'pages': [{'page': 1}, {'page': 1}]}):
            with self.subTest(change=change):
                self.documents = deepcopy(original)
                next(item for item in self.documents['documents'] if item['id'] == 'lubricants').update(change)
                self.write()
                self.assertEqual(self.query(component='gearbox')['recommendations'], [])

    def test_citation_links_ignore_catalog_or_client_urls(self):
        doc = next(item for item in self.documents['documents'] if item['id'] == 'lubricants')
        doc['url'] = 'https://example.invalid/exfiltrate'
        self.catalog['requirements'][2]['sources'][0]['url'] = 'javascript:alert(1)'
        self.write()
        source = self.query(component='gearbox')['recommendations'][0]['sources'][0]
        self.assertEqual(source['url'], '/api/customer/documents/lubricants/pdf#page=1')

    def test_missing_identity_evidence_suppresses_asset_guidance(self):
        self.catalog['equipment'][0]['fields']['serial']['source']['page'] = 99
        self.write()
        result = self.query()
        self.assertEqual(result['applicability'], 'unverified')
        self.assertEqual(result['recommendations'], [])
        with self.assertRaisesRegex(ValueError, 'identity'):
            recommendations.get_equipment('21031004-1', self.root)

    def test_ambiguous_group_component_is_not_selected(self):
        other = deepcopy(next(item for item in self.catalog['equipment'] if item['id'] == 'gearbox-1'))
        other['serial'] = other['fields']['serial']['value'] = 'OTHER'
        self.catalog['equipment'].append(other)
        self.write()
        result = self.query(component='gearbox')
        self.assertEqual(result['recommendations'], [])
        self.assertNotIn('gearbox', [item['key'] for item in result['availableComponents']])

    def test_duplicate_registry_or_document_identity_is_rejected(self):
        self.catalog['equipment'].append(deepcopy(self.catalog['equipment'][0]))
        self.write()
        with self.assertRaisesRegex(ValueError, 'Duplicate equipment'):
            self.query()
        self.catalog['equipment'].pop()
        self.documents['documents'].append(deepcopy(self.documents['documents'][0]))
        self.write()
        with self.assertRaisesRegex(ValueError, 'Duplicate document'):
            self.query()

    def test_invalid_user_selectors_are_rejected(self):
        for serial, component, rules in [(None, None, None), (' 21031004-1', None, None),
                                         ('21031004-1', 'https://external', None),
                                         ('21031004-1', None, 'seal_leak'),
                                         ('21031004-1', None, ['invented'])]:
            with self.subTest(serial=serial, component=component, rules=rules):
                with self.assertRaises(ValueError):
                    self.query(serial, component, rules)


if __name__ == '__main__':
    unittest.main()
