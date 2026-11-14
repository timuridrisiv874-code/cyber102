"""Cross-compile the English–Russian translator with the Android NDK for arm64."""
import argparse
import hashlib
from pathlib import Path
import os
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / 'var/native/translation-src'
CPP = ROOT / 'android/app/src/main/cpp/translation'
ASSETS = ROOT / 'android/tiny/assets/en-ru'
SOURCES = [
    ('ctranslate2', 'https://github.com/OpenNMT/CTranslate2.git', 'v4.9.0', 'a22ba1c63ee35d1a0d25db149aa2a66d36a6a732'),
    ('sentencepiece', 'https://github.com/google/sentencepiece.git', 'v0.2.1', '31646a467d2051eb904e0b45de3a73e91fe1c1e3'),
]
ASSET_HASHES = {
    'config.json': '152eb448f1020eb55f90971d7b95f61896abee8415d555ca51ae7897ac0a0e68',
    'model.bin': '191b8552ead2b7dc67ad144cd792250bbcacd7824d883c5bf1f5a75fa58703c6',
    'shared_vocabulary.json': 'd7fdfce7c8bf8abc6df52deadd54333603a7ad03991d7383c9e6554b594000ad',
    'sentencepiece.model': '8282335e85cc36ac5ec1cba7ce18399ce948bf72f3c755b1aced54a13b373c02',
}


def run(*args):
    subprocess.run(list(map(str, args)), check=True)


def fetch_sources():
    NATIVE.mkdir(parents=True, exist_ok=True)
    for name, url, tag, revision in SOURCES:
        checkout = NATIVE / name
        if not checkout.exists():
            run('git', 'clone', '--branch', tag, '--depth', '1', url, checkout)
        actual = subprocess.check_output(['git', '-C', checkout, 'rev-parse', 'HEAD'], text=True).strip()
        if actual != revision:
            raise ValueError(f'Expected {name} at {revision}, found {actual}')
    ct2 = NATIVE / 'ctranslate2'
    run('git', '-C', ct2, 'submodule', 'update', '--init', '--depth', '1', '--jobs', '4',
        'third_party/spdlog', 'third_party/cpu_features', 'third_party/ruy')
    run('git', '-C', ct2 / 'third_party/ruy', 'submodule', 'update', '--init', '--depth', '1',
        'third_party/cpuinfo')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', required=True, type=Path)
    parser.add_argument('--ndk', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    for file, sha in ASSET_HASHES.items():
        if hashlib.sha256((ASSETS / file).read_bytes()).hexdigest() != sha:
            raise ValueError(f'Invalid bundled translation asset: {file}')
    fetch_sources()
    cmake = os.environ.get('CMAKE_BIN') or shutil.which('cmake')
    if not cmake:
        raise RuntimeError('Install CMake 3.24 or newer, or set CMAKE_BIN to its executable')
    ndk = args.sdk / 'ndk' / args.ndk
    ninja = args.sdk / 'cmake/3.22.1/bin/ninja'
    build = NATIVE / 'build'
    run(cmake, '-S', CPP, '-B', build, '-G', 'Ninja', '-DCMAKE_POLICY_VERSION_MINIMUM=3.5',
        f'-DCMAKE_MAKE_PROGRAM={ninja}', f'-DCMAKE_TOOLCHAIN_FILE={ndk}/build/cmake/android.toolchain.cmake',
        '-DANDROID_ABI=arm64-v8a', '-DANDROID_PLATFORM=android-26', '-DANDROID_STL=c++_static',
        '-DCMAKE_BUILD_TYPE=Release', f'-DTRANSLATION_SOURCE={NATIVE}')
    run(cmake, '--build', build, '--parallel', str(min(os.cpu_count() or 4, 8)), '--target', 'cyber_translation')
    strip, = (ndk / 'toolchains/llvm/prebuilt').glob('*/bin/llvm-strip')
    dest = args.output / 'arm64-v8a' / 'libcyber_translation.so'
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix('.tmp')
    run(strip, '--strip-unneeded', '-o', tmp, build / dest.name)
    tmp.replace(dest)


if __name__ == '__main__':
    main()
