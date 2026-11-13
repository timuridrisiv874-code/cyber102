from io import BytesIO
import unittest
from types import SimpleNamespace

from PIL import Image
from server.vision import extract_request
from vision_runtime import benchmark_crop as crop, benchmark_joint as joint


class JointBenchmarkTests(unittest.TestCase):
    def test_input_matches_real_api_normalization_and_never_upscales(self):
        with Image.new('RGB', (1600, 1200), '#a86135') as original:
            first, _ = crop.normalized_jpeg(original)
            body, headers = crop.multipart(first)
            expected, _ = extract_request(headers['Content-Type'], body)
            prepared, raw = joint.prepare_image(original, crop)
            with prepared:
                self.assertEqual(raw, expected)
                self.assertEqual(prepared.size, (1280, 960))
        with Image.new('RGB', (160, 90)) as small:
            prepared, raw = joint.prepare_image(small, crop)
            with prepared, Image.open(BytesIO(raw)) as encoded:
                self.assertEqual(prepared.size, (160, 90))
                self.assertEqual(encoded.size, prepared.size)

    def test_crop_location_is_overview_not_crop_coordinates(self):
        result = joint.normalized_crop_box({'pixels': [200, 160, 800, 640]}, (1000, 800))
        self.assertEqual(result, [200, 200, 800, 800])
        original = joint.normalized_crop_box({'pixels': [0, 100, 1600, 900]}, (1600, 1200))
        self.assertEqual(original[2:], [1000, 750])

    def test_invalid_response_retains_raw_output_without_repair_or_retry(self):
        engine = SimpleNamespace(model_id='test', last_output='{"box":[0,0,1200,1000]}',
                                 last_metrics=None, last_memory_metrics={'peakAllocatedMiB': 1},
                                 last_input_metadata={'imageGridTHW': [[1, 30, 40]]})
        calls = []
        def invalid():
            calls.append(True)
            raise ValueError('Model box outside image')
        row = joint.measure(engine, invalid)
        self.assertFalse(row['success'])
        self.assertEqual(row['rawOutput'], engine.last_output)
        self.assertEqual(row['memory']['peakAllocatedMiB'], 1)
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
