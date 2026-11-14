"""Export the offline equipment picker from the same verified registry as the server."""

import argparse
import json
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server import recommendations
from server.demo_data import PASSPORT

DESTINATION = ROOT / "mobile" / "equipment-catalog.json"


def render_catalog():
    records = []
    seen = set()
    for item in [PASSPORT, *recommendations.registry()]:
        demo = item is PASSPORT
        entry = {
            "serial": item["serial"],
            "name": item["name" if demo else "title"],
            "model": item["model"],
            "demo": demo,
        }
        if not all(isinstance(entry[key], str) and entry[key].strip()
                   for key in ("serial", "name", "model")):
            raise ValueError("Incomplete equipment identity in source registry")
        if entry["serial"] in seen:
            raise ValueError(f"Duplicate equipment serial: {entry['serial']}")
        seen.add(entry["serial"])
        if item.get("manufacturer"):
            entry["manufacturer"] = item["manufacturer"]
        records.append(entry)
    catalog = {"schemaVersion": 1, "equipment": records}
    return (json.dumps(catalog, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify the existing file without changing it.")
    args = parser.parse_args()
    content = render_catalog()
    if DESTINATION.is_file() and DESTINATION.read_bytes() == content:
        print("Mobile equipment catalog is up to date.")
        return 0
    if args.check:
        print("Mobile equipment catalog is missing or out of date. Run python3 tools/prepare_mobile_catalog.py.",
              file=sys.stderr)
        return 1
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=DESTINATION.parent, delete=False) as tmp:
        path = Path(tmp.name)
        try:
            tmp.write(content)
            tmp.close()
            path.chmod(0o644)
            path.replace(DESTINATION)
        finally:
            path.unlink(missing_ok=True)
    print(f"Wrote {DESTINATION.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
