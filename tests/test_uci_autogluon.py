import io
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import numpy as np

from ml import benchmark_uci, benchmark_uci_autogluon as experiment


class UCIAutoGluonTest(unittest.TestCase):
    def test_features_capture_cycle_shape_and_handle_constant_channels(self):
        values = np.vstack((np.arange(60), np.full(60, 7), np.arange(60)[::-1])).astype(float)
        base, extra, seconds = experiment.sensor_features(values, np)
        self.assertEqual((base.shape, extra.shape, seconds.shape), ((3, 8), (3, 15), (3, 60)))
        self.assertAlmostEqual(extra[0, experiment.EXTRA.index('slope_per_second')], 1)
        self.assertAlmostEqual(extra[2, experiment.EXTRA.index('slope_per_second')], -1)
        self.assertAlmostEqual(extra[0, experiment.EXTRA.index('lag1_correlation')], 1)
        self.assertAlmostEqual(extra[0, experiment.EXTRA.index('first20s_mean')], 9.5)
        self.assertAlmostEqual(extra[0, experiment.EXTRA.index('middle20s_mean')], 29.5)
        self.assertAlmostEqual(extra[0, experiment.EXTRA.index('last20s_mean')], 49.5)
        self.assertEqual(extra[1, experiment.EXTRA.index('excess_kurtosis')], 0)
        self.assertEqual(extra[1, experiment.EXTRA.index('lag1_correlation')], 0)
        self.assertTrue(np.isfinite(extra).all())

    def test_features_do_not_depend_on_other_cycles(self):
        values = np.random.default_rng(1).normal(size=(4, 120))
        base, extra, _ = experiment.sensor_features(values, np)
        changed = values.copy()
        changed[3] += 1000
        next_base, next_extra, _ = experiment.sensor_features(changed, np)
        np.testing.assert_array_equal(base[:3], next_base[:3])
        np.testing.assert_array_equal(extra[:3], next_extra[:3])

    def archive(self, path, profile_offset):
        with ZipFile(path, 'w') as archive:
            profile = np.full((6, 5), profile_offset, dtype=int)
            profile[:, 2] = [0, 1, 2, 0, 1, 2]
            data = io.StringIO()
            np.savetxt(data, profile, fmt='%d')
            archive.writestr('profile.txt', data.getvalue())
            for index, sensor in enumerate(benchmark_uci.SENSORS):
                values = 2 + index + np.arange(6)[:, None] + np.arange(60)[None, :] / 60
                data = io.StringIO()
                np.savetxt(data, values)
                archive.writestr(sensor + '.txt', data.getvalue())

    def test_baseline_is_unchanged_and_other_profile_labels_are_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = Path(directory) / 'one.zip', Path(directory) / 'two.zip'
            self.archive(first, 0)
            self.archive(second, 999)
            sensors = {sensor: 60 for sensor in benchmark_uci.SENSORS}
            with patch.object(experiment, 'CYCLE_COUNT', 6), patch.object(experiment, 'SENSORS', sensors), \
                    patch.object(benchmark_uci, 'CYCLE_COUNT', 6), patch.object(benchmark_uci, 'SENSORS', sensors):
                enhanced, labels, names, dependencies = experiment.load_features(first, np)
                replaced, next_labels, next_names, _ = experiment.load_features(second, np)
                historical, historical_labels, historical_names = benchmark_uci.load_cycles(first, np)
        self.assertEqual(enhanced.shape, (6, 340))
        np.testing.assert_array_equal(enhanced[:, :112], historical)
        np.testing.assert_array_equal(labels, historical_labels)
        self.assertEqual(names[:112], historical_names)
        np.testing.assert_array_equal(enhanced, replaced)
        np.testing.assert_array_equal(labels, next_labels)
        self.assertEqual(names, next_names)
        self.assertEqual(dependencies['PS1_PS2_mean_difference'], ['PS1', 'PS2'])
        self.assertEqual(len(names), len(set(names)))

    def test_stress_preserves_rows_and_uses_training_scales_only(self):
        training = np.array([[1, 2, 3, 4], [2, 4, 6, 4], [3, 6, 9, 4]], dtype=float)
        test = np.array([[10, 20, 30, 4], [20, 40, 60, 4]], dtype=float)
        original = test.copy()
        names = ['PS1_mean', 'PS2_mean', 'PS1_PS2_mean_ratio', 'TS1_std']
        dependencies = {'PS1_mean': ['PS1'], 'PS2_mean': ['PS2'],
                        'PS1_PS2_mean_ratio': ['PS1', 'PS2'], 'TS1_std': ['TS1']}
        stress, scale = experiment.stress_features(training, test, names, dependencies, np)
        shifted, next_scale = experiment.stress_features(training, test + 1000, names, dependencies, np)
        np.testing.assert_array_equal(test, original)
        np.testing.assert_array_equal(scale, next_scale)
        np.testing.assert_allclose(stress['feature_noise'] - test, shifted['feature_noise'] - test - 1000,
                                   atol=1e-12)
        self.assertTrue(np.isnan(stress['PS1_missing'][:, [0, 2]]).all())
        np.testing.assert_array_equal(stress['PS1_missing'][:, [1, 3]], test[:, [1, 3]])
        np.testing.assert_allclose(stress['PS1_feature_drift'][:, 0], test[:, 0] + scale[0] * 0.5)
        np.testing.assert_array_equal(stress['feature_noise'][:, 3], test[:, 3])

    def test_metrics_report_severe_leak_misses_and_validate_probability_contract(self):
        labels = np.array([0, 1, 2])
        perfect = experiment.metrics(labels, np.eye(3), np)
        self.assertEqual(perfect['balanced_accuracy'], 1)
        self.assertEqual(perfect['log_loss'], 0)
        probabilities = np.array([[0.9, 0.05, 0.05], [0.1, 0.8, 0.1], [0.1, 0.8, 0.1]])
        missed = experiment.metrics(labels, probabilities, np)
        self.assertEqual(missed['recall_by_class']['2'], 0)
        self.assertEqual(missed['confusion_matrix'][2], [0, 1, 0])
        with self.assertRaisesRegex(ValueError, 'normalized probabilities'):
            experiment.metrics(labels, probabilities * 2, np)

    def test_fixed_split_and_four_arms_do_not_enable_random_bagging(self):
        self.assertEqual(experiment.SPLITS, {'train': (0, 1323), 'validation': (1323, 1764), 'test': (1764, 2205)})
        self.assertEqual(len(experiment.ARMS), 4)
        options = experiment.fit_options('autogluon', 4, 300, 4)
        self.assertEqual(set(options['hyperparameters']), {'CAT', 'RF'})
        for key in ('num_bag_folds', 'num_stack_levels', 'auto_stack', 'refit_full', 'dynamic_stacking', 'num_gpus'):
            self.assertFalse(options[key])
        self.assertEqual(options['memory_limit'], 4)

    def test_export_keeps_synthetic_rows_test_only_with_original_labels(self):
        features = np.arange(2205 * 2, dtype=float).reshape(2205, 2)
        labels = np.arange(2205) % 3
        names = ['PS1_mean', 'PS2_mean']
        stressed = features[1764:].copy()
        stressed[:, 0] = np.nan
        report = {'datasetDOI': '10.24432/C5CW21', 'archiveSha256': 'source', 'scriptSha256': 'code',
                  'features': {'dependencies': {name: [name[:3]] for name in names}},
                  'stressProtocol': experiment.STRESS, 'testReuse': 'historical test reused'}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = experiment.export_datasets(root, features, labels, names,
                                                {'PS1_missing': stressed}, report, np)
            with ZipFile(root / result['path']) as archive:
                provenance = json.loads(archive.read('provenance.json'))
                rows = list(csv.DictReader(io.StringIO(archive.read('PS1_missing.csv').decode())))
                self.assertIn('README.txt', archive.namelist())
        self.assertEqual(len(rows), 441)
        self.assertEqual(rows[0]['cycle_number'], '1765')
        self.assertEqual(rows[0]['split'], 'test_only')
        self.assertEqual(rows[0]['origin'], 'synthetic_feature_perturbation_of_real_test')
        self.assertEqual(rows[0]['PS1_mean'], '')
        self.assertEqual([int(row['internal_pump_leakage']) for row in rows], labels[1764:].tolist())
        self.assertEqual(provenance['featureNames'], names)
        self.assertIn('cycle_number', provenance['metadataNotFeatures'])


if __name__ == '__main__':
    unittest.main()
