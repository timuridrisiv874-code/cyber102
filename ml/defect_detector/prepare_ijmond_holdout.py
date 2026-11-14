"""Export source-camera/date groups from the published IJmond smoke benchmark."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path, PurePosixPath
from tarfile import open as open_tar


ARCHIVE_MD5 = '20eb0e868e1fb8575612b9a4b77367e1'
SOURCE = 'https://doi.org/10.21942/uva.31847188.v3'
ARCHIVE_ROOT = 'ijmond_seg/test/'


def verify_archive(archive_path):
    with archive_path.open('rb') as stream:
        if hashlib.file_digest(stream, 'md5').hexdigest() != ARCHIVE_MD5:
            raise ValueError('Archive checksum differs from the reviewed IJmond release')


def load_annotations(archive):
    data = json.load(archive.extractfile(ARCHIVE_ROOT + '_annotations.coco.json'))
    categories = {row['id']: row['name'] for row in data['categories']}
    by_id = defaultdict(list)
    for annotation in data['annotations']:
        if annotation['iscrowd'] or categories[annotation['category_id']] not in (
                'high-opacity-smoke', 'low-opacity-smoke'):
            raise ValueError(f'Unexpected smoke label {annotation["id"]}')
        by_id[annotation['image_id']].append(annotation)
    return data['images'], by_id, categories


def source_capture(image):
    filename = image['file_name']
    if PurePosixPath(filename).name != filename:
        raise ValueError(f'Unexpected source filename {filename}')
    camera, details = filename.split('__', 1)
    timestamp, _ = details.split('_frame_', 1)
    return camera, timestamp.split('T', 1)[0], timestamp


def frame_objects(image, by_id, categories):
    w, h = image['width'], image['height']
    objects = []
    for a in by_id[image['id']]:
        x, y, width, height = a['bbox']
        box = [max(0, x) / w, max(0, y) / h, min(w, x + width) / w, min(h, y + height) / h]
        if not 0 <= box[0] < box[2] <= 1 or not 0 <= box[1] < box[3] <= 1:
            raise ValueError(f'Invalid box {a["id"]}')
        objects.append({'class': 'smoke', 'xyxy': box, 'sourceClass': categories[a['category_id']]})
    return objects


def export(archive_path, output, per_camera):
    verify_archive(archive_path)
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    with open_tar(archive_path) as archive:
        images, by_id, categories = load_annotations(archive)
        candidates = defaultdict(lambda: defaultdict(list))
        for image in images:
            camera, date, _ = source_capture(image)
            candidates[camera][date].append(image)
        seen = set()
        for camera, dates in sorted(candidates.items()):
            order = sorted(dates, key=lambda date: hashlib.sha256(f'{camera}/{date}'.encode()).digest())
            for date in order[:per_camera]:
                scene = f'{camera}-{date}'
                image = min(dates[date], key=lambda item: hashlib.sha256(item['file_name'].encode()).digest())
                raw = archive.extractfile(ARCHIVE_ROOT + 'images/' + image['file_name']).read()
                digest = hashlib.sha256(raw).hexdigest()
                if digest in seen:
                    raise ValueError(f'Duplicate source image {image["file_name"]}')
                seen.add(digest)
                objects = frame_objects(image, by_id, categories)
                (output / image['file_name']).write_bytes(raw)
                rows.append({'file': image['file_name'], 'scene': scene, 'camera': camera, 'source': SOURCE,
                             'sha256': digest, 'objects': objects})
    (output / 'frames.json').write_text(json.dumps(rows, indent=2) + '\n', encoding='utf-8')
    counts = Counter(row['camera'] for row in rows)
    print(json.dumps({'imagesByCamera': counts, 'boxes': sum(len(row['objects']) for row in rows)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--per-camera', type=int, default=12)
    args = parser.parse_args()
    if args.per_camera < 1:
        parser.error('--per-camera must be positive')
    export(args.archive, args.output, args.per_camera)
