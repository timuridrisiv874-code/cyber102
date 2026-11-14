"""Fetch or verify the two pinned Qwen3.8-27B GGUF data files."""
import argparse
import json
import os
from pathlib import Path
from urllib.parse import quote
from urllib.request import ProxyHandler, Request, build_opener

from server.vision_models import model_spec
from .qwen_download import HttpsOnlyRedirect, verify_file

SPEC = model_spec('qwen3_8_27b')
REPOSITORY = 'unsloth/Qwen3.8-27B-GGUF'
REVISION = SPEC.revision
MODEL_FILE = 'Qwen3.8-27B-UD-Q4_K_M.gguf'
PROJECTOR_FILE = 'mmproj-F16.gguf'
FILES = {
    MODEL_FILE: {'size': 16564259744, 'lfs': {
        'sha256': '1bce504dfecd84153666928a41d8a66706f0158d3a317bc5196b72819d7dc529'}},
    PROJECTOR_FILE: {'size': 927607488, 'lfs': {
        'sha256': 'cbb841a9ee0636b2ec172f5bb8df2ea8dfeb01e90fe7c6126581d662a0b4e43e'}},
}


def verify_directory(directory, *, require_manifest=True):
    directory = Path(directory)
    if require_manifest:
        manifest = json.loads((directory / 'cyber102-manifest.json').read_text(encoding='utf-8'))
        if any(manifest.get(key) != value for key, value in (
                ('repository', REPOSITORY), ('revision', REVISION), ('model', SPEC.model_id),
                ('quantization', 'UD-Q4_K_M'))):
            raise ValueError('Qwen3.8 GGUF manifest does not match the pinned release')
    # Always hash the actual files, including after a task or machine restart.
    return [verify_file(directory / name, entry) for name, entry in FILES.items()]


def write_manifest(directory):
    directory = Path(directory)
    records = verify_directory(directory, require_manifest=False)
    manifest = {'repository': REPOSITORY, 'revision': REVISION, 'model': SPEC.model_id,
                'quantization': 'UD-Q4_K_M', 'files': records,
                'source': f'https://huggingface.co/{REPOSITORY}/tree/{REVISION}'}
    temporary = directory / 'cyber102-manifest.json.part'
    temporary.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, directory / 'cyber102-manifest.json')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'models' / SPEC.directory)
    parser.add_argument('--download', action='store_true', help='Download missing files over pinned HTTPS')
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    if args.download:
        opener = build_opener(ProxyHandler({}), HttpsOnlyRedirect())
        for name, entry in FILES.items():
            path = args.directory / name
            if path.exists():
                verify_file(path, entry)
                continue
            temporary = path.with_name(path.name + '.part')
            url = f'https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/{quote(name)}'
            with opener.open(Request(url), timeout=60) as source, temporary.open('wb') as target:
                for block in iter(lambda: source.read(1024 * 1024), b''):
                    target.write(block)
            verify_file(temporary, entry)
            os.replace(temporary, path)
    print(json.dumps(write_manifest(args.directory), indent=2))


if __name__ == '__main__':
    main()
