import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ml.contract import FEATURES, Observation, utc_timestamp
from ml.train import autogluon_fit_options, main, train, train_autogluon


class Frame:
    """Only the tabular boundary used by the trainer; no ML dependencies needed."""
    def __init__(self, rows, columns):
        self.rows = list(rows)
        self.columns = list(columns)
        self.labels = {}

    def __setitem__(self, key, values):
        self.columns.append(key)
        self.labels[key] = list(values)


class Predictor:
    instances = []

    def __init__(self, **options):
        self.options = options
        self.model_best = "WeightedEnsemble_L2"
        self.predictions = []
        self.instances.append(self)

    def fit(self, training, **options):
        self.training, self.fit_options = training, options
        return self

    def leaderboard(self, **options):
        # A test frame supplied here would silently turn model selection into leakage.
        assert options == {"display": False}
        return SimpleNamespace(to_dict=lambda orient: [
            {"model": self.model_best, "score_val": -0.31},
            {"model": "CatBoost", "score_val": -0.4},
        ])

    def predict_proba(self, frame, **options):
        assert options == {"as_multiclass": False}
        self.predictions.append(frame)
        return SimpleNamespace(tolist=lambda: [0.2 if i % 2 == 0 else 0.8 for i in range(len(frame.rows))])

    def save_space(self, **options):
        self.cleanup = options


def partitions():
    def rows(serial, month, labels, marker):
        return [Observation(serial, utc_timestamp(f"2026-{month:02d}-{day:02d}T00:00:00Z"),
                            (marker + day,) + (float("nan"),) * (len(FEATURES) - 1),
                            {"bearing_fault": label}) for day, label in enumerate(labels, 1)]
    return {"train": rows("P-1", 6, [0, 0, 0, 1], 100),
            "validation": rows("P-2", 7, [1, 1, 0], 200),
            "test": rows("P-3", 9, [0, 1], 300)}


class MLTrainingTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.args = argparse.Namespace(engine="autogluon", gpu=False, cpus=4, seed=42,
                                       time_limit=300, memory_gb=4.0, output=self.root / "model")
        Predictor.instances.clear()
        modules = {"autogluon": SimpleNamespace(),
                   "autogluon.tabular": SimpleNamespace(TabularPredictor=Predictor),
                   "pandas": SimpleNamespace(DataFrame=Frame)}
        self.dependencies = patch.dict("sys.modules", modules)
        self.dependencies.start()
        self.addCleanup(self.dependencies.stop)

    def test_fit_keeps_test_labels_and_metadata_out_of_training(self):
        data = partitions()
        _, scores, details = train_autogluon("bearing_fault", data, self.root, self.args)
        predictor = Predictor.instances[-1]
        self.assertEqual(predictor.training.rows, [row.features for row in data["train"]])
        self.assertEqual(predictor.fit_options["tuning_data"].rows,
                         [row.features for row in data["validation"]])
        self.assertEqual(predictor.training.columns, [*FEATURES, "bearing_fault"])
        self.assertEqual(predictor.fit_options["tuning_data"].labels["bearing_fault"], [1, 1, 0])
        self.assertEqual([frame.columns for frame in predictor.predictions], [list(FEATURES)] * 2)
        self.assertEqual(predictor.predictions[-1].rows, [row.features for row in data["test"]])
        self.assertEqual(set(scores), {"validation", "test"})
        self.assertEqual(details["validation_candidates"][0]["validation_log_loss"], 0.31)
        self.assertEqual(predictor.cleanup, {"remove_data": True})

    def test_cpu_budget_disables_random_folds_and_pretrained_models(self):
        options = autogluon_fit_options(self.args)
        self.assertEqual(set(options["hyperparameters"]), {"CAT", "RF"})
        self.assertEqual((options["num_cpus"], options["num_gpus"], options["time_limit"],
                          options["memory_limit"], options["fit_strategy"]), (4, 0, 300, 4.0, "sequential"))
        for option in ("num_bag_folds", "num_stack_levels", "auto_stack", "dynamic_stacking",
                       "refit_full", "calibrate_decision_threshold"):
            self.assertFalse(options[option])
        self.args.gpu = True
        with self.assertRaisesRegex(ValueError, "uses CPU"):
            autogluon_fit_options(self.args)

    def test_report_uses_train_only_baseline_and_marks_experiment(self):
        with patch("ml.train.version", return_value="1.6.1"):
            report = train(self.args, {"bearing_fault": partitions()}, {}, {"data": "example"})
        saved = json.loads((self.args.output / "report.json").read_text())
        self.assertEqual(report, saved)
        self.assertFalse(saved["runtime_ready"])
        model = saved["models"]["bearing_fault"]
        baseline = model["train_prevalence_baseline"]
        self.assertEqual(baseline["probability"], 0.25)
        self.assertEqual(baseline["metrics"]["test"]["average_precision"], 0.5)
        self.assertEqual(baseline["metrics"]["test"]["fn"], 1)
        self.assertIn("fit", model)
        self.assertIn("elapsed_seconds", model)

    def cli_files(self):
        path = self.root / "data.jsonl"
        records = []
        for rows in partitions().values():
            for row in rows:
                records.append({"serial": row.serial, "timestamp": row.timestamp.isoformat(),
                                "source": "field", FEATURES[0]: row.features[0], **row.labels})
        path.write_text("\n".join(json.dumps(row) for row in records))
        split = self.root / "split.json"
        split.write_text(json.dumps({"train": ["P-1"], "validation": ["P-2"], "test": ["P-3"],
                                     "validation_from": "2026-07-01T00:00:00Z",
                                     "test_from": "2026-09-01T00:00:00Z"}))
        return ["train", "--data", str(path), "--split", str(split), "--labels", "bearing_fault",
                "--output", str(self.args.output)]

    def test_cli_defaults_to_autogluon_but_retains_catboost(self):
        for extra, expected in (([], "autogluon"), (["--engine", "catboost", "--gpu"], "catboost")):
            with patch("ml.train.train") as fitting, contextlib.redirect_stdout(io.StringIO()):
                main(self.cli_files() + extra)
            args = fitting.call_args.args[0]
            self.assertEqual(args.engine, expected)
            self.assertEqual(args.memory_gb, 4.0)

    def test_cli_rejects_nonfinite_memory_before_training(self):
        for value in ("0", "-1", "nan", "inf"):
            with self.subTest(value=value), patch("ml.train.train") as fitting:
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    main(self.cli_files() + ["--memory-gb", value])
                fitting.assert_not_called()


if __name__ == "__main__":
    unittest.main()
