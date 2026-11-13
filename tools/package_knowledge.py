"""Build the shareable Cyber102 research archive from an explicit public-source allowlist."""
from hashlib import sha256
from pathlib import Path
import json
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / 'output/cyber102-research-pack.zip'
ALLOWED = ('docs', 'ml', 'server', 'web', 'tests', 'deploy', 'data_tools', 'tools', 'vision_runtime')
SUFFIXES = {'.md', '.json', '.py', '.mjs', '.cjs', '.txt', '.ps1', '.html', '.js', '.css', '.jpg'}


def main():
    files = [ROOT / p for p in ('README.md', '.gitignore', 'requirements.txt',
             'data/README.md', 'data/commons-manifest.json',
             'TIMUR/README.md', 'TIMUR/TASKS.md', 'TIMUR/CHECKLIST.md',
             'output/README.md', 'output/documents/cyber102-engineering-pack.docx',
             'output/pdf/cyber102-engineering-pack.pdf')]
    for directory in ALLOWED:
        for path in sorted((ROOT / directory).rglob('*')):
            if path.is_relative_to(ROOT / 'ml/artifacts'):
                continue
            if path.is_file() and '__pycache__' not in path.parts and (
                path.suffix in SUFFIXES or path.name == 'Caddyfile'
            ):
                files.append(path)
    files.extend((ROOT / 'data/customer').glob('*.json'))
    files.extend((ROOT / 'data/customer').glob('*.md'))
    files = sorted(set(files))
    for path in files:
        if not path.is_file() or not path.resolve().is_relative_to(ROOT):
            raise ValueError(f'Missing or external package input: {path.name}')
    manifest = {'version': '1.4', 'prepared': '2026-11-13',
                'scope': 'Cyber102 documentation, source code, synthetic templates and imported customer example metadata and sensor rows. Original customer files, image previews, model weights and external manuals are excluded.',
                'files': [{'path': str(f.relative_to(ROOT)), 'bytes': f.stat().st_size,
                           'sha256': sha256(f.read_bytes()).hexdigest()} for f in files]}
    ARCHIVE.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(ARCHIVE, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for path in files:
            output.write(path, path.relative_to(ROOT))
        output.writestr('PACKAGE-MANIFEST.json', json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    with zipfile.ZipFile(ARCHIVE) as output:
        if output.testzip() is not None:
            raise ValueError('Archive integrity check failed')
        for entry in manifest['files']:
            assert sha256(output.read(entry['path'])).hexdigest() == entry['sha256']
    print(f'{ARCHIVE}: {len(files)} files, {ARCHIVE.stat().st_size} bytes; all hashes verified')


if __name__ == '__main__':
    main()
