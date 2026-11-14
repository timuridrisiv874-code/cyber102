"""Replay manual two-point taps against recorded iPhone 13 Pro RGB-D data.

Usage: python3 ios/Tests/replay_iphone_measurement.py /path/to/air_purifier_01.zip
Requires Pillow and swiftc. See measurement-validation.md for data and protocol.
"""

import argparse
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import statistics
import subprocess
import tempfile
import zipfile

from PIL import Image

# Taps selected visually on 768 x 576 RGB before reading the depth maps.
# Front lip of the outlet and corresponding bottom edge at the foot of the
# front-facing side. Move neither point if the app rejects its depth.
TAPS = {
    0: ((270, 304), (445, 289)),
    30: ((309, 169), (465, 206)),
    60: ((423, 261), (670, 247)),
    90: ((477, 279), (614, 300)),
    120: ((344, 256), (490, 274)),
    150: ((390, 313), (548, 313)),
    180: ((380, 322), (556, 326)),
}
ROOT = Path(__file__).resolve().parents[2]


def read_frames(archive):
    prefix = "air_purifier_01"
    with zipfile.ZipFile(archive) as bundle:
        def read(name):
            return bundle.read(f"{prefix}/{name}")

        truth = json.loads(read("metadata.json"))["metric_check"]["tape_cm"] * 10
        trajectory = json.loads(read("trajectory.json"))["frames"]
        pairs = []
        for frame, taps in TAPS.items():
            k = trajectory[frame]["rgb_intrinsics"]
            depth = Image.open(BytesIO(read(f"depth/{frame:06}.png")))
            confidence = Image.open(BytesIO(read(f"confidence/{frame:06}.png")))
            assert depth.size == confidence.size
            points = []
            for x, y in taps:
                nx, ny = x / 768, y / 576
                dx, dy = int(nx * depth.width), int(ny * depth.height)
                patch = []
                for row in range(dy - 1, dy + 2):
                    for col in range(dx - 1, dx + 2):
                        mm = depth.getpixel((col, row))
                        conf = confidence.getpixel((col, row))
                        patch.append(mm / 1000 if mm > 0 and conf in (1, 2) else None)
                points.append(dict(u=nx * k["width"] - 0.5, v=ny * k["height"] - 0.5, patch=patch))
            pairs.append(dict(frame=frame, points=points, **{key:k[key] for key in ("fx","fy","cx","cy")}))
    return truth, pairs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args()
    digest = sha256()
    with args.bundle.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != "8078c1d4a697f42a7075a00bc6dbf6a2c2bc1b8da8593d7141642c130a2c73df":
        parser.error("Expected air_purifier_01.zip from Zenodo release 21499803.")
    truth, pairs = read_frames(args.bundle)
    with tempfile.TemporaryDirectory() as directory:
        executable = Path(directory) / "replay"
        subprocess.run(["swiftc", "-module-cache-path", directory,
                        ROOT / "ios/Cyber102/MeasurementGeometry.swift", ROOT / "ios/Cyber102/LidarSampling.swift",
                        ROOT / "ios/Tests/ReplayDepthChecks.swift", "-o", executable], check=True)
        lines = subprocess.check_output([executable], input=json.dumps(pairs), text=True).splitlines()
    values = []
    for line in lines:
        frame, value = line.split()
        if value != "rejected":
            values.append(float(value))
        print(frame, value)
    if not values:
        raise RuntimeError("No frame passed both depth checks.")
    errors = [abs(value - truth) for value in values]
    print(f"Reference {truth:.1f} mm; accepted {len(values)}/{len(pairs)}; "
          f"MAE {statistics.mean(errors):.2f} mm; maximum absolute error {max(errors):.2f} mm; "
          f"mean absolute relative error {100*statistics.mean(errors)/truth:.2f}%.")


if __name__ == "__main__":
    main()
