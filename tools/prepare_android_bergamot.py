"""Verify or fetch the exact offline Firefox EN→RU files bundled in the Android APK."""

import argparse
import hashlib
import shutil
import tempfile
import urllib.request
from pathlib import Path


DESTINATION = Path(__file__).resolve().parents[1] / "var/models/bergamot-android/assets/translation-runtime"
MODELS = "https://firefox-settings-attachments.cdn.mozilla.net/main-workspace/translations-models/"
ENGINE = "https://raw.githubusercontent.com/mozilla/translate/14fdf57e323e532c3e11c75618e676c96d93df00/js/"
FILES = {
    "model.enru.intgemm.alphas.bin": (
        MODELS + "db6b48bb-b563-4ea1-bbf4-b033fa9cc482.bin",
        "184cb5cda528eeefc0f75f5d0035d787b71d74af135e3c5608d01ae02ecfb920",
    ),
    "lex.50.50.enru.s2t.bin": (
        MODELS + "e88cc45c-0e0b-45d3-a957-8d1ba3a9b5aa.bin",
        "4d91839726b960e70b6d05c53d0cffd16262832b1c0e1ea99d66f412dcc6a239",
    ),
    "vocab.enru.spm": (
        MODELS + "568807dd-9620-4c4d-a0b5-d651d64c0ab0.spm",
        "56ee63e14e8cb926c394242adc3ed7cc602644c3d33058cff2ce2959d52a6258",
    ),
    "bergamot-translator-worker.js": (
        ENGINE + "bergamot-translator-worker.js",
        "2cb354e739ba02cb04ee93da0c24f4b4d9de5e17c26fc41f1aeed4fb41811a5c",
    ),
    "bergamot-translator-worker.wasm": (
        ENGINE + "bergamot-translator-worker.wasm",
        "65cf5be754ea6d44db018a38e1f4a5c2fb2d7ef601d0e0067abde4e3d145386f",
    ),
}


def matches(path, expected):
    if not path.is_file():
        return False
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() == expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--download", action="store_true")
    source.add_argument("--from-directory", type=Path, help="Copy an existing verified download")
    args = parser.parse_args()
    for name, (url, expected) in FILES.items():
        destination = DESTINATION / name
        if matches(destination, expected):
            continue
        if not (args.download or args.from_directory):
            parser.exit(1, f"Missing or invalid {name}. Run tools/prepare_android_bergamot.py --download.\n")
        DESTINATION.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=DESTINATION, delete=False) as tmp:
            staged = Path(tmp.name)
        try:
            if args.from_directory:
                local = args.from_directory / name
                if not local.is_file():
                    local = args.from_directory / "translate-main/js" / name
                shutil.copyfile(local, staged)
            else:
                with urllib.request.urlopen(url, timeout=120) as response, staged.open("wb") as output:
                    shutil.copyfileobj(response, output)
            if not matches(staged, expected):
                parser.exit(1, f"SHA-256 mismatch for {name}.\n")
            staged.replace(destination)
        finally:
            staged.unlink(missing_ok=True)
    print(f"Verified {len(FILES)} Bergamot files in {DESTINATION}")


if __name__ == "__main__":
    main()
