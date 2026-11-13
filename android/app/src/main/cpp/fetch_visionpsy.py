"""Prepare the checksum-pinned VisionPsy fork for the Android build."""
import hashlib
from pathlib import Path
import tarfile
from urllib.request import urlopen

REVISION = '4effbda7afed28688498147759f62cd487e90944'
SHA256 = '79af525ba56656d009e0fba8ac45a8f1d077fe66e29e6f3c3abc2d4f18030fa3'
BASE = Path(__file__).resolve().parents[5] / 'var/native'


def main():
    BASE.mkdir(parents=True, exist_ok=True)
    archive = BASE / f'visionpsy-{REVISION}.tar.gz'
    source = BASE / f'visionpsy-{REVISION}'
    runtime = source / 'llama-cpp-inference/llama.cpp-custom'
    marker = runtime / 'CYBER102_REVISION'
    if marker.exists() and marker.read_text().strip() == REVISION:
        return
    if not archive.exists():
        partial = archive.with_suffix('.part')
        try:
            with urlopen('https://codeload.github.com/tether-ai-research/qvac-visionpsy-nano/tar.gz/' + REVISION, timeout=60) as stream, partial.open('wb') as out:
                while chunk := stream.read(1024 * 1024):
                    out.write(chunk)
                    if out.tell() > 100 * 1024 * 1024:
                        raise ValueError('Unexpectedly large VisionPsy archive')
            partial.replace(archive)
        finally:
            partial.unlink(missing_ok=True)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        raise ValueError('VisionPsy source checksum mismatch')
    prefix = f'qvac-visionpsy-nano-{REVISION}/'
    with tarfile.open(archive) as bundle:
        for member in bundle:
            name = member.name
            if not name.startswith(prefix + 'llama-cpp-inference/llama.cpp-custom/'):
                continue
            member.name = name[len(prefix):]
            if (not (source / member.name).resolve().is_relative_to(source.resolve())
                    or member.issym() or member.islnk()):
                raise ValueError('Unexpected archive path or link')
            bundle.extract(member, source, filter='data')
    marker.write_text(REVISION + '\n')


if __name__ == '__main__':
    main()
