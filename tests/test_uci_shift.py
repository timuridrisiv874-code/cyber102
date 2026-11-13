import unittest

try:
    import numpy as np
except ImportError:
    np = None

from ml.benchmark_uci_shift import GROUPS, SEEDS, WEIGHTS, phase_features, shape_names, validation_selection


@unittest.skipIf(np is None, 'NumPy is needed for numerical feature checks')
class ShiftFeatureTests(unittest.TestCase):
    def test_shape_contract_excludes_undefined_ratio_and_absolute_levels(self):
        names = shape_names()
        self.assertEqual(len(names), 53)
        self.assertEqual(len(set(names)), 53)
        self.assertNotIn('PS3_PS4_mean_ratio', names)
        self.assertNotIn('FS1_mean', names)
        self.assertNotIn('cycle_number', names)

    def test_phase_zero_and_constant_channels(self):
        result = phase_features(np.array([[0.] * 60, [10.] * 60, [-5.] * 60]), np)
        np.testing.assert_array_equal(result[0], np.zeros(24))
        np.testing.assert_array_equal(result[1, ::2], np.ones(12))
        np.testing.assert_array_equal(result[2, ::2], -np.ones(12))
        np.testing.assert_array_equal(result[:, 1::2], np.zeros((3, 12)))

    def test_fixed_block_order_scale_and_row_independence(self):
        values = np.arange(60, dtype=float).reshape(1, 60)
        result = phase_features(values, np)
        rms = np.sqrt(np.mean(values ** 2))
        self.assertAlmostEqual(result[0, 0], 2 / rms)
        self.assertAlmostEqual(result[0, 1], np.std(np.arange(5)) / rms)
        self.assertAlmostEqual(result[0, 22], 57 / rms)
        np.testing.assert_allclose(phase_features(values * 7, np), result)
        np.testing.assert_array_equal(phase_features(np.vstack((values, values * 300)), np)[0], result[0])

    def test_invalid_cycles_rejected(self):
        for bad in (np.ones((2, 61)), np.ones((2, 0)), np.array([[float('nan')] * 60])):
            with self.assertRaises(ValueError):
                phase_features(bad, np)

    def test_selection_uses_validation_and_fixed_seed_set(self):
        counts = {'full340': 340, 'shape53': 53, 'phase389': 389}
        records = [{'featureGroup': group, 'weighting': weighting, 'seed': seed, 'features': counts[group],
                    'validation': {'balanced_accuracy': .8, 'log_loss': .4},
                    'test': {'balanced_accuracy': .99 if group == 'full340' else .01}}
                   for group in GROUPS for weighting in WEIGHTS for seed in SEEDS]
        selected = validation_selection(records, np)
        self.assertEqual((selected['winner']['featureGroup'], selected['winner']['weighting']), ('shape53', 'None'))
        self.assertFalse(selected['testPredictionsAvailableAtSelection'])
        with self.assertRaises(ValueError):
            validation_selection(records[:-1], np)

    def test_validation_score_takes_precedence_over_model_size(self):
        counts = {'full340': 340, 'shape53': 53, 'phase389': 389}
        records = [{'featureGroup': group, 'weighting': weighting, 'seed': seed, 'features': counts[group],
                    'validation': {'balanced_accuracy': .9 if group == 'phase389' else .8, 'log_loss': .5}}
                   for group in GROUPS for weighting in WEIGHTS for seed in SEEDS]
        self.assertEqual(validation_selection(records, np)['winner']['featureGroup'], 'phase389')


if __name__ == '__main__':
    unittest.main()
