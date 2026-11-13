"""Reproduce a CPU CatBoost benchmark on UCI hydraulic rig pump leakage."""

import argparse
import hashlib
import json
import platform
import shutil
from collections import Counter
from pathlib import Path
from tempfile import mkdtemp
from zipfile import BadZipFile, ZipFile


CYCLE_COUNT = 2205
CLASSES = (0, 1, 2)
SENSORS = {
    "PS1": 6000, "PS2": 6000, "PS3": 6000, "PS4": 6000, "PS5": 6000, "PS6": 6000,
    "EPS1": 6000, "FS1": 600, "FS2": 600,
    "TS1": 60, "TS2": 60, "TS3": 60, "TS4": 60, "VS1": 60,
}
STATISTICS = ("mean", "std", "min", "max", "q05", "median", "q95", "last_first_5s_mean_diff")
FIT = {
    "iterations": 500, "depth": 5, "learning_rate": 0.05,
    "loss_function": "MultiClass", "eval_metric": "MultiClass",
    "random_seed": 42, "task_type": "CPU", "thread_count": 2, "allow_writing_files": False,
}


def archive_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as data:
        for chunk in iter(lambda: data.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_cycles(path, np):
    """Read official archive matrices in row order and aggregate each cycle independently."""
    with ZipFile(path) as archive:
        with archive.open("profile.txt") as profile_file:
            profile = np.loadtxt(profile_file, dtype=np.int64)
        if profile.shape != (CYCLE_COUNT, 5) or set(profile[:, 2]) != set(CLASSES):
            raise ValueError("profile.txt does not match the official 2205-cycle label schema")
        labels = profile[:, 2]

        columns, feature_names = [], []
        for sensor, sample_count in SENSORS.items():
            with archive.open(f"{sensor}.txt") as sensor_file:
                values = np.loadtxt(sensor_file, dtype=np.float64)
            if values.shape != (CYCLE_COUNT, sample_count) or not np.isfinite(values).all():
                raise ValueError(f"{sensor}.txt does not match the expected cycle and sample counts")
            first_last_window = sample_count // 12  # Five seconds in each 60-second cycle.
            columns.extend((
                values.mean(axis=1), values.std(axis=1), values.min(axis=1), values.max(axis=1),
                *np.quantile(values, (0.05, 0.50, 0.95), axis=1),
                values[:, -first_last_window:].mean(axis=1) - values[:, :first_last_window].mean(axis=1),
            ))
            feature_names.extend(f"{sensor}_{statistic}" for statistic in STATISTICS)
            del values
    return np.column_stack(columns), labels, feature_names


def counts(labels):
    frequencies = Counter(int(label) for label in labels)
    return {str(label): frequencies[label] for label in CLASSES}


def evaluate(truth, predicted):
    confusion = [[0] * len(CLASSES) for _ in CLASSES]
    for actual, prediction in zip(truth, predicted):
        confusion[int(actual)][int(prediction)] += 1
    true_positives = [confusion[label][label] for label in CLASSES]
    actual_counts = [sum(row) for row in confusion]
    predicted_counts = [sum(column) for column in zip(*confusion)]
    recalls = [tp / support for tp, support in zip(true_positives, actual_counts)]
    f1_scores = [
        2 * tp / (support + predicted_count)
        for tp, support, predicted_count in zip(true_positives, actual_counts, predicted_counts)
    ]
    return {
        "accuracy": sum(true_positives) / len(truth),
        "macro_f1": sum(f1_scores) / len(CLASSES),
        "balanced_accuracy": sum(recalls) / len(CLASSES),
        "confusion_matrix": confusion,
    }


def run(archive, output):
    import catboost
    import numpy as np

    if output.exists():
        raise ValueError("output must be a new directory")
    data_digest = archive_digest(archive)
    features, labels, feature_names = load_cycles(archive, np)
    train_end = CYCLE_COUNT * 60 // 100
    validation_end = CYCLE_COUNT * 80 // 100
    partitions = {
        "train": (0, train_end),
        "validation": (train_end, validation_end),
        "test": (validation_end, CYCLE_COUNT),
    }
    class_balance = {}
    for name, (start, end) in partitions.items():
        balance = counts(labels[start:end])
        if not all(balance.values()):
            raise ValueError(f"missing class in fixed chronological {name} partition: {balance}")
        class_balance[name] = {"cycles": end - start, "first_cycle": start + 1, "last_cycle": end,
                               "class_counts": balance}

    model = catboost.CatBoostClassifier(**FIT)
    train = catboost.Pool(features[:train_end], labels[:train_end], feature_names=feature_names)
    validation = catboost.Pool(features[train_end:validation_end], labels[train_end:validation_end],
                               feature_names=feature_names)
    model.fit(train, eval_set=validation, early_stopping_rounds=40, verbose=False)
    metrics = {}
    majority = Counter(int(label) for label in labels[:train_end]).most_common(1)[0][0]
    for name in ("validation", "test"):
        start, end = partitions[name]
        predictions = model.predict(features[start:end], thread_count=2).ravel()
        metrics[name] = {
            "catboost": evaluate(labels[start:end], predictions),
            "train_majority_baseline": evaluate(labels[start:end], np.full(end - start, majority)),
        }
    report = {
        "purpose": "single hydraulic rig benchmark, not field validation",
        "dataset_doi": "10.24432/C5CW21",
        "archive_sha256": data_digest,
        "label": "internal pump leakage, profile.txt column 3",
        "classes": {"0": "no leakage", "1": "weak leakage", "2": "severe leakage"},
        "split": "contiguous cycle order, 60/20/20",
        "partitions": class_balance,
        "feature_names": feature_names,
        "fit": {**FIT, "early_stopping_rounds": 40, "saved_trees": model.tree_count_},
        "versions": {"python": platform.python_version(), "catboost": catboost.__version__,
                     "numpy": np.__version__},
        "metrics": metrics,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        model.save_model(str(staging / "uci-internal-leakage.cbm"))
        (staging / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        staging.rename(output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True, help="official ZIP from UCI")
    parser.add_argument("--output", type=Path, required=True, help="new output directory")
    args = parser.parse_args(argv)
    try:
        report = run(args.archive, args.output)
    except (BadZipFile, ImportError, KeyError, OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(report["metrics"]["test"], indent=2))
    print(args.output / "report.json")


if __name__ == "__main__":
    main()
