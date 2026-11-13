"""Extract the reviewed connector scenes from the Power Equipment Image Dataset."""
import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from PIL import Image


ARCHIVE_SHA256 = '4dfb88386d7f460347be6c86241cbd16af322dc47a3ad0099c3e90f2c41bb8a3'
SOURCE = 'https://huggingface.co/datasets/FacePeck2026/Power-equipment-image-dataset'
SCENES = {
    'voc5': ('power-switchbank', 'jj_jxps'),
    'voc2': ('power-inline-joint', 'gydxjd_ps'),
}


def extract(archive_path, output):
    with archive_path.open('rb') as file:
        if hashlib.file_digest(file, 'sha256').hexdigest() != ARCHIVE_SHA256:
            raise ValueError('Archive SHA-256 differs from the reviewed release')
    output.mkdir(parents=True, exist_ok=False)
    rows, seen = [], set()
    with ZipFile(archive_path) as archive:
        for name in sorted(archive.namelist()):
            member = PurePosixPath(name)
            group = member.parent.parent.name
            if member.parent.name != 'Annotations' or group not in SCENES or member.suffix != '.xml':
                continue
            scene, category = SCENES[group]
            root = ET.fromstring(archive.read(name))
            objects = root.findall('object')
            if not any(obj.findtext('name') == category for obj in objects):
                continue
            if any(obj.findtext('name') != category for obj in objects):
                raise ValueError(f'Mixed labels in reviewed connector image: {name}')
            width, height = (int(root.findtext(f'size/{axis}')) for axis in ('width', 'height'))
            filename = member.stem + '.jpg'
            source_image = member.parent.parent / 'JPEGImages' / filename
            photo = archive.read(str(source_image))
            digest = hashlib.sha256(photo).hexdigest()
            if digest in seen:
                raise ValueError(f'Duplicate connector photograph: {name}')
            seen.add(digest)
            with Image.open(BytesIO(photo)) as image:
                if image.getexif().get(274, 1) != 1 or image.width * height != image.height * width:
                    raise ValueError(f'Image layout does not match its annotations: {name}')
            boxes = []
            for obj in objects:
                box = obj.find('bndbox')
                left, top, right, bottom = [int(box.findtext(key)) / size for key, size in (
                    ('xmin', width), ('ymin', height), ('xmax', width), ('ymax', height))]
                if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
                    raise ValueError(f'Invalid connector box: {name}')
                boxes.append({'class': 'terminal_damage', 'xyxy': [left, top, right, bottom]})
            (output / filename).write_bytes(photo)
            rows.append({'file': filename, 'scene': scene, 'source': SOURCE, 'sha256': digest, 'objects': boxes})
    (output / 'frames.json').write_text(json.dumps(rows, indent=2) + '\n', encoding='utf-8')
    print(f'{len(rows)} photos, {sum(len(row["objects"]) for row in rows)} boxes, '
          f'{len({row["scene"] for row in rows})} scenes')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    extract(args.archive, args.output)
