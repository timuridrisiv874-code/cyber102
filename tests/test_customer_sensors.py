import json
from pathlib import Path
import unittest

from data_tools.import_customer_sensors import assessment, assess_group, join_measurements, timestamp


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/customer"


class SensorAssessmentTests(unittest.TestCase):
    def test_documented_boundaries_are_strict(self):
        rule = {"unit": "mm/s", "warning": 2.8, "alarm": 4.5}
        self.assertEqual(assessment(2.8, rule)["level"], "below_threshold")
        self.assertEqual(assessment(2.81, rule)["level"], "warning")
        self.assertEqual(assessment(4.5, rule)["level"], "warning")
        self.assertEqual(assessment(4.51, rule)["level"], "alarm")

    def test_missing_input_and_missing_threshold_remain_unknown(self):
        rule = {"unit": "g", "warning": 2.5, "alarm": None}
        self.assertEqual(assessment(None, rule)["level"], "unknown")
        self.assertEqual(assessment(0, rule)["level"], "below_threshold")
        self.assertEqual(assessment(6, rule)["level"], "warning")
        self.assertEqual(assessment(4, {**rule, "warning": None})["level"], "unknown")

    def test_missing_required_reading_cannot_produce_normal_label(self):
        result = assess_group(
            {"one_x_mm_s": 1, "two_x_mm_s": None, "source_label": "Норма"},
            {
                "one_x": {"unit": "mm/s", "warning": 2.8, "alarm": 4.5},
                "two_x": {"unit": "mm/s", "warning": 1.8, "alarm": 3},
            },
            {"one_x": "one_x_mm_s", "two_x": "two_x_mm_s"},
        )
        self.assertIsNone(result["computed_label"])
        self.assertIsNone(result["disagreement"])
        self.assertEqual(result["two_x"]["level"], "unknown")

    def test_join_rejects_mismatched_timestamps_or_rpm(self):
        when = timestamp("2026-11-10 08:00")
        other = timestamp("2026-11-10 09:00")
        with self.assertRaisesRegex(ValueError, "timestamps differ"):
            join_measurements({when: {"rpm": 2963}}, {other: {"rpm": 2963}}, {})
        with self.assertRaisesRegex(ValueError, "RPM is missing or inconsistent"):
            join_measurements({when: {"rpm": 2963}}, {when: {"rpm": 2953}}, {})


class ImportedSensorPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = json.loads((DATA / "sensors.json").read_text(encoding="utf-8"))
        cls.report = json.loads((DATA / "sensor-import-report.json").read_text(encoding="utf-8"))

    def test_provenance_covers_all_rows_without_assigning_equipment(self):
        dataset = self.dataset["dataset"]
        self.assertIsNone(dataset["equipmentSerial"])
        self.assertIsNone(dataset["timezone"])
        self.assertEqual(dataset["source"], "customer_example")
        self.assertEqual(dataset["intervalHours"], 1)
        self.assertEqual(len(self.dataset["rows"]), 500)
        sources = {source["id"]: source for source in self.dataset["sources"]}
        self.assertEqual(len(sources), 4)
        for source in sources.values():
            self.assertRegex(source["sha256"], r"^[a-f0-9]{64}$")
        for number, row in enumerate(self.dataset["rows"], start=2):
            self.assertNotIn("Z", row["timestamp"])
            self.assertIsNone(timestamp(row["timestamp"]).tzinfo)
            for group, sheet in (("vibration", "Вибрация"), ("temperature", "Термометрия")):
                evidence = row["provenance"][group]
                self.assertEqual(evidence["sheet"], sheet)
                self.assertEqual(evidence["row"], number)
                self.assertEqual(evidence["file"], sources[evidence["sourceId"]]["file"])

    def test_reports_missing_hf_alarm_boundary_without_changing_source_label(self):
        high_frequency = self.dataset["thresholds"]["vibration"]["high_frequency"]
        self.assertEqual(high_frequency["warning"], 2.5)
        self.assertIsNone(high_frequency["alarm"])
        self.assertGreater(high_frequency["source"]["paragraph"], 0)
        self.assertEqual(self.report["summary"]["futureDatedRows"], 412)
        self.assertEqual(self.report["summary"]["temperature"]["labelDisagreements"], 0)
        self.assertEqual(len(self.report["labelDisagreements"]), 18)
        for disagreement in self.report["labelDisagreements"]:
            self.assertEqual(disagreement["sourceLabel"], "Тревога: Дефект подшипника")
            self.assertEqual(disagreement["computedLabel"], "Пред: Износ/Шум")


if __name__ == "__main__":
    unittest.main()
