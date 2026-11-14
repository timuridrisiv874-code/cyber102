from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from server import app, result_cache


class ResultCacheTests(unittest.TestCase):
    def test_default_cache_retains_successful_results_for_a_week(self):
        with TemporaryDirectory() as directory:
            cache = result_cache.ResultCache(directory)
            self.assertEqual(cache.ttl, 7 * 86400)

    def test_persistent_result_is_copied_and_expires(self):
        now = [100.0]
        with TemporaryDirectory() as directory:
            cache = result_cache.ResultCache(directory, ttl=10, clock=lambda: now[0])
            value = {'status': 'defect', 'observations': [{'condition': 'corrosion'}]}
            cache.put('a' * 64, value)
            value['observations'].clear()
            restored = result_cache.ResultCache(directory, ttl=10, clock=lambda: now[0])
            row = restored.get('a' * 64)
            self.assertEqual(row['observations'], [{'condition': 'corrosion'}])
            row['observations'].clear()
            self.assertTrue(restored.get('a' * 64)['observations'])
            now[0] += 10
            self.assertIsNone(restored.get('a' * 64))

    def test_errors_uncertainty_and_saved_evidence_are_never_cached(self):
        with TemporaryDirectory() as directory:
            cache = result_cache.ResultCache(directory)
            for row in [{'status': 'unusable'}, {'status': 'failed'},
                        {'status': 'defect', 'analysisId': 'old'},
                        {'status': 'clear', 'representativeFrame': {}},
                        {'status': 'clear', 'media': {'videoTimeSeconds': 2}}]:
                cache.put('b' * 64, row)
                self.assertIsNone(cache.get('b' * 64))

    def test_corrupt_result_is_a_miss_and_capacity_is_bounded(self):
        with TemporaryDirectory() as directory:
            cache = result_cache.ResultCache(directory, max_entries=2)
            for char in 'abcd':
                cache.put(char * 64, {'status': 'clear'})
            self.assertEqual(len(list(Path(directory).glob('*.json'))), 2)
            path = Path(directory) / ('d' * 64 + '.json')
            path.write_text('{"result": "broken"}')
            self.assertIsNone(cache.get('d' * 64))
            with self.assertRaises(ValueError):
                cache.get('../unrelated')

    def test_media_timestamps_do_not_change_inference_but_models_inputs_and_context_do(self):
        model = SimpleNamespace(model_id='vlm', revision='one')
        payload = {'image': b'original', 'backend': 'internvl3', 'profile': 'standard',
                   'media': {'captureSource': 'uploaded_photo'}, 'equipmentContext': None}
        key = result_cache.inference_key('photo', payload, model)
        self.assertEqual(key, result_cache.inference_key('photo', payload | {'media': {'videoTimeSeconds': 10}}, model))
        for changed in [payload | {'image': b'other'}, payload | {'profile': 'fast'},
                        payload | {'equipmentContext': {'text': 'another unit'}}]:
            self.assertNotEqual(key, result_cache.inference_key('photo', changed, model))
        self.assertNotEqual(key, result_cache.inference_key('photo', payload, SimpleNamespace(model_id='vlm', revision='two')))
        video = {'frames': [{'image': 'abc', 'timestampSeconds': 1}], 'durationSeconds': 3}
        moved = deepcopy(video)
        moved['frames'][0]['timestampSeconds'] = 2
        self.assertNotEqual(result_cache.inference_key('video', video, model), result_cache.inference_key('video', moved, model))

    def test_photo_hit_uses_current_media_and_new_analysis_identity(self):
        with TemporaryDirectory() as directory:
            cache = result_cache.ResultCache(Path(directory) / 'cache')
            payload = {'image': b'original', 'backend': 'internvl3', 'profile': 'standard',
                       'media': {'captureSource': 'uploaded_video', 'videoTimeSeconds': 1},
                       'database': str(Path(directory) / 'test.sqlite3'), 'equipmentContext': None}
            with patch.object(app.vision, 'analyze', return_value={'status': 'clear'}) as infer, \
                 patch.object(app.inspections, 'store_analysis', side_effect=[{'analysisId': 'first'}, {'analysisId': 'second'}]):
                first = app.save_photo_job(payload, app.analyze_photo_job(payload, cache=cache))
                moved = payload | {'media': {'captureSource': 'uploaded_video', 'videoTimeSeconds': 5}}
                second = app.save_photo_job(moved, app.analyze_photo_job(moved, cache=cache))
            self.assertEqual(infer.call_count, 1)
            self.assertNotEqual(first['analysisId'], second['analysisId'])
            self.assertEqual(second['media']['videoTimeSeconds'], 5)


if __name__ == '__main__':
    unittest.main()
