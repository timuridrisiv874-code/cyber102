import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from data_tools import collect_commons as collector


def page():
    return {
        'title': 'File:Test pump.jpg',
        'imageinfo': [{
            'url': 'https://upload.wikimedia.org/wikipedia/commons/0/00/Test_pump.jpg?utm_source=test',
            'descriptionurl': 'https://commons.wikimedia.org/wiki/File:Test_pump.jpg',
            'mime': 'image/jpeg', 'size': 2000, 'width': 200, 'height': 200,
            'extmetadata': {
                'LicenseShortName': {'value': 'CC BY-SA 4.0'},
                'LicenseUrl': {'value': 'https://creativecommons.org/licenses/by-sa/4.0/'},
                'Artist': {'value': '<a href="/wiki/User:Example">Example &amp; Author</a>'},
                'Restrictions': {'value': ''},
            },
        }],
    }


class CommonsCollectionTest(unittest.TestCase):
    def test_preserves_attribution_and_validates_download_domain(self):
        info = collector.candidate(page(), 'pump', 'reference_only')
        self.assertEqual(info['creator'], 'Example & Author')
        self.assertEqual(info['attribution'], info['creator'])
        self.assertEqual(info['license'], 'CC BY-SA 4.0')
        self.assertNotIn('utm_source', info['download_url'])

        unsafe = page()
        unsafe['imageinfo'][0]['url'] = 'https://upload.wikimedia.org@other.example/image.jpg'
        with self.assertRaises(ValueError):
            collector.candidate(unsafe, 'pump', 'reference_only')

    def test_missing_license_and_additional_restrictions_are_skipped(self):
        unknown = page()
        unknown['imageinfo'][0]['extmetadata'].pop('LicenseUrl')
        with self.assertRaises(ValueError):
            collector.candidate(unknown, 'pump', 'reference_only')

        restricted = page()
        restricted['imageinfo'][0]['extmetadata']['Restrictions']['value'] = 'personality rights'
        with self.assertRaises(ValueError):
            collector.candidate(restricted, 'pump', 'reference_only')

    def test_normalizes_legacy_creative_commons_license_url(self):
        old_link = page()
        old_link['imageinfo'][0]['extmetadata']['LicenseUrl']['value'] = 'http://creativecommons.org/licenses/by-sa/4.0/'
        candidate = collector.candidate(old_link, 'pump', 'reference_only')
        self.assertEqual(candidate['license_url'], 'https://creativecommons.org/licenses/by-sa/4.0/')

    def test_exact_model_query_is_prioritized_and_empty_imageinfo_is_safe(self):
        exact = page()
        exact['title'] = 'File:Насос Д 160-1126.jpg'
        catalog = {'target_queries': ['Д 160-1126'], 'references': [{'title': page()['title'], 'subject': 'pump'}]}
        with patch.object(collector, 'api_query', side_effect=[[{'imageinfo': []}, exact], [page()]]):
            result = list(collector.fetch_catalog(catalog, timeout=2, user_agent='test'))
        self.assertEqual([item[2] for item in result], ['model_number_mentioned', 'reference_only'])

    def test_sha256_duplicate_has_one_saved_file_and_manifest_entry(self):
        first = collector.candidate(page(), 'pump', 'reference_only')
        second = {**first, 'download_url': first['download_url'] + '?alternate=1'}
        with tempfile.TemporaryDirectory() as directory, patch.object(
            collector, 'read_remote', return_value=(b'same image contents', 'image/jpeg'),
        ), patch.object(collector, 'verify_image'), patch.object(collector.time, 'sleep'):
            output = Path(directory)
            (output / 'unlabelled').mkdir()
            records = []
            self.assertTrue(collector.save_candidate(first, output, records, timeout=2, user_agent='test'))
            self.assertFalse(collector.save_candidate(second, output, records, timeout=2, user_agent='test'))
            manifest = json.loads((output / 'manifest.json').read_text())
            self.assertEqual(manifest, records)
            self.assertEqual(len(manifest), 1)
            self.assertEqual(manifest[0]['review_status'], 'unlabelled')
            self.assertEqual((output / manifest[0]['local_file']).read_bytes(), b'same image contents')

    def test_rejects_nonimage_bytes_and_dimensions_mismatch(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest('Pillow используется только сборщиком фото')
        with io.BytesIO() as buffer:
            Image.new('RGB', (200, 200)).save(buffer, format='JPEG')
            content = buffer.getvalue()
        collector.verify_image(content, 'image/jpeg', 200, 200)
        with self.assertRaises(ValueError):
            collector.verify_image(content, 'image/jpeg', 201, 200)
        with self.assertRaises(OSError):
            collector.verify_image(b'<html>not an image</html>', 'image/jpeg', 200, 200)


if __name__ == '__main__':
    unittest.main()
