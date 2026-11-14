"""Add industrial smoke to a smoke/flame/steam rehearsal dataset."""
import argparse
from collections import Counter, defaultdict
from datetime import date
import hashlib
import json
from pathlib import Path
import shutil
from tarfile import open as open_tar

from prepare_ijmond_holdout import ARCHIVE_ROOT, SOURCE, frame_objects, load_annotations, source_capture, verify_archive
from prepare_plume_training import CLASSES, IMAGE_EXTENSIONS, SPLITS


CAMERA = 'kooks_2'


def group_examples(images, annotations, days, per_day):
    captures = defaultdict(lambda: defaultdict(list))
    for image in images:
        camera, day, clip = source_capture(image)
        if camera == CAMERA and day in days:
            captures[day][clip].append(image)
    for clips in captures.values():
        # Keep one positive per original clip and an author-labelled negative when available.
        # Order captures by a stable hash so nearby frames cannot dominate the sample.
        ranked = sorted(clips, key=lambda clip: hashlib.sha256(clip.encode()).digest())
        positives = []
        for clip in ranked:
            frames = sorted(clips[clip], key=lambda row: hashlib.sha256(row['file_name'].encode()).digest())
            positive = next((frame for frame in frames if annotations[frame['id']]), None)
            negative = next((frame for frame in frames if not annotations[frame['id']]), None)
            if positive is not None:
                positives.append(positive)
            if negative is not None:
                yield negative
        yield from positives[:per_day]


def build(archive_path, base, holdout, output, per_day):
    verify_archive(archive_path)
    base_coverage = json.loads((base / 'coverage.json').read_text(encoding='utf-8'))
    if base_coverage['classes'] != list(CLASSES):
        raise ValueError('Source class IDs do not match the smoke/flame/steam checkpoint')
    controls = json.loads(holdout.read_text(encoding='utf-8'))
    reserved_dates = {source_capture({'file_name': row['file']})[1] for row in controls}
    reserved_names = {row['file'] for row in controls}
    seen = {row['sha256'] for row in controls}
    counts = {split: Counter() for split in SPLITS}
    with open_tar(archive_path) as archive:
        images, annotations, categories = load_annotations(archive)
        dates = sorted({day for camera, day, _ in map(source_capture, images)
                        if camera == CAMERA and day not in reserved_dates})
        if len(dates) < 10:
            raise ValueError('Not enough smoke observation dates')
        # Leave a full day between development splits to reduce adjacent-day similarity.
        boundary = len(dates) * 4 // 5
        day_limit = date.fromisoformat(dates[boundary])
        split_days = {'train': [day for day in dates[:boundary] if (day_limit - date.fromisoformat(day)).days > 1],
                      'val': dates[boundary:]}
        output.mkdir(parents=True, exist_ok=False)
        for split in SPLITS:
            for kind in ('images', 'labels'):
                (output / kind / split).mkdir(parents=True)
            for image in (base / 'images' / split).iterdir():
                if image.suffix.lower() not in IMAGE_EXTENSIONS:
                    continue
                label = image.with_suffix('.txt').name
                digest = hashlib.sha256(image.read_bytes()).hexdigest()
                if digest in seen:
                    raise ValueError(f'Duplicate rehearsal image: {image.name}')
                seen.add(digest)
                shutil.copyfile(image, output / 'images' / split / image.name)
                shutil.copyfile(base / 'labels' / split / label, output / 'labels' / split / label)
                counts[split]['rehearsal_images'] += 1

        selected = {}
        for split, days in split_days.items():
            for image in group_examples(images, annotations, set(days), per_day):
                filename = image['file_name']
                member_name = ARCHIVE_ROOT + 'images/' + filename
                if filename in reserved_names or member_name in selected:
                    raise ValueError(f'Duplicate industrial smoke frame: {filename}')
                selected[member_name] = split, image

    source_frames = {split: [] for split in split_days}
    with open_tar(archive_path, mode='r|gz') as archive:
        for member in archive:
            entry = selected.pop(member.name, None)
            if entry is None:
                continue
            split, image = entry
            filename = image['file_name']
            _, day, clip = source_capture(image)
            raw = archive.extractfile(member).read()
            digest = hashlib.sha256(raw).hexdigest()
            if digest in seen:
                raise ValueError(f'Duplicate industrial smoke frame: {filename}')
            seen.add(digest)
            target = 'ijmond-' + filename
            objects = frame_objects(image, annotations, categories)
            (output / 'images' / split / target).write_bytes(raw)
            label = (output / 'labels' / split / target).with_suffix('.txt')
            with label.open('x', encoding='utf-8') as stream:
                for obj in objects:
                    left, top, right, bottom = obj['xyxy']
                    values = ((left + right) / 2, (top + bottom) / 2, right - left, bottom - top)
                    stream.write('0 ' + ' '.join(f'{value:.8f}' for value in values) + '\n')
            counts[split]['industrial_smoke_images' if objects else 'industrial_clear_images'] += 1
            counts[split].update(obj['sourceClass'] for obj in objects)
            source_frames[split].append({'file': target, 'date': day, 'clip': clip, 'sha256': digest})
            if not selected:
                break
    if selected:
        raise ValueError(f'Missing IJmond files in archive: {len(selected)}')

    for split in split_days:
        if not counts[split]['industrial_smoke_images']:
            raise ValueError(f'No smoke annotations in IJmond {split}')
        source_frames[split].sort(key=lambda row: row['file'])
    report = {**base_coverage, 'domainSource': SOURCE, 'domainCamera': CAMERA, 'domainDates': split_days,
              'domainFrames': source_frames, 'coveredByRehearsal': base_coverage['imageCountsByStratum'],
              'imageCountsByStratum': counts,
              'testScope': 'Original D-Fire test. Check unseen IJmond cameras, external smoke and steam separately.'}
    (output / 'coverage.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return {'source': SOURCE, 'dates': {split: len(days) for split, days in split_days.items()}, 'counts': counts}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('base', type=Path)
    parser.add_argument('holdout', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--per-day', type=int, default=3)
    args = parser.parse_args()
    if args.per_day < 1:
        parser.error('--per-day must be positive')
    print(json.dumps(build(args.archive, args.base, args.holdout, args.output, args.per_day)))
