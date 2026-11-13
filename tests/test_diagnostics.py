import unittest

from server.diagnostics import DEFAULT_THRESHOLDS, RULE_METADATA, RULE_REQUIREMENTS, evaluate


class DiagnosticsTest(unittest.TestCase):
    def test_each_rule_can_match(self):
        cases = [
            ("bearing_lubrication", {"vibration": "rising", "temperature": "rising", "current": "normal"}, {}),
            ("cavitation", {"broadband_vibration": "rising", "inlet_pressure": "falling", "sound_sensor_present": False}, {}),
            ("shaft_misalignment", {"axial_vibration": "high", "temperature": "normal", "current": "slightly_high"}, {}),
            ("hydraulic_wear", {"efficiency_drop_pct": 15, "vibration": "normal", "temperature": "normal"}, {}),
            ("scheduled_maintenance", {"hours": 4000}, {}),
            ("seal_leak", {"vibration": "rising", "temperature": "rising"}, {"seal_leak": True}),
            ("foundation_looseness", {"low_frequency_vibration": "rising", "one_x_dominant": True}, {"foundation_issue": True}),
            ("oil_degradation", {"bearing_temperature_delta_c": 10, "bearing_overheat_sustained": True}, {"oil_issue": True}),
            ("terminal_contact", {"phase_imbalance_pct": 5.1}, {"terminal_damage": True}),
            ("gauge_mismatch", {"discharge_pressure": 10}, {"gauge_pressure": 8.4}),
            ("coupling_guard", {}, {"guard_issue": True}),
            ("cavitation_confirmed", {"high_frequency_vibration": "rising", "inlet_pressure": "falling"}, {"flow_unstable": True}),
        ]
        self.assertEqual(set(RULE_REQUIREMENTS), {expected for expected, _, _ in cases})
        metadata = {rule["id"]: rule for rule in RULE_METADATA}
        self.assertEqual(len(metadata), len(RULE_METADATA))
        for expected, telemetry, vision in cases:
            with self.subTest(rule=expected):
                findings = evaluate(telemetry, vision)
                self.assertEqual([finding["id"] for finding in findings], [expected])
                self.assertTrue(findings[0]["evidence"])
                self.assertTrue(findings[0]["zones"])
                self.assertEqual(findings[0]["title"], metadata[expected]["title"])
                provided = {f"{source}.{key}" for source, fields in (
                    ("telemetry", telemetry), ("vision", vision), ("thresholds", DEFAULT_THRESHOLDS),
                ) for key in fields}
                self.assertTrue(any(set(group) <= provided for group in RULE_REQUIREMENTS[expected]))

    def test_missing_conditions_do_not_imply_normal_or_true(self):
        self.assertEqual(evaluate({}, {}), [])
        self.assertEqual(evaluate({"vibration": "rising", "temperature": "rising"}, {}), [])
        self.assertEqual(evaluate({"temperature": "rising"}, {"seal_leak": True}), [])
        self.assertEqual(evaluate({"low_frequency_vibration": "rising"}, {"foundation_issue": True}), [])
        self.assertEqual(evaluate({"bearing_temperature_delta_c": 13}, {"oil_issue": True}), [])
        self.assertEqual(evaluate({"phase_imbalance_pct": 10}, {}), [])
        self.assertEqual(evaluate({}, {"guard_issue": "false"}), [])

    def test_a_single_changed_observation_can_clear_each_finding(self):
        cases = [
            ("bearing_lubrication", {"vibration": "rising", "temperature": "rising", "current": "high"}, {}),
            ("cavitation", {"broadband_vibration": "rising", "inlet_pressure": "normal", "sound_sensor_present": False}, {}),
            ("shaft_misalignment", {"axial_vibration": "high", "temperature": "high", "current": "slightly_high"}, {}),
            ("hydraulic_wear", {"efficiency_drop_pct": 15, "vibration": "high", "temperature": "normal"}, {}),
            ("scheduled_maintenance", {"previous_hours": 3998, "hours": 3999}, {}),
            ("seal_leak", {"vibration": "rising", "temperature": "normal"}, {"seal_leak": True}),
            ("foundation_looseness", {"low_frequency_vibration": "rising", "one_x_dominant": False}, {"foundation_issue": True}),
            ("oil_degradation", {"bearing_temperature_delta_c": 9.99, "bearing_overheat_sustained": True}, {"oil_issue": True}),
            ("terminal_contact", {"phase_imbalance_pct": 5, "terminal_temperature_rising": False}, {"terminal_damage": True}),
            ("gauge_mismatch", {"discharge_pressure": 0.8}, {"gauge_pressure": 0.92}),
            ("coupling_guard", {}, {"guard_issue": False}),
            ("cavitation_confirmed", {"high_frequency_vibration": "normal", "inlet_pressure": "falling"}, {"flow_unstable": True}),
        ]
        for rule, telemetry, vision in cases:
            with self.subTest(rule=rule):
                self.assertEqual(evaluate(telemetry, vision), [])

    def test_cavitation_optional_audio_and_promotion(self):
        basic = {"broadband_vibration": "rising", "inlet_pressure": "falling"}
        self.assertEqual(evaluate(basic, {}), [])
        self.assertEqual(evaluate(basic | {"sound_sensor_present": None, "gravel_noise": True}, {}), [])
        self.assertEqual(evaluate(basic | {"sound_sensor_present": True}, {}), [])
        self.assertEqual(evaluate(basic | {"sound_sensor_present": "false"}, {}), [])
        self.assertEqual(evaluate(basic | {"sound_sensor_present": False}, {})[0]["id"], "cavitation")
        self.assertEqual(evaluate(basic | {"sound_sensor_present": True, "gravel_noise": True}, {})[0]["id"], "cavitation")
        all_trends = basic | {"high_frequency_vibration": "rising"}
        self.assertEqual([f["id"] for f in evaluate(all_trends | {"sound_sensor_present": False}, {"flow_unstable": True})], ["cavitation_confirmed"])
        self.assertEqual([f["id"] for f in evaluate(all_trends, {"flow_unstable": True})], ["cavitation_confirmed"])
        audio = {"sound_sensor_present": True, "gravel_noise": True}
        self.assertEqual([f["id"] for f in evaluate(all_trends | audio, {})], ["cavitation_confirmed"])
        self.assertEqual(evaluate({"inlet_pressure": "falling"}, {"flow_unstable": True}), [])

    def test_misalignment_can_use_coupling_instead_of_axial_measurement(self):
        telemetry = {"coupling_vibration": "high", "temperature": "normal", "current": "slightly_high"}
        findings = evaluate(telemetry, {})
        self.assertEqual([finding["id"] for finding in findings], ["shaft_misalignment"])
        self.assertIn("Вибрация на муфте высокая", findings[0]["evidence"])

    def test_threshold_comparison_and_invalid_inputs(self):
        normal = {"vibration": "normal", "temperature": "normal"}
        self.assertEqual(evaluate(normal | {"efficiency_drop_pct": 14.99}, {}), [])
        self.assertEqual(evaluate(normal | {"efficiency_drop_pct": 15}, {}, {"efficiency_drop_pct": 20}), [])
        self.assertEqual(evaluate(normal | {"efficiency_drop_pct": 101}, {}), [])
        self.assertEqual(evaluate({"phase_imbalance_pct": 5}, {"terminal_damage": True}), [])
        self.assertEqual(evaluate({"terminal_temperature_rising": True}, {"terminal_damage": True})[0]["id"], "terminal_contact")
        self.assertEqual(evaluate({"discharge_pressure": 10}, {"gauge_pressure": 11.5}), [])
        self.assertEqual(evaluate({"discharge_pressure": 1}, {"gauge_pressure": 1.15}), [])
        close_to_limit = evaluate({"discharge_pressure": 1}, {"gauge_pressure": 1.15000000000001})
        self.assertEqual([finding["id"] for finding in close_to_limit], ["gauge_mismatch"])
        self.assertEqual(evaluate({"discharge_pressure": 0}, {"gauge_pressure": 100}), [])
        self.assertEqual(evaluate({"discharge_pressure": 10}, {"gauge_pressure": True}), [])
        self.assertEqual(evaluate({"discharge_pressure": 10}, {"gauge_pressure": float("nan")}), [])
        self.assertEqual(evaluate({"discharge_pressure": 10}, {"gauge_pressure": 10 ** 999}), [])

    def test_oil_rule_requires_sustained_heat_and_accepts_larger_excess(self):
        for temperature in (10, 12.5, 15, 20):
            with self.subTest(temperature=temperature):
                telemetry = {"bearing_temperature_delta_c": temperature, "bearing_overheat_sustained": True}
                self.assertEqual([finding["id"] for finding in evaluate(telemetry, {"oil_issue": True})], ["oil_degradation"])
                self.assertEqual(evaluate(telemetry | {"bearing_overheat_sustained": False}, {"oil_issue": True}), [])
                self.assertEqual(evaluate(telemetry, {"oil_issue": None}), [])

    def test_invalid_profile_thresholds_disable_their_conditions(self):
        telemetry = {
            "efficiency_drop_pct": 20, "vibration": "normal", "temperature": "normal",
            "phase_imbalance_pct": 20, "bearing_temperature_delta_c": 15,
            "bearing_overheat_sustained": True, "discharge_pressure": 10, "hours": 4000,
        }
        vision = {"terminal_damage": True, "oil_issue": True, "gauge_pressure": 5}
        invalid = {
            "efficiency_drop_pct": "15", "phase_imbalance_pct": False,
            "bearing_temperature_delta_c": -2, "pressure_mismatch_pct": {},
            "service_intervals_hours": None,
        }
        self.assertEqual(evaluate(telemetry, vision, invalid), [])
        self.assertEqual([f["id"] for f in evaluate(telemetry, vision, invalid | {
            "service_intervals_hours": [4000, 4000, {}, None, False, -1, "4000"],
        })], ["scheduled_maintenance"])

    def test_maintenance_crossing_and_per_asset_intervals(self):
        self.assertEqual(evaluate({"hours": 4001}, {}), [])
        self.assertEqual(evaluate({"previous_hours": 4000, "hours": 4001}, {}), [])
        self.assertEqual(evaluate({"previous_hours": 8001, "hours": 4001}, {}), [])
        self.assertEqual(evaluate({"previous_hours": 8001, "hours": 4000}, {})[0]["id"], "scheduled_maintenance")
        self.assertEqual(evaluate({"previous_hours": 3990, "hours": 4010}, {})[0]["id"], "scheduled_maintenance")
        self.assertEqual(evaluate({"hours": 8000}, {})[0]["id"], "scheduled_maintenance")
        self.assertEqual(evaluate({"hours": 16000}, {})[0]["id"], "scheduled_maintenance")
        self.assertEqual(evaluate({"hours": 4000}, {}, {"service_intervals_hours": [2000]}), [])
        self.assertEqual(evaluate({"hours": 2000}, {}, {"service_intervals_hours": [2000]})[0]["id"], "scheduled_maintenance")
        reached = evaluate({"previous_hours": 3900, "hours": 8050}, {})[0]
        self.assertIn("4000, 8000", reached["evidence"][0])

    def test_critical_diagnostics_are_listed_before_other_matches(self):
        telemetry = {"vibration": "rising", "temperature": "rising", "current": "normal", "hours": 4000}
        findings = evaluate(telemetry, {"seal_leak": True, "guard_issue": True})
        self.assertEqual([finding["id"] for finding in findings], [
            "seal_leak", "coupling_guard", "bearing_lubrication", "scheduled_maintenance",
        ])


if __name__ == "__main__":
    unittest.main()
