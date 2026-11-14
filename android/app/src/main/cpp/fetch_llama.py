"""Fetch the checksum-pinned public native dependency; never downloads models."""
import hashlib
from pathlib import Path
import tarfile
import urllib.request

REVISION = 'b1b770d90d94cad9e9ea5b993bdbc383a522fb28'
ARCHIVE_SHA256 = 'ae8556670b17c25931cd39eb7c95617650eaf6091bbcff206750583ddde1acb4'
ROOT = Path(__file__).resolve().parents[5]
DESTINATION = ROOT / 'var' / 'native'


def main():
    DESTINATION.mkdir(parents=True, exist_ok=True)
    archive = DESTINATION / f'{REVISION}.tar.gz'
    source = DESTINATION / f'llama.cpp-{REVISION}'
    marker = source / 'CYBER102_REVISION'
    if marker.exists() and marker.read_text().strip() == REVISION:
        print(source)
        return
    if not archive.exists():
        partial = archive.with_suffix('.partial')
        with urllib.request.urlopen(f'https://codeload.github.com/ggml-org/llama.cpp/tar.gz/{REVISION}', timeout=60) as response, partial.open('wb') as output:
            total = 0
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > 100 * 1024 * 1024:
                    raise ValueError('Native source archive is unexpectedly large')
                output.write(chunk)
        partial.replace(archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != ARCHIVE_SHA256:
        raise ValueError('Native source archive checksum mismatch')
    with tarfile.open(archive) as bundle:
        for member in bundle.getmembers():
            target = (DESTINATION / member.name).resolve()
            if not target.is_relative_to(source.resolve()) or member.issym() or member.islnk():
                raise ValueError('Unexpected archive path or link')
        bundle.extractall(DESTINATION, filter='data')
    marker.write_text(REVISION + '\n')
    print(source)


if __name__ == '__main__':
    main()
