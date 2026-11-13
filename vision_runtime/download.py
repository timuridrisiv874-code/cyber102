"""Download a pinned official InternVL snapshot. Inference itself stays offline."""
import argparse
from hashlib import sha256, sha1
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from urllib.parse import quote
from urllib.request import ProxyHandler, Request, build_opener

REPOSITORY = 'OpenGVLab/InternVL3_5-2B-HF'
REVISION = '3f301ffcf3dcbb47893afae6650ea3e78d96fb6d'
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
    parser.add_argument('--directory', type=Path, default=ROOT / 'models/internvl3_5-2b-hf')
    parser.add_argument('--curl', action='store_true', help='Use the system curl downloader; do not change inference network restrictions')
    args = parser.parse_args()
    destination = args.directory.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    opener = build_opener(ProxyHandler({}))
    curl = None
    if args.curl:
        curl = str(Path(os.environ['SystemRoot']) / 'System32/curl.exe') if os.name == 'nt' else shutil.which('curl')
        if not curl or not Path(curl).is_file():
            raise RuntimeError('System curl is unavailable')
    def fetch_with_curl(url, output=None):
        command = [curl, '--fail', '--location', '--silent', '--show-error', '--retry', '2',
                   '--connect-timeout', '30', '--max-time', '3600', '--noproxy', '*',
                   '--proto', '=https', '--proto-redir', '=https', url]
        if output is not None:
            command.extend(['--output', str(output)])
        return subprocess.run(command, check=True, stdout=subprocess.PIPE).stdout
    url = f'https://huggingface.co/api/models/{REPOSITORY}/revision/{REVISION}?blobs=true'
    if curl:
        metadata = json.loads(fetch_with_curl(url))
    else:
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
            if curl:
                fetch_with_curl(file_url, temporary)
            else:
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
