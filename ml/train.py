"""Train and evaluate advisory binary models using labeled field observations."""

import argparse
import csv
import hashlib
import json
import math
import platform
import shutil
from importlib.metadata import version
from itertools import groupby
from pathlib import Path
from tempfile import mkdtemp
from time import perf_counter

from .contract import FEATURES, LABELS, PARTITIONS, labeled_partitions, read_observations, split_observations, unique_fields


def class_counts(rows, label):
    positives = sum(row.labels[label] for row in rows)
    return {"rows": len(rows), "assets": len({row.serial for row in rows}),
            "positives": positives, "negatives": len(rows) - positives}


def counts_and_metrics(truth, scores, threshold=0.5):
    if not truth or len(truth) != len(scores) or not all(math.isfinite(score) and 0 <= score <= 1 for score in scores):
        raise ValueError("expected one probability per evaluated row")
    tp = fp = tn = fn = 0
    for label, score in zip(truth, scores):
        if score >= threshold:
            if label:
                tp += 1
            else:
                fp += 1
        elif label:
            fn += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    specificity = tn / (tn + fp) if tn + fp else 0.0
    # Accumulate recall increments by score group so tied scores are treated equally.
    positives = tp + fn
    retrieved = true_retrieved = 0
    average_precision = 0.0
    ranked = sorted(zip(scores, truth), key=lambda row: row[0], reverse=True)
    for _, tied in groupby(ranked, key=lambda row: row[0]):
        group = list(tied)
        hits = sum(label for _, label in group)
        retrieved += len(group)
        true_retrieved += hits
        if positives:
            average_precision += (hits / positives) * (true_retrieved / retrieved)
    return {
        "threshold": threshold,
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "precision": precision, "recall": recall, "specificity": specificity,
        "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0,
        "balanced_accuracy": (recall + specificity) / 2,
        "average_precision": average_precision if positives else None,
        "prevalence": positives / len(truth),
    }


def train_catboost(label, partitions, output, args):
    from catboost import CatBoostClassifier

    device = {"task_type": "GPU", "devices": "0"} if args.gpu else {"task_type": "CPU"}
    model = CatBoostClassifier(
        iterations=500, depth=6, learning_rate=0.05, loss_function="Logloss",
        eval_metric="Logloss", random_seed=args.seed, allow_writing_files=False,
        thread_count=args.cpus, **device,
    )
    training, validation = partitions["train"], partitions["validation"]
    model.fit(
        [list(row.features) for row in training], [row.labels[label] for row in training],
        eval_set=([list(row.features) for row in validation], [row.labels[label] for row in validation]),
        early_stopping_rounds=40, verbose=False,
    )
    artifact = f"{label}.cbm"
    model.save_model(str(output / artifact))
    scores = {
        part: model.predict_proba([list(row.features) for row in partitions[part]])[:, 1].tolist()
        for part in ("validation", "test")
    }
    return artifact, scores, {"trees": model.tree_count_}


def train_autogluon(label, partitions, output, args):
    from autogluon.tabular import TabularPredictor
    from pandas import DataFrame

    def frame(partition, include_label):
        rows = partitions[partition]
        table = DataFrame([row.features for row in rows], columns=list(FEATURES))
        if include_label:
            table[label] = [row.labels[label] for row in rows]
        return table

    artifact = label
    fit_options = autogluon_fit_options(args)
    predictor = TabularPredictor(label=label, problem_type="binary", eval_metric="log_loss",
                                 positive_class=1, path=str(output / artifact), verbosity=1)
    predictor.fit(
        frame("train", True), tuning_data=frame("validation", True),
        **fit_options,
    )
    # No test data is passed to model selection or the candidate leaderboard.
    candidates = [
        {"model": str(row["model"]), "validation_log_loss": -float(row["score_val"])}
        for row in predictor.leaderboard(display=False).to_dict(orient="records")
    ]
    scores = {
        part: predictor.predict_proba(frame(part, False), as_multiclass=False).tolist()
        for part in ("validation", "test")
    }
    predictor.save_space(remove_data=True)
    return artifact, scores, {"model": predictor.model_best, "fit": fit_options,
                              "selection": "minimum validation log_loss",
                              "validation_candidates": candidates}


def autogluon_fit_options(args):
    """A small CPU comparison, with no random folds or pretrained model downloads."""
    if args.gpu:
        raise ValueError("the AutoGluon experiment uses CPU; remove --gpu or use --engine catboost")
    return {
        "hyperparameters": {
            "CAT": {"iterations": 500, "depth": 6, "learning_rate": 0.05, "random_seed": args.seed},
            "RF": {"n_estimators": 300, "max_depth": 12, "min_samples_leaf": 2, "random_state": args.seed},
        },
        "num_bag_folds": 0, "num_stack_levels": 0, "auto_stack": False,
        "dynamic_stacking": False, "refit_full": False,
        "fit_weighted_ensemble": True, "calibrate_decision_threshold": False,
        "fit_strategy": "sequential", "num_cpus": args.cpus, "num_gpus": 0,
        "time_limit": args.time_limit, "memory_limit": args.memory_gb,
    }


def file_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def train(args, selected, summary, digests):
    if args.output.exists():
        raise ValueError("output must be a new directory")
    if args.engine == "autogluon":
        autogluon_fit_options(args)
    fit = train_catboost if args.engine == "catboost" else train_autogluon
    distribution = "catboost" if args.engine == "catboost" else "autogluon.tabular"
    try:
        engine_version = version(distribution)
    except ImportError as error:
        raise ValueError(f"install the optional dependencies for {args.engine} first") from error
    report = {
        "schema_version": 2, "engine": args.engine, "engine_version": engine_version,
        "python_version": platform.python_version(), "source": "field",
        "purpose": "offline field-data experiment; not an operational diagnostic model",
        "runtime_ready": False,
        "device": "gpu" if args.gpu else "cpu", "seed": args.seed, "cpus": args.cpus,
        "features": list(FEATURES), "dataset": summary, "sha256": digests,
        "models": {},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(mkdtemp(prefix=f".{args.output.name}-", dir=args.output.parent))
    try:
        for label, partitions in selected.items():
            started = perf_counter()
            artifact, probabilities, details = fit(label, partitions, staging, args)
            metrics = {
                part: counts_and_metrics([row.labels[label] for row in partitions[part]], scores)
                for part, scores in probabilities.items()
            }
            prevalence = sum(row.labels[label] for row in partitions["train"]) / len(partitions["train"])
            baseline = {
                part: counts_and_metrics([row.labels[label] for row in partitions[part]],
                                         [prevalence] * len(partitions[part]))
                for part in ("validation", "test")
            }
            report["models"][label] = {
                "artifact": artifact, "metrics": metrics, "elapsed_seconds": round(perf_counter() - started, 3),
                "train_prevalence_baseline": {"probability": prevalence, "metrics": baseline}, **details,
            }
        (staging / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        if args.output.exists():
            raise ValueError("output already exists")
        staging.rename(args.output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return report


def positive_int(value):
    result = int(value)
    if result < 1:
        raise argparse.ArgumentTypeError("expected a positive integer")
    return result


def positive_float(value):
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("expected a positive finite number")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="check the dataset and split without importing ML packages")
    training = commands.add_parser("train", help="train and evaluate on labeled field observations")
    for command in (validate, training):
        command.add_argument("--data", type=Path, required=True)
        command.add_argument("--split", type=Path, required=True)
        command.add_argument("--labels", choices=LABELS, nargs="+", default=list(LABELS))
    training.add_argument("--engine", choices=("autogluon", "catboost"), default="autogluon")
    training.add_argument("--output", type=Path, required=True)
    training.add_argument("--gpu", action="store_true", help="CatBoost only: use the first visible CUDA GPU")
    training.add_argument("--cpus", type=positive_int, default=4)
    training.add_argument("--seed", type=int, default=42)
    training.add_argument("--time-limit", type=positive_int, default=300, help="AutoGluon time budget per label in seconds")
    training.add_argument("--memory-gb", type=positive_float, default=4.0, help="AutoGluon soft memory budget in GB")
    args = parser.parse_args(argv)

    try:
        manifest = json.loads(args.split.read_text(encoding="utf-8-sig"), object_pairs_hook=unique_fields)
        observations = read_observations(args.data)
        partitions = split_observations(observations, manifest)
        selected = labeled_partitions(partitions, args.labels)
        summary = {
            "rows_total": len(observations),
            "rows_outside_split": len(observations) - sum(map(len, partitions.values())),
            "labels": {label: {part: class_counts(rows[part], label) for part in PARTITIONS}
                       for label, rows in selected.items()},
        }
        if args.command == "train":
            digests = {"data": file_digest(args.data), "split": file_digest(args.split)}
            train(args, selected, summary, digests)
            print(args.output / "report.json")
        else:
            print(json.dumps(summary, indent=2))
    except (OSError, ValueError, ImportError, csv.Error, RecursionError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
