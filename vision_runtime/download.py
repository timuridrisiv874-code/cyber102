"""Download a pinned official InternVL snapshot. Inference itself stays offline."""
import argparse
from hashlib import sha256, sha1
import json
import os
from pathlib import Path
import time
from urllib.parse import quote
from urllib.request import ProxyHandler, Request, build_opener

REPOSITORY = 'OpenGVLab/InternVL3-2B-hf'
REVISION = 'cb57a075cb75a2e6d1b668b128d48bb00ae321d2'
ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {'.json', '.jinja', '.safetensors', '.txt', '.md'}


def digest_file(path):
    digest = sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=ROOT / 'models/internvl3-2b-hf')
    args = parser.parse_args()
    destination = args.directory.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    opener = build_opener(ProxyHandler({}))
    url = f'https://huggingface.co/api/models/{REPOSITORY}/revision/{REVISION}?blobs=true'
    with opener.open(url, timeout=30) as response:
        metadata = json.load(response)
    if metadata.get('sha') != REVISION:
        raise ValueError('Repository revision mismatch')
    records = []
    for entry in metadata['siblings']:
        name = entry['rfilename']
        if '/' in name or '\\' in name or Path(name).suffix not in ALLOWED or name.startswith('.'):
            continue
        path = destination / name
        expected = entry.get('lfs', {}).get('sha256')
        if not (path.exists() and expected and digest_file(path) == expected):
            temporary = path.with_name(path.name + '.part')
            file_url = f'https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/{quote(name)}'
            print(f'Downloading {name}', flush=True)
            with opener.open(Request(file_url, headers={'User-Agent': 'Cyber102-model-setup/1.0'}), timeout=60) as response, temporary.open('wb') as output:
                total = 0
                checkpoint = time.monotonic()
                for block in iter(lambda: response.read(1024 * 1024), b''):
                    output.write(block)
                    total += len(block)
                    if time.monotonic() - checkpoint >= 20:
                        print(f'{name}: {total // (1024 * 1024)} MiB', flush=True)
                        checkpoint = time.monotonic()
            actual = digest_file(temporary)
            if expected and actual != expected:
                raise ValueError(f'Checksum mismatch: {name}')
            if not expected and entry.get('blobId'):
                blob = temporary.read_bytes()
                git_hash = sha1(b'blob ' + str(len(blob)).encode() + b'\0' + blob).hexdigest()
                if git_hash != entry['blobId']:
                    raise ValueError(f'Git checksum mismatch: {name}')
            os.replace(temporary, path)
        records.append({'path': name, 'bytes': path.stat().st_size, 'sha256': digest_file(path)})
    manifest = {'repository': REPOSITORY, 'revision': REVISION, 'files': records,
                'source': f'https://huggingface.co/{REPOSITORY}/tree/{REVISION}',
                'license_metadata': metadata.get('cardData', {}).get('license')}
    (destination / 'cyber102-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'downloaded': len(records), 'repository': REPOSITORY, 'revision': REVISION}), flush=True)


if __name__ == '__main__':
    main()
