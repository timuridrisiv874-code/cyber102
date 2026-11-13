"""Download and verify the pinned official Qwen3-VL-4B candidate as data only."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha1, sha256
import json
import os
from pathlib import Path
import time
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

REPOSITORY = 'Qwen/Qwen3-VL-4B-Instruct'
REVISION = 'ebb281ec70b05090aa6165b016eac8ec08e71b17'
ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {'.json', '.jinja', '.safetensors', '.txt', '.md'}


class HttpsOnlyRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        if not new_url.startswith('https://'):
            raise ValueError('Refusing a non-HTTPS model download redirect')
        return super().redirect_request(request, fp, code, message, headers, new_url)


def checksum(path):
    digest = sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def allowed_file(name):
    return (isinstance(name, str) and name and '/' not in name and '\\' not in name
            and not name.startswith('.') and Path(name).suffix in ALLOWED)


def verify_file(path, entry):
    if path.stat().st_size != entry['size']:
        raise ValueError(f'File size mismatch: {path.name}')
    actual = checksum(path)
    expected = entry.get('lfs', {}).get('sha256')
    if expected:
        if actual != expected:
            raise ValueError(f'SHA-256 mismatch: {path.name}')
    elif entry.get('blobId'):
        body = path.read_bytes()
        if sha1(b'blob ' + str(len(body)).encode() + b'\0' + body).hexdigest() != entry['blobId']:
            raise ValueError(f'Git blob mismatch: {path.name}')
    else:
        raise ValueError(f'Missing official checksum: {path.name}')
    return {'path': path.name, 'bytes': path.stat().st_size, 'sha256': actual}


def download_file(entry, destination):
    name = entry['rfilename']
    if not allowed_file(name):
        raise ValueError('Unsupported model file')
    path = destination / name
    if path.exists():
        try:
            return verify_file(path, entry)
        except ValueError:
            pass
    temporary = path.with_name(path.name + '.part')
    url = f'https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/{quote(name)}'
    opener = build_opener(ProxyHandler({}), HttpsOnlyRedirect())
    print(f'Downloading {name}', flush=True)
    with opener.open(Request(url, headers={'User-Agent': 'Cyber102-candidate-setup/1.0'}), timeout=60) as response, temporary.open('wb') as output:
        total, checkpoint = 0, time.monotonic()
        for block in iter(lambda: response.read(1024 * 1024), b''):
            output.write(block)
            total += len(block)
            if time.monotonic() - checkpoint >= 20:
                print(f'{name}: {total // (1024 * 1024)} MiB', flush=True)
                checkpoint = time.monotonic()
    record = verify_file(temporary, entry)
    os.replace(temporary, path)
    record['path'] = name
    print(f'Verified {name}', flush=True)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=ROOT / 'models/qwen3-vl-4b-instruct')
    parser.add_argument('--workers', type=int, choices=(1, 2, 3, 4), default=2)
    args = parser.parse_args()
    destination = args.directory.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    opener = build_opener(ProxyHandler({}), HttpsOnlyRedirect())
    with opener.open(f'https://huggingface.co/api/models/{REPOSITORY}/revision/{REVISION}?blobs=true', timeout=30) as response:
        metadata = json.load(response)
    if metadata.get('sha') != REVISION:
        raise ValueError('Repository revision mismatch')
    entries = [entry for entry in metadata['siblings'] if allowed_file(entry.get('rfilename'))]
    # Small native configuration files become available before the two weight shards.
    entries.sort(key=lambda entry: (entry['rfilename'].endswith('.safetensors'), entry['rfilename']))
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        records = list(pool.map(lambda entry: download_file(entry, destination), entries))
    manifest = {'repository': REPOSITORY, 'revision': REVISION, 'files': records,
                'source': f'https://huggingface.co/{REPOSITORY}/tree/{REVISION}',
                'license_metadata': metadata.get('cardData', {}).get('license')}
    temporary = destination / 'cyber102-manifest.json.part'
    temporary.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, destination / 'cyber102-manifest.json')
    print(json.dumps({'downloaded': len(records), 'bytes': sum(row['bytes'] for row in records),
                      'repository': REPOSITORY, 'revision': REVISION}), flush=True)


if __name__ == '__main__':
    main()
