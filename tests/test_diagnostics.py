import unittest

from server.diagnostics import evaluate


class DiagnosticsTest(unittest.TestCase):
    def test_each_rule_can_match(self):
        cases = [
            ("bearing_lubrication", {"vibration": "rising", "temperature": "rising", "current": "normal"}, {}),
            ("cavitation", {"broadband_vibration": "rising", "inlet_pressure": "falling"}, {}),
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
        for expected, telemetry, vision in cases:
            with self.subTest(rule=expected):
                findings = evaluate(telemetry, vision)
                self.assertEqual([finding["id"] for finding in findings], [expected])
                self.assertTrue(findings[0]["evidence"])
                self.assertTrue(findings[0]["zones"])

    def test_missing_conditions_do_not_imply_normal_or_true(self):
        self.assertEqual(evaluate({}, {}), [])
        self.assertEqual(evaluate({"vibration": "rising", "temperature": "rising"}, {}), [])
        self.assertEqual(evaluate({"temperature": "rising"}, {"seal_leak": True}), [])
        self.assertEqual(evaluate({"low_frequency_vibration": "rising"}, {"foundation_issue": True}), [])
        self.assertEqual(evaluate({"bearing_temperature_delta_c": 13}, {"oil_issue": True}), [])
        self.assertEqual(evaluate({"phase_imbalance_pct": 10}, {}), [])
        self.assertEqual(evaluate({}, {"guard_issue": "false"}), [])

    def test_cavitation_optional_audio_and_promotion(self):
        basic = {"broadband_vibration": "rising", "inlet_pressure": "falling"}
        self.assertEqual(evaluate(basic | {"sound_sensor_present": True}, {}), [])
        self.assertEqual(evaluate(basic | {"sound_sensor_present": True, "gravel_noise": True}, {})[0]["id"], "cavitation")
        all_trends = basic | {"high_frequency_vibration": "rising"}
        self.assertEqual([f["id"] for f in evaluate(all_trends, {"flow_unstable": True})], ["cavitation_confirmed"])
        audio = {"sound_sensor_present": True, "gravel_noise": True}
        self.assertEqual([f["id"] for f in evaluate(all_trends | audio, {})], ["cavitation_confirmed"])
        self.assertEqual(evaluate({"inlet_pressure": "falling"}, {"flow_unstable": True}), [])

    def test_threshold_comparison_and_invalid_inputs(self):
        normal = {"vibration": "normal", "temperature": "normal"}
        self.assertEqual(evaluate(normal | {"efficiency_drop_pct": 14.99}, {}), [])
        self.assertEqual(evaluate(normal | {"efficiency_drop_pct": 15}, {}, {"efficiency_drop_pct": 20}), [])
        self.assertEqual(evaluate({"phase_imbalance_pct": 5}, {"terminal_damage": True}), [])
        self.assertEqual(evaluate({"terminal_temperature_rising": True}, {"terminal_damage": True})[0]["id"], "terminal_contact")
        self.assertEqual(evaluate({"discharge_pressure": 10}, {"gauge_pressure": 11.5}), [])
        self.assertEqual(evaluate({"discharge_pressure": 1}, {"gauge_pressure": 1.15}), [])
        self.assertEqual(evaluate({"discharge_pressure": 0}, {"gauge_pressure": 100}), [])
        self.assertEqual(evaluate({"discharge_pressure": 10}, {"gauge_pressure": True}), [])
        self.assertEqual(evaluate({"discharge_pressure": 10}, {"gauge_pressure": float("nan")}), [])
        self.assertEqual(evaluate({"discharge_pressure": 10}, {"gauge_pressure": 10 ** 999}), [])

    def test_maintenance_crossing_and_per_asset_intervals(self):
        self.assertEqual(evaluate({"hours": 4001}, {}), [])
        self.assertEqual(evaluate({"previous_hours": 4000, "hours": 4001}, {}), [])
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
