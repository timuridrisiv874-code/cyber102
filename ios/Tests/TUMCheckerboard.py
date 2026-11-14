"""Replay a real 89.2 mm checkerboard on the production two-point Swift implementation.

Requires swiftc and opencv-python-headless. From the project checkout run
    python3 ios/Tests/TUMCheckerboard.py /path/to/rgbd_dataset_freiburg3_checkerboard_large

Raw RGB-D recording and ground-truth dimensions are from the TUM Computer Vision Group:
https://webshare.cvg.cit.tum.de/g/rgbd/dataset/ (Calibration Files → freiburg3_checkerboard_large).
The Asus Xtion provides registered RGB/depth without an Apple confidence map.
"""
import argparse
import collections
import json
import pathlib
import statistics
import subprocess
import tempfile

import cv2


def frames(path):
    return [(float(t), name) for line in path.read_text().splitlines()
            if line and not line.startswith("#") for t, name in [line.split()]]


def pair_for(rgb_path, depth_path):
    gray = cv2.imread(str(rgb_path), cv2.IMREAD_GRAYSCALE)
    depth = cv2.imread(str(depth_path), cv2.IMREAD_UNCHANGED)
    assert gray.shape == depth.shape == (480, 640) and str(depth.dtype) == "uint16"
    found, corners = cv2.findChessboardCornersSB(gray, (9, 7))
    if not found:
        return None
    pair = []
    for u, v in corners.reshape(7, 9, 2)[3, 4:6]:
        # OpenCV coordinates put each pixel center at an integer. The app converts the
        # normalized image location to this same coordinate system at unprojection.
        x, y = int(float(u) + 0.5), int(float(v) + 0.5)
        patch = depth[y - 1:y + 2, x - 1:x + 2]
        assert patch.shape == (3, 3)
        pair.append(dict(u=float(u), v=float(v), rawDepth=patch.ravel().tolist()))
    return pair


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording", type=pathlib.Path)
    args = parser.parse_args()
    dataset = args.recording
    rgb = frames(dataset / "rgb.txt")
    depth = frames(dataset / "depth.txt")
    samples = []
    pairs = []
    paired_rows = []
    for i in range(0, len(rgb), 60):
        t, rgb_name = rgb[i]
        depth_time, depth_name = min(depth, key=lambda d: abs(d[0] - t))
        skew = abs(depth_time - t)
        row = {"rgbIndex": i, "rgbTimestamp": t, "depthTimestamp": depth_time,
               "pairSkewMs": round(skew * 1000, 3)}
        samples.append(row)
        if skew > 0.020:
            row["status"] = "timestamps_apart"
            continue
        pair = pair_for(dataset / rgb_name, dataset / depth_name)
        if pair is None:
            row["status"] = "board_not_visible"
            continue
        pairs.append(pair)
        paired_rows.append(row)
    tests = pathlib.Path(__file__).resolve().parent
    app = tests.parent / "Cyber102"
    with tempfile.TemporaryDirectory() as d:
        binary = pathlib.Path(d) / "Replay"
        subprocess.run(["swiftc", "-module-cache-path", str(pathlib.Path(d) / "cache"),
                        str(app / "MeasurementGeometry.swift"), str(app / "LidarSampling.swift"),
                        str(tests / "TUMDepthReplay.swift"), "-o", str(binary)], check=True)
        out = subprocess.run([str(binary)], input=json.dumps(pairs), text=True, capture_output=True, check=True)
    lengths = json.loads(out.stdout)
    measurements = []
    for row, mm in zip(paired_rows, lengths, strict=True):
        if mm is None:
            row["status"] = "depth_unreliable"
        else:
            row.update(status="measured", lengthMm=round(mm, 3))
            measurements.append(mm)
    truth = 89.2
    errors = [abs(v - truth) for v in measurements]
    report = {
        "recording": "TUM freiburg3_checkerboard_large",
        "sensor": "Asus Xtion (structured light, no ARKit confidence map)",
        "rgbFrames": len(rgb), "rgbStep": 60, "groundTruthMm": truth, "pattern": [9, 7],
        "cornerRowColZeroBased": [[3, 4], [3, 5]], "maxPairSkewMs": 20,
        "opencv": cv2.__version__, "counts": dict(collections.Counter(s["status"] for s in samples)),
        "meanAbsoluteErrorMm": round(statistics.mean(errors), 3),
        "medianAbsoluteErrorMm": round(statistics.median(errors), 3), "maxAbsoluteErrorMm": round(max(errors), 3),
        "meanAbsoluteErrorPercent": round(statistics.mean(errors) / truth * 100, 3),
        "minMeasuredMm": round(min(measurements), 3), "maxMeasuredMm": round(max(measurements), 3),
        "samples": samples,
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
