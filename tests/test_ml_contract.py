import contextlib
import io
import json
import math
import tempfile
import unittest
from pathlib import Path

from ml.contract import FEATURES, labeled_partitions, read_observations, split_observations
from ml.train import counts_and_metrics, main


MANIFEST = {
    "train": ["P-1"], "validation": ["P-2"], "test": ["P-3"],
    "validation_from": "2026-07-01T00:00:00Z",
    "test_from": "2026-09-01T00:00:00Z",
}


def row(serial, timestamp, label=None, **extra):
    return {
        "serial": serial, "timestamp": timestamp, "source": "field",
        "vibration_rms_mm_s": 1.2, "bearing_fault": label, **extra,
    }


class MLContractTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def data(self, *rows):
        path = self.root / "data.jsonl"
        path.write_text("\n".join(map(json.dumps, rows)), encoding="utf-8")
        return path

    def test_csv_preserves_unknown_and_zero_separately(self):
        path = self.root / "data.csv"
        path.write_text(
            "serial,timestamp,source,vibration_rms_mm_s,bearing_temperature_c,bearing_fault,cavitation\n"
            "P-1,2026-06-01T00:00:00Z,field,1.2,,0,\n", encoding="utf-8",
        )
        observation, = read_observations(path)
        self.assertEqual(len(observation.features), len(FEATURES))
        self.assertEqual(observation.features[FEATURES.index("vibration_rms_mm_s")], 1.2)
        self.assertTrue(math.isnan(observation.features[FEATURES.index("bearing_temperature_c")]))
        self.assertEqual(observation.labels["bearing_fault"], 0)
        self.assertIsNone(observation.labels["cavitation"])
        self.assertNotIn("serial", FEATURES)
        self.assertNotIn("timestamp", FEATURES)
        self.assertNotIn("bearing_fault", FEATURES)

    def test_rejects_duplicates_after_time_zone_normalization(self):
        path = self.data(row("P-1", "2026-06-01T00:00:00Z"), row("P-1", "2026-06-01T03:00:00+03:00"))
        with self.assertRaisesRegex(ValueError, "row 2.*duplicate"):
            read_observations(path)

    def test_rejects_synthetic_observations_labels_and_unlisted_features(self):
        date = "2026-06-01T00:00:00Z"
        for record in (
            row("P-1", date, source="demo"),
            row("P-1", date, True),
            row("P-1", date, 1, vibration_rms_mm_s=float("inf")),
            row("P-1", date, 1, vibration_rms_mm_s="1.2"),
            row("P-1", date, 1, rule_diagnosis="wear"),
            row("P-1", "2026-06-01T00:00:00", 1),
        ):
            with self.subTest(record=record), self.assertRaisesRegex(ValueError, "row 1"):
                read_observations(self.data(record))

    def test_split_enforces_asset_disjointness_and_utc_boundaries(self):
        path = self.data(
            row("P-1", "2026-06-30T23:59:59Z", 1),
            row("P-1", "2026-07-01T00:00:00Z", 0),  # train asset outside time window
            row("P-2", "2026-07-01T03:00:00+03:00", 0),
            row("P-3", "2026-08-01T00:00:00Z", 0),  # test asset too early
            row("P-3", "2026-09-01T00:00:00Z", 1),
        )
        observations = read_observations(path)
        partitions = split_observations(observations, MANIFEST)
        self.assertEqual({name: [item.serial for item in rows] for name, rows in partitions.items()},
                         {"train": ["P-1"], "validation": ["P-2"], "test": ["P-3"]})
        with self.assertRaisesRegex(ValueError, "more than once"):
            split_observations(observations, MANIFEST | {"test": ["P-2", "P-3"]})
        with self.assertRaisesRegex(ValueError, "assigned time window"):
            split_observations(observations, MANIFEST | {"test": ["P-4"]})
        with self.assertRaisesRegex(ValueError, "both independently verified classes"):
            labeled_partitions(partitions, ["bearing_fault"])

    def test_validate_cli_reports_known_labels_and_excluded_rows(self):
        data = self.data(
            row("P-1", "2026-06-01T00:00:00Z", 0), row("P-1", "2026-06-02T00:00:00Z", 1),
            row("P-2", "2026-07-01T00:00:00Z", 0), row("P-2", "2026-07-02T00:00:00Z", 1),
            row("P-3", "2026-09-01T00:00:00Z", 0), row("P-3", "2026-09-02T00:00:00Z", 1),
            row("P-3", "2026-09-03T00:00:00Z"), row("P-4", "2026-09-04T00:00:00Z", 1),
        )
        split = self.root / "split.json"
        split.write_text(json.dumps(MANIFEST), encoding="utf-8")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            main(["validate", "--data", str(data), "--split", str(split), "--labels", "bearing_fault"])
        summary = json.loads(stdout.getvalue())
        self.assertEqual(summary["rows_total"], 8)
        self.assertEqual(summary["rows_outside_split"], 1)
        self.assertEqual(summary["labels"]["bearing_fault"]["test"],
                         {"rows": 2, "assets": 1, "positives": 1, "negatives": 1})

    def test_metrics_count_exact_threshold_and_rank_ties(self):
        metrics = counts_and_metrics([1, 0, 1, 0], [0.9, 0.5, 0.5, 0.1])
        self.assertEqual((metrics["tp"], metrics["fp"], metrics["tn"], metrics["fn"]), (2, 1, 1, 0))
        self.assertAlmostEqual(metrics["average_precision"], 0.5 + 0.5 * 2 / 3)
        self.assertEqual(metrics["recall"], 1.0)
        self.assertEqual(metrics["balanced_accuracy"], 0.75)
        self.assertEqual(metrics["prevalence"], 0.5)
        with self.assertRaises(ValueError):
            counts_and_metrics([1], [float("nan")])


if __name__ == "__main__":
    unittest.main()
