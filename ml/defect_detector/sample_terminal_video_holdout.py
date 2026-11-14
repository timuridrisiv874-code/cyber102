"""Recreate the measured frames of the supplied 20-second terminal walkthrough."""
import argparse
from hashlib import file_digest
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory


SOURCE_SHA256 = '5654cf77feb83cfbc6785e8eb65010fcb8297553effb15f08d3829fc8fc29cf8'
CLIP = 'walkthrough-173157.mp4'


def sha256(path):
    with path.open('rb') as stream:
        return file_digest(stream, 'sha256').hexdigest()


def prepare(video, annotations, output):
    if sha256(video) != SOURCE_SHA256:
        raise ValueError('Source video differs from the reviewed walkthrough')
    rows = json.loads(annotations.read_bytes())
    if len(rows) != 8:
        raise ValueError('Expected the eight reviewed timestamps')
    metadata = subprocess.check_output([
        'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
        '-of', 'default=nokey=1:noprint_wrappers=1', str(video),
    ])
    duration = float(metadata)
    if not 20 <= duration < 21:
        raise ValueError('Unexpected clip duration')
    if output.exists():
        raise FileExistsError(output)
    with TemporaryDirectory(dir=output.parent) as temporary:
        root = Path(temporary)
        frames = []
        for index, row in enumerate(rows):
            name = f'walkthrough-173157-{index:02}.jpg'
            timestamp = 1.25 + 2.5 * index
            if (row['clip'], row['frameIndex'], row['file'], row['timestampSeconds']) != (CLIP, index, name, timestamp):
                raise ValueError(f'Manifest differs from the measured sampling: {name}')
            photo = root / name
            subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-ss', str(timestamp),
                            '-i', str(video), '-frames:v', '1', '-q:v', '3', str(photo)], check=True)
            if row['sha256'] != sha256(photo):
                raise ValueError(f'Decoded frame differs from the recorded JPEG: {name}')
            frames.append({key: row[key] for key in ('file', 'timestampSeconds', 'sha256')})
        (root / 'clips.json').write_text(json.dumps({CLIP: {
            'durationSeconds': duration, 'sourceSha256': SOURCE_SHA256, 'frames': frames,
        }}, indent=2) + '\n', encoding='utf-8')
        root.replace(output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('video', type=Path)
    parser.add_argument('annotations', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    prepare(args.video, args.annotations, args.output)
