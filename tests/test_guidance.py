import json
import unittest
from copy import deepcopy

from server.demo_data import SCENARIOS
from server.diagnostics import RULE_METADATA, evaluate
from server.guidance import assess


class GuidanceTest(unittest.TestCase):
    def rule(self, rule_id, telemetry=None, vision=None, thresholds=None):
        response = assess(telemetry or {}, vision or {}, thresholds)
        return next(row for row in response['rules'] if row['id'] == rule_id)

    def test_all_twelve_rules_report_the_actual_findings_and_met_evidence(self):
        cases = [
            ('bearing_lubrication', {'vibration': 'rising', 'temperature': 'rising', 'current': 'normal'}, {}),
            ('cavitation', {'broadband_vibration': 'rising', 'inlet_pressure': 'falling', 'sound_sensor_present': False}, {}),
            ('shaft_misalignment', {'axial_vibration': 'high', 'temperature': 'normal', 'current': 'slightly_high'}, {}),
            ('hydraulic_wear', {'efficiency_drop_pct': 15, 'vibration': 'normal', 'temperature': 'normal'}, {}),
            ('scheduled_maintenance', {'hours': 4000}, {}),
            ('seal_leak', {'vibration': 'rising', 'temperature': 'rising'}, {'seal_leak': True}),
            ('foundation_looseness', {'low_frequency_vibration': 'rising', 'one_x_dominant': True}, {'foundation_issue': True}),
            ('oil_degradation', {'bearing_temperature_delta_c': 10, 'bearing_overheat_sustained': True}, {'oil_issue': True}),
            ('terminal_contact', {'phase_imbalance_pct': 5.1}, {'terminal_damage': True}),
            ('gauge_mismatch', {'discharge_pressure': 10}, {'gauge_pressure': 8.4}),
            ('coupling_guard', {}, {'guard_issue': True}),
            ('cavitation_confirmed', {'high_frequency_vibration': 'rising', 'inlet_pressure': 'falling'}, {'flow_unstable': True}),
        ]
        self.assertEqual({rule['id'] for rule in RULE_METADATA}, {case[0] for case in cases})
        for rule_id, telemetry, vision in cases:
            with self.subTest(rule=rule_id):
                response = assess(telemetry, vision)
                actual = {row['id'] for row in response['rules'] if row['status'] == 'triggered'}
                self.assertEqual(actual, {rule_id})
                row = self.rule(rule_id, telemetry, vision)
                self.assertTrue(all(check['status'] == 'met' for check in row['checks']))
                self.assertEqual(row['missing'], [])
                self.assertEqual(row['nextSteps'], [])

    def test_every_demo_matches_diagnosis_and_healthy_is_fully_evaluated(self):
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario['id']):
                response = assess(scenario['telemetry'], scenario['vision'])
                actual = {row['id'] for row in response['rules'] if row['status'] == 'triggered'}
                self.assertEqual(actual, {finding['id'] for finding in evaluate(scenario['telemetry'], scenario['vision'])})
                self.assertEqual(response['summary']['total'], 12)
                self.assertEqual(response['summary']['assessed'], 12)
                self.assertEqual(response['nextChecks'], [])
        healthy = assess(SCENARIOS[0]['telemetry'], SCENARIOS[0]['vision'])
        self.assertEqual(healthy['summary']['notTriggered'], 12)

    def test_missing_current_is_first_useful_check_and_not_a_normal_reading(self):
        response = assess({'vibration': 'rising', 'temperature': 'rising'}, {})
        bearing = next(row for row in response['rules'] if row['id'] == 'bearing_lubrication')
        self.assertEqual(bearing['status'], 'insufficient_data')
        self.assertEqual(bearing['missing'], ['telemetry.current'])
        self.assertEqual(response['nextChecks'][0]['field'], 'telemetry.current')
        self.assertEqual(bearing['checks'][-1]['status'], 'unknown')
        self.assertEqual([c['status'] for c in bearing['checks'][:2]], ['met', 'met'])
        self.assertIn('По фото ток не определяется', response['nextChecks'][0]['instruction'])

    def test_explicit_false_and_missing_are_different(self):
        for value in (None, 0, 1, '', 'false', 'true', [], {}):
            with self.subTest(value=value):
                row = self.rule('coupling_guard', vision={'guard_issue': value})
                self.assertEqual(row['status'], 'insufficient_data')
                self.assertEqual(row['missing'], ['vision.guard_issue'])
        row = self.rule('coupling_guard', vision={'guard_issue': False})
        self.assertEqual(row['status'], 'not_triggered')
        self.assertEqual(row['checks'][0]['value'], False)
        self.assertEqual(row['checks'][0]['status'], 'not_met')

    def test_a_disproven_conjunct_stops_a_rule_but_does_not_hide_unknown_checks(self):
        row = self.rule('bearing_lubrication', {'vibration': 'normal'})
        self.assertEqual(row['status'], 'not_triggered')
        self.assertEqual([check['status'] for check in row['checks']], ['not_met', 'unknown', 'unknown'])
        self.assertEqual(row['missing'], [])
        self.assertEqual(row['nextSteps'], [])

    def test_unknown_acoustic_availability_does_not_mean_no_sensor(self):
        telemetry = {'broadband_vibration': 'rising', 'inlet_pressure': 'falling'}
        for availability in (None, 'false', 0, 1):
            row = self.rule('cavitation', telemetry | {'sound_sensor_present': availability})
            self.assertEqual(row['status'], 'insufficient_data')
            self.assertEqual(row['missing'], ['telemetry.sound_sensor_present'])
        row = self.rule('cavitation', telemetry | {'sound_sensor_present': False})
        self.assertEqual(row['status'], 'triggered')
        self.assertNotIn('telemetry.gravel_noise', [check['field'] for check in row['checks']])
        row = self.rule('cavitation', telemetry | {'sound_sensor_present': True})
        self.assertEqual(row['status'], 'insufficient_data')
        self.assertEqual(row['missing'], ['telemetry.gravel_noise'])
        self.assertEqual(self.rule('cavitation', telemetry | {'sound_sensor_present': True, 'gravel_noise': False})['status'], 'not_triggered')
        self.assertEqual(self.rule('cavitation', telemetry | {'sound_sensor_present': True, 'gravel_noise': True})['status'], 'triggered')

    def test_confirmed_cavitation_uses_visual_or_audio_without_requiring_both(self):
        telemetry = {'high_frequency_vibration': 'rising', 'inlet_pressure': 'falling'}
        visual = self.rule('cavitation_confirmed', telemetry, {'flow_unstable': True})
        self.assertEqual(visual['status'], 'triggered')
        self.assertFalse(any('sound' in check['field'] for check in visual['checks']))
        acoustic = self.rule('cavitation_confirmed', telemetry | {'sound_sensor_present': True, 'gravel_noise': True}, {'flow_unstable': False})
        self.assertEqual(acoustic['status'], 'triggered')
        self.assertEqual(acoustic['alternatives'][0]['status'], 'not_met')
        missing = self.rule('cavitation_confirmed', telemetry | {'sound_sensor_present': True}, {'flow_unstable': False})
        self.assertEqual(missing['status'], 'insufficient_data')
        self.assertEqual(missing['missing'], ['telemetry.gravel_noise'])
        unavailable = self.rule('cavitation_confirmed', telemetry | {'sound_sensor_present': False, 'gravel_noise': True}, {'flow_unstable': False})
        self.assertEqual(unavailable['status'], 'not_triggered')

    def test_confirmed_cavitation_explains_suppressed_preliminary_duplicate(self):
        telemetry = {'broadband_vibration': 'rising', 'high_frequency_vibration': 'rising',
                     'inlet_pressure': 'falling', 'sound_sensor_present': False}
        row = self.rule('cavitation', telemetry, {'flow_unstable': True})
        self.assertEqual(row['status'], 'not_triggered')
        self.assertEqual(row['supersededBy'], 'cavitation_confirmed')
        self.assertEqual(row['nextSteps'], [])
        self.assertIn('не дублируется', row['note'])

    def test_misalignment_uses_either_vibration_and_requests_only_viable_branch(self):
        common = {'temperature': 'normal', 'current': 'slightly_high'}
        self.assertEqual(self.rule('shaft_misalignment', common | {'coupling_vibration': 'high'})['status'], 'triggered')
        row = self.rule('shaft_misalignment', common | {'axial_vibration': 'normal'})
        self.assertEqual(row['status'], 'insufficient_data')
        self.assertEqual(row['missing'], ['telemetry.coupling_vibration'])
        self.assertEqual(self.rule('shaft_misalignment', common | {'axial_vibration': 'normal', 'coupling_vibration': 'normal'})['status'], 'not_triggered')

    def test_electrical_temperature_alternative_does_not_require_phase_data(self):
        row = self.rule('terminal_contact', {'terminal_temperature_rising': True}, {'terminal_damage': True}, {'phase_imbalance_pct': None})
        self.assertEqual(row['status'], 'triggered')
        self.assertEqual(row['missing'], [])
        self.assertEqual(row['selectedAlternative'], 1)
        missing = self.rule('terminal_contact', {'phase_imbalance_pct': 5}, {'terminal_damage': True})
        self.assertEqual(missing['status'], 'insufficient_data')
        self.assertEqual(missing['missing'], ['telemetry.terminal_temperature_rising'])
        clear = self.rule('terminal_contact', {'phase_imbalance_pct': 5, 'terminal_temperature_rising': False}, {'terminal_damage': True})
        self.assertEqual(clear['status'], 'not_triggered')

    def test_nonfinite_string_boolean_and_out_of_range_measurements_are_unknown(self):
        normal = {'vibration': 'normal', 'temperature': 'normal'}
        for invalid in (None, True, False, '15', float('nan'), float('inf'), float('-inf'), 10 ** 999, -1, 101, {}):
            with self.subTest(value=type(invalid).__name__):
                row = self.rule('hydraulic_wear', normal | {'efficiency_drop_pct': invalid})
                self.assertEqual(row['status'], 'insufficient_data')
                self.assertEqual(row['missing'], ['telemetry.efficiency_drop_pct'])
                json.dumps(row, allow_nan=False)
        for invalid in (None, 'normal', [], 1):
            row = self.rule('bearing_lubrication', {'vibration': 'rising', 'temperature': 'rising', 'current': invalid})
            self.assertEqual(row['status'], 'triggered' if invalid == 'normal' else 'insufficient_data')

    def test_invalid_threshold_requests_profile_instead_of_remeasuring_good_value(self):
        telemetry = {'efficiency_drop_pct': 20, 'vibration': 'normal', 'temperature': 'normal'}
        for invalid in (None, False, '15', {}, -1, 0, float('nan'), float('inf')):
            with self.subTest(value=invalid):
                row = self.rule('hydraulic_wear', telemetry, thresholds={'efficiency_drop_pct': invalid})
                self.assertEqual(row['status'], 'insufficient_data')
                self.assertEqual(row['missing'], ['thresholds.efficiency_drop_pct'])
                self.assertEqual(row['nextSteps'][0]['source'], 'thresholds')
                json.dumps(row, allow_nan=False)
        self.assertEqual(self.rule('hydraulic_wear', telemetry, thresholds={'efficiency_drop_pct': 20})['status'], 'triggered')
        self.assertEqual(self.rule('hydraulic_wear', telemetry, thresholds={'efficiency_drop_pct': 20.1})['status'], 'not_triggered')

    def test_zero_is_valid_for_strict_phase_and_pressure_limits(self):
        self.assertEqual(self.rule('terminal_contact', {'phase_imbalance_pct': 0.1}, {'terminal_damage': True}, {'phase_imbalance_pct': 0})['status'], 'triggered')
        self.assertEqual(self.rule('gauge_mismatch', {'discharge_pressure': 1}, {'gauge_pressure': 1.01}, {'pressure_mismatch_pct': 0})['status'], 'triggered')

    def test_oil_temperature_requires_sustained_heat_and_preserves_inclusive_limit(self):
        base = {'bearing_overheat_sustained': True}
        for delta, state in ((9.99, 'not_triggered'), (10, 'triggered'), (25, 'triggered')):
            self.assertEqual(self.rule('oil_degradation', base | {'bearing_temperature_delta_c': delta}, {'oil_issue': True})['status'], state)
        row = self.rule('oil_degradation', {'bearing_temperature_delta_c': 12}, {'oil_issue': True})
        self.assertEqual(row['missing'], ['telemetry.bearing_overheat_sustained'])

    def test_pressure_strict_boundary_and_zero_reference(self):
        for measured, state in ((1.15, 'not_triggered'), (1.15000000000001, 'triggered'), (.85, 'not_triggered'), (.849999999999, 'triggered'), (0, 'triggered')):
            row = self.rule('gauge_mismatch', {'discharge_pressure': 1}, {'gauge_pressure': measured})
            self.assertEqual(row['status'], state)
        zero = self.rule('gauge_mismatch', {'discharge_pressure': 0}, {'gauge_pressure': 1})
        self.assertEqual(zero['status'], 'insufficient_data')
        self.assertEqual(zero['missing'], ['telemetry.discharge_pressure'])
        no_gauge = self.rule('gauge_mismatch', {'discharge_pressure': 1})
        self.assertEqual(no_gauge['missing'], ['vision.gauge_pressure'])

    def test_maintenance_requires_a_previous_counter_only_when_crossing_is_uncertain(self):
        cases = [
            ({'hours': 3999}, 'not_triggered', []),
            ({'hours': 4000}, 'triggered', []),
            ({'hours': 4001}, 'insufficient_data', ['telemetry.previous_hours']),
            ({'previous_hours': 3990, 'hours': 4001}, 'triggered', []),
            ({'previous_hours': 4000, 'hours': 4001}, 'not_triggered', []),
            ({'previous_hours': 4001, 'hours': 4001}, 'not_triggered', []),
            ({'previous_hours': 8001, 'hours': 4001}, 'insufficient_data', ['telemetry.previous_hours']),
            ({'previous_hours': 8001, 'hours': 4000}, 'triggered', []),
            ({'previous_hours': '3999', 'hours': 4001}, 'insufficient_data', ['telemetry.previous_hours']),
            ({'previous_hours': 3999}, 'insufficient_data', ['telemetry.hours']),
        ]
        for telemetry, status, missing in cases:
            with self.subTest(telemetry=telemetry):
                row = self.rule('scheduled_maintenance', telemetry)
                self.assertEqual(row['status'], status)
                self.assertEqual(row['missing'], missing)

    def test_maintenance_uses_equipment_profile_and_filters_invalid_intervals(self):
        valid_mixed = {'service_intervals_hours': [4000, 4000, None, {}, False, -1, '8000']}
        row = self.rule('scheduled_maintenance', {'previous_hours': 3900, 'hours': 8001}, thresholds=valid_mixed)
        self.assertEqual(row['status'], 'triggered')
        self.assertEqual(row['checks'][-1]['value'], [4000])
        for configured in (None, [], {}, [False, 0, -1, '4000']):
            row = self.rule('scheduled_maintenance', {'hours': 4000}, thresholds={'service_intervals_hours': configured})
            self.assertEqual(row['status'], 'insufficient_data')
            self.assertEqual(row['missing'], ['thresholds.service_intervals_hours'])
        self.assertEqual(self.rule('scheduled_maintenance', {'hours': 2000}, thresholds={'service_intervals_hours': [2000]})['status'], 'triggered')

    def test_next_checks_are_deduplicated_deterministic_and_contain_only_actionable_missing_fields(self):
        response = assess({'vibration': 'rising'}, {'seal_leak': True})
        steps = response['nextChecks']
        fields = [step['field'] for step in steps]
        self.assertEqual(len(fields), len(set(fields)))
        self.assertEqual(fields[0], 'telemetry.temperature')
        self.assertEqual([step['order'] for step in steps], list(range(1, len(steps) + 1)))
        self.assertEqual(response, assess({'vibration': 'rising'}, {'seal_leak': True}))
        temperature = next(step for step in steps if step['field'] == 'telemetry.temperature')
        self.assertIn('seal_leak', temperature['ruleIds'])
        self.assertIn('bearing_lubrication', temperature['ruleIds'])
        self.assertTrue(all(not field.startswith('comparison.') for field in fields))
        self.assertTrue(all(step['instruction'] for step in steps))
        self.assertNotIn('confidence', json.dumps(response))

    def test_empty_invalid_and_mutable_inputs_leave_a_json_safe_honest_result(self):
        for telemetry, vision, thresholds in (({}, {}, None), (None, None, None), ([], 'invalid', [])):
            response = assess(telemetry, vision, thresholds)
            self.assertEqual(response['summary'], {'total': 12, 'triggered': 0, 'notTriggered': 0, 'insufficientData': 12, 'assessed': 0})
            self.assertTrue(all(row['missing'] and row['nextSteps'] for row in response['rules']))
            self.assertNotIn('"_signal"', json.dumps(response, allow_nan=False))
        original = deepcopy(SCENARIOS[-1])
        scenario = deepcopy(original)
        thresholds = {'service_intervals_hours': [4000, 8000, 16000]}
        assess(scenario['telemetry'], scenario['vision'], thresholds)
        self.assertEqual(scenario, original)
        self.assertEqual(thresholds['service_intervals_hours'], [4000, 8000, 16000])


if __name__ == '__main__':
    unittest.main()
