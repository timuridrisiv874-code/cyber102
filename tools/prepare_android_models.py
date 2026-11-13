"""Verify or explicitly download the pinned on-device SmolVLM files for an APK."""
import argparse
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import quote
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'android/app/src/main/assets/models/model-manifest.json'


def verify(path, entry):
    if not path.is_file() or path.stat().st_size != entry['size']:
        return False
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest() == entry['sha256']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download', action='store_true', help='Download missing official weights; otherwise only verify local files')
    parser.add_argument('--directory', type=Path, default=ROOT / 'var/models/smolvlm')
    args = parser.parse_args()
    catalog = json.loads(MANIFEST.read_text(encoding='utf-8'))
    repository, revision = catalog['repository'], catalog['revision']
    if repository not in ('ggml-org/SmolVLM-256M-Instruct-GGUF', 'ggml-org/SmolVLM-500M-Instruct-GGUF', 'ggml-org/SmolVLM2-2.2B-Instruct-GGUF') or not re.fullmatch(r'[a-f0-9]{40}', revision):
        raise ValueError('Expected an approved official repository and pinned revision')
    if {entry['role'] for entry in catalog['files']} != {'model', 'projector'} or len(catalog['files']) != 2:
        raise ValueError('Expected one model and its matching projector')
    args.directory.mkdir(parents=True, exist_ok=True)
    for entry in catalog['files']:
        name = entry['fileName']
        if not re.fullmatch(r'[A-Za-z0-9_.-]+\.gguf', name) or not re.fullmatch(r'[a-f0-9]{64}', entry['sha256']) or not 0 < entry['size'] <= 2 * 1024 ** 3:
            raise ValueError('Invalid pinned model entry')
        target = args.directory / name
        if not verify(target, entry):
            if not args.download:
                raise FileNotFoundError(f'Verified weights missing: {target}. Use --download to prepare them.')
            partial = target.with_suffix('.partial')
            url = f'https://huggingface.co/{repository}/resolve/{revision}/{quote(name)}'
            try:
                with urlopen(url, timeout=90) as response, partial.open('wb') as output:
                    received = 0
                    while chunk := response.read(1024 * 1024):
                        received += len(chunk)
                        if received > entry['size']:
                            raise ValueError('Download exceeds the pinned size')
                        output.write(chunk)
                if not verify(partial, entry):
                    raise ValueError(f'Weight checksum mismatch: {name}')
                partial.replace(target)
            finally:
                partial.unlink(missing_ok=True)
        print(f'OK {name}: {entry["size"]} bytes, SHA-256 {entry["sha256"]}', flush=True)


if __name__ == '__main__':
    main()
