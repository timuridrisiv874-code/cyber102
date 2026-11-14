import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from server import vision, video_review
from server.vision_models import MODEL_ENV, configure_from_file, model_spec


class SelectedModelTests(unittest.TestCase):
    def test_shared_file_handles_bom_and_rejects_unknown_without_changing_environment(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=False):
            root = Path(directory)
            self.assertEqual(configure_from_file(root), 'internvl3_5')
            (root / 'var').mkdir()
            path = root / 'var/vlm-model.txt'
            path.write_text('qwen3_vl_4b\n', encoding='utf-8-sig')
            self.assertEqual(configure_from_file(root), 'qwen3_vl_4b')
            self.assertEqual(model_spec().model_id, 'Qwen/Qwen3-VL-4B-Instruct')
            path.write_text('unknown-model')
            with self.assertRaises(ValueError):
                configure_from_file(root)
            self.assertEqual(os.environ[MODEL_ENV], 'qwen3_vl_4b')

    def test_photo_accepts_selected_model_and_rejects_other_model_or_revision(self):
        observation = dict(component='pipe', condition='corrosion', box=[100, 200, 800, 900],
                           observation='На трубе виден бурый налёт.')
        for key in ('internvl3_5', 'qwen3_vl_4b', 'qwen3_8_27b'):
            expected = model_spec(key)
            envelope = dict(model=expected.model_id, revision=expected.revision, observation=observation)
            with self.subTest(key=key), patch.dict(os.environ, {MODEL_ENV: key}):
                with patch.object(vision, 'request_json', return_value=envelope):
                    self.assertEqual(vision.analyze(b'jpeg', backend='internvl3')['model'], expected.model_id)
                for changed in ({'model': 'unselected-model'}, {'revision': 'wrong-revision'}):
                    with patch.object(vision, 'request_json', return_value=envelope | changed):
                        with self.assertRaises(vision.VisionUnavailable):
                            vision.analyze(b'jpeg', backend='internvl3')

    def test_video_checks_selected_identity_without_rewriting_model_output(self):
        expected = model_spec('qwen3_vl_4b')
        payload = {'frames': [{'timestampSeconds': 0.5}], 'durationSeconds': 1.0}
        envelope = dict(model=expected.model_id, revision=expected.revision, durationSeconds=2,
                        summary='Виден участок трубы.', observations=[dict(frameIndex=0, timestampSeconds=0.5,
                        component='pipe', condition='uncertain', box=[0, 0, 0, 0], observation='')])
        with patch.dict(os.environ, {MODEL_ENV: 'qwen3_vl_4b'}):
            result = video_review.result_from_envelope(envelope, payload)
            self.assertEqual((result['model'], result['status']), (expected.model_id, 'unusable'))
        with patch.dict(os.environ, {MODEL_ENV: 'internvl3_5'}), self.assertRaises(ValueError):
            video_review.result_from_envelope(envelope, payload)

    def test_27b_photo_profiles_and_video_identity(self):
        expected = model_spec('qwen3_8_27b')
        payload = {'frames': [{'timestampSeconds': 0.5}], 'durationSeconds': 1.0}
        envelope = dict(model=expected.model_id, revision=expected.revision, durationSeconds=2,
                        summary='Виден участок трубы.', observations=[dict(frameIndex=0, timestampSeconds=0.5,
                        component='pipe', condition='uncertain', box=[0, 0, 0, 0], observation='')])
        with patch.dict(os.environ, {MODEL_ENV: 'qwen3_8_27b'}):
            self.assertEqual(vision.supported_profiles('internvl3'), ('standard', 'fast', 'liquid'))
            self.assertEqual(video_review.result_from_envelope(envelope, payload)['model'], expected.model_id)
        with patch.dict(os.environ, {MODEL_ENV: 'qwen3_vl_4b'}), self.assertRaises(ValueError):
            video_review.result_from_envelope(envelope, payload)
