import tempfile
import unittest
import unicodedata
from pathlib import Path
import zipfile

from PIL import Image
from data_tools.import_customer_materials import build, GROUPS, DOCUMENT, digest


class MaterialImportTest(unittest.TestCase):
    def test_portable_names_deduplication_and_source_integrity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / 'source', root / 'result'
            for name in GROUPS:
                (source / name).mkdir(parents=True)
            name = unicodedata.normalize('NFD', 'Безымянный.png')
            image = source / 'Насосные агрегаты' / name
            Image.new('RGB', (1800, 900), 'red').save(image)
            original_hash = digest(image)
            (source / 'Запорная арматура' / 'copy.png').write_bytes(image.read_bytes())
            with zipfile.ZipFile(source / DOCUMENT, 'w') as archive:
                archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>1. Проверка</w:t></w:r></w:p><w:p><w:r><w:t>Описание признака</w:t></w:r></w:p></w:body></w:document>')
            result = build(source, output)
            self.assertEqual(result['summary']['uniqueImages'], 1)
            self.assertEqual(result['summary']['duplicateImages'], 1)
            row = result['images'][0]
            self.assertTrue(unicodedata.is_normalized('NFC', row['sourcePath']))
            self.assertEqual(row['aliases'], ['Запорная арматура/copy.png'])
            self.assertEqual(digest(image), original_hash)
            self.assertEqual(digest(output / 'raw' / row['sourcePath']), original_hash)
            self.assertEqual((row['width'], row['height']), (1280, 640))
            self.assertIsNone(row['defectLabel'])
            self.assertEqual(result['documents'][0]['sections'][0]['paragraphs'], ['Описание признака'])
            before = (output / 'materials.json').read_bytes()
            build(source, output)
            self.assertEqual((output / 'materials.json').read_bytes(), before)
            build(output / 'raw', output)
            self.assertEqual((output / 'materials.json').read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
