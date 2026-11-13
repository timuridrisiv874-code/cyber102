"""Four fixed CPU experiments on real UCI hydraulic-rig leakage labels.

The historical test partition is reused and is not a new blind test. Synthetic
feature perturbations measure sensitivity only; they do not create new fault labels.
"""
import argparse
import csv
from collections import Counter
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
import sys
import time
from zipfile import ZIP_DEFLATED, ZipFile

from .benchmark_uci import CYCLE_COUNT, CLASSES, FIT, SENSORS, STATISTICS, archive_digest, counts, evaluate


SPLITS = {'train': (0, 1323), 'validation': (1323, 1764), 'test': (1764, 2205)}
PAIRS = (('PS1', 'PS2'), ('PS3', 'PS4'), ('PS5', 'PS6'),
         ('FS1', 'FS2'), ('TS1', 'TS2'), ('TS3', 'TS4'))
EXTRA = ('rms', 'peak_to_peak', 'iqr', 'mad', 'skewness', 'excess_kurtosis',
         'mean_absolute_difference', 'slope_per_second', 'lag1_correlation',
         'first20s_mean', 'first20s_std', 'middle20s_mean', 'middle20s_std', 'last20s_mean', 'last20s_std')
ARMS = (('catboost_baseline', 'catboost', 'baseline'), ('autogluon_baseline', 'autogluon', 'baseline'),
        ('catboost_enhanced', 'catboost', 'enhanced'), ('autogluon_enhanced', 'autogluon', 'enhanced'))
STRESS = {'seed': 20261113, 'noiseTrainStdFraction': 0.10, 'dropChannel': 'PS1',
          'driftChannel': 'PS1', 'driftTrainStdFraction': 0.50,
          'scope': 'Independent feature-space sensitivity perturbations on the real test rows; '
                   'not a physical sensor simulator, not independent validation, not new fault ground truth.'}


def correlation(first, second, np):
    a, b = first - first.mean(axis=1, keepdims=True), second - second.mean(axis=1, keepdims=True)
    denominator = np.sqrt(np.sum(a * a, axis=1) * np.sum(b * b, axis=1))
    result = np.divide(np.sum(a * b, axis=1), denominator,
                       out=np.zeros(len(first), dtype=float), where=denominator > 1e-12)
    return np.clip(result, -1, 1)


def sensor_features(values, np):
    """Use only samples from the current completed 60-second cycle."""
    if values.ndim != 2 or values.shape[1] % 60 or not np.isfinite(values).all():
        raise ValueError('Expected finite, equal-rate 60-second sensor cycles')
    length = values.shape[1]
    mean, std = values.mean(axis=1), values.std(axis=1)
    quantiles = np.quantile(values, (0.05, 0.25, 0.50, 0.75, 0.95), axis=1)
    window = length // 12
    base = np.column_stack((mean, std, values.min(axis=1), values.max(axis=1),
                            quantiles[0], quantiles[2], quantiles[4],
                            values[:, -window:].mean(axis=1) - values[:, :window].mean(axis=1)))
    z = (values - mean[:, None]) / np.where(std > 1e-12, std, 1)[:, None]
    skew = (z ** 3).mean(axis=1)
    kurtosis = np.where(std > 1e-12, (z ** 4).mean(axis=1) - 3, 0)
    del z
    times = np.arange(length, dtype=float) * (60 / length)
    times -= times.mean()
    extra = [np.sqrt(np.mean(values * values, axis=1)), np.ptp(values, axis=1),
             quantiles[3] - quantiles[1], np.median(np.abs(values - quantiles[2][:, None]), axis=1),
             skew, kurtosis, np.abs(np.diff(values, axis=1)).mean(axis=1),
             values @ times / np.sum(times * times), correlation(values[:, :-1], values[:, 1:], np)]
    for block in np.split(values, 3, axis=1):
        extra.extend((block.mean(axis=1), block.std(axis=1)))
    seconds = values.reshape(len(values), 60, length // 60).mean(axis=2)
    return base, np.column_stack(extra), seconds


def load_features(path, np):
    """Keep one raw sensor matrix in memory, plus small per-cycle summaries."""
    baseline, extras, names, extra_names, dependencies, second_means = [], [], [], [], {}, {}
    with ZipFile(path) as archive:
        with archive.open('profile.txt') as source:
            profile = np.loadtxt(source, dtype=np.int64)
        if profile.shape != (CYCLE_COUNT, 5) or set(profile[:, 2]) != set(CLASSES):
            raise ValueError('Expected the official 2205-cycle profile with leakage classes 0/1/2')
        labels = profile[:, 2].copy()
        del profile  # Other condition labels and the stable flag never become predictors.
        for sensor, sample_count in SENSORS.items():
            with archive.open(sensor + '.txt') as source:
                values = np.loadtxt(source, dtype=np.float64)
            if values.shape != (CYCLE_COUNT, sample_count):
                raise ValueError(f'Unexpected shape for {sensor}')
            base, extra, seconds = sensor_features(values, np)
            del values
            baseline.append(base)
            extras.append(extra)
            second_means[sensor] = seconds
            for group, statistics in ((names, STATISTICS), (extra_names, EXTRA)):
                for statistic in statistics:
                    name = f'{sensor}_{statistic}'
                    group.append(name)
                    dependencies[name] = [sensor]
    cross = []
    for first, second in PAIRS:
        a, b = second_means[first], second_means[second]
        mean_a, mean_b = a.mean(axis=1), b.mean(axis=1)
        ratio = np.divide(mean_a, mean_b, out=np.full(len(a), np.nan), where=np.abs(mean_b) > 1e-12)
        cross.extend((correlation(a, b, np), mean_a - mean_b, ratio))
        for statistic in ('correlation_1s_means', 'mean_difference', 'mean_ratio'):
            name = f'{first}_{second}_{statistic}'
            extra_names.append(name)
            dependencies[name] = [first, second]
    baseline = np.column_stack(baseline)
    enhanced = np.column_stack((baseline, *extras, *cross))
    if baseline.shape[1] != 112 or enhanced.shape[1] != 340 or np.isinf(enhanced).any():
        raise ValueError('Unexpected feature contract')
    return enhanced, labels, names + extra_names, dependencies


def stress_features(training, test, names, dependencies, np):
    """Estimate perturbation scales on training only and preserve held-out row order."""
    scale = np.nanstd(training, axis=0)
    scale = np.where(np.isfinite(scale), scale, 0)
    generator = np.random.default_rng(STRESS['seed'])
    noise = test.copy() + generator.normal(size=test.shape) * scale * STRESS['noiseTrainStdFraction']
    dropped, drifted = test.copy(), test.copy()
    drop = [index for index, name in enumerate(names) if STRESS['dropChannel'] in dependencies[name]]
    drift = [index for index, name in enumerate(names) if STRESS['driftChannel'] in dependencies[name]]
    dropped[:, drop] = np.nan
    drifted[:, drift] += scale[drift] * STRESS['driftTrainStdFraction']
    return {'feature_noise': noise, 'PS1_missing': dropped, 'PS1_feature_drift': drifted}, scale


def metrics(truth, probabilities, np):
    probabilities = np.asarray(probabilities, dtype=float)
    if (probabilities.shape != (len(truth), len(CLASSES)) or not np.isfinite(probabilities).all()
            or np.any(probabilities < 0) or np.any(probabilities > 1)
            or not np.allclose(probabilities.sum(axis=1), 1, atol=1e-5)):
        raise ValueError('Expected normalized probabilities for classes 0, 1, 2')
    predictions = probabilities.argmax(axis=1)
    result = evaluate(truth, predictions)
    result['log_loss'] = float(-np.log(np.clip(probabilities[np.arange(len(truth)), truth], 1e-15, 1)).mean())
    result['recall_by_class'] = {str(label): float(np.mean(predictions[truth == label] == label)) for label in CLASSES}
    return result


def fit_options(engine, cpus, time_limit, memory_gb):
    cat = {**FIT, 'thread_count': cpus}
    if engine == 'catboost':
        return {**cat, 'early_stopping_rounds': 40}
    return {'hyperparameters': {
        'CAT': {'iterations': 500, 'depth': 5, 'learning_rate': 0.05, 'random_seed': 42},
        'RF': {'n_estimators': 300, 'max_depth': 12, 'min_samples_leaf': 2, 'random_state': 42}},
        'num_cpus': cpus, 'num_gpus': 0, 'time_limit': time_limit, 'memory_limit': memory_gb,
        'fit_strategy': 'sequential', 'num_bag_folds': 0, 'num_stack_levels': 0,
        'auto_stack': False, 'dynamic_stacking': False, 'refit_full': False,
        'fit_weighted_ensemble': True, 'calibrate_decision_threshold': False}


def fit_arm(engine, features, labels, names, directory, options, np):
    train_start, train_end = SPLITS['train']
    validation_start, validation_end = SPLITS['validation']
    if engine == 'catboost':
        from catboost import CatBoostClassifier, Pool
        options = dict(options)
        early_stopping = options.pop('early_stopping_rounds')
        model = CatBoostClassifier(**options)
        started = time.perf_counter()
        model.fit(Pool(features[train_start:train_end], labels[train_start:train_end], feature_names=names),
                  eval_set=Pool(features[validation_start:validation_end], labels[validation_start:validation_end], feature_names=names),
                  early_stopping_rounds=early_stopping, verbose=False)
        elapsed = time.perf_counter() - started
        model.save_model(str(directory / 'model.cbm'))
        if list(model.classes_) != list(CLASSES):
            raise ValueError('Unexpected CatBoost class ordering')
        return lambda table: model.predict_proba(table), {'fitSeconds': elapsed, 'trees': model.tree_count_}, lambda: None
    from autogluon.tabular import TabularPredictor
    from pandas import DataFrame
    training = DataFrame(features[train_start:train_end], columns=names)
    training['leakage'] = labels[train_start:train_end]
    validation = DataFrame(features[validation_start:validation_end], columns=names)
    validation['leakage'] = labels[validation_start:validation_end]
    predictor = TabularPredictor(label='leakage', problem_type='multiclass', eval_metric='log_loss',
                                 path=str(directory / 'predictor'), verbosity=1)
    started = time.perf_counter()
    predictor.fit(training, tuning_data=validation, **options)
    elapsed = time.perf_counter() - started
    candidates = [{'model': str(row['model']), 'validation_log_loss': -float(row['score_val'])}
                  for row in predictor.leaderboard(display=False).to_dict(orient='records')]
    return (lambda table: predictor.predict_proba(DataFrame(table, columns=names))[list(CLASSES)].to_numpy(),
            {'fitSeconds': elapsed, 'model': predictor.model_best, 'validationCandidates': candidates},
            lambda: predictor.save_space(remove_data=True))


def write_report(path, report):
    staged = path.with_suffix('.tmp')
    staged.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    staged.replace(path)


def peak_rss_bytes():
    """Cumulative high-water RSS for this process, including feature extraction."""
    try:
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(peak if sys.platform == 'darwin' else peak * 1024)
    except ImportError:
        return None


def export_datasets(output, features, labels, names, stress, report, np):
    directory = output / 'derived-datasets'
    directory.mkdir()
    files = {}
    cases = [(part, features[start:end], labels[start:end], start, 'uci_real_cycle', part)
             for part, (start, end) in SPLITS.items()]
    cases.extend((case, table, labels[1764:], 1764, 'synthetic_feature_perturbation_of_real_test', 'test_only')
                 for case, table in stress.items())
    for case, table, target, start, origin, split in cases:
        path = directory / f'{case}.csv'
        with path.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['cycle_number', 'split', 'origin', 'internal_pump_leakage', *names])
            for index, (values, label) in enumerate(zip(table, target)):
                writer.writerow([start + index + 1, split, origin, int(label),
                                 *('' if np.isnan(value) else float(value) for value in values)])
        files[path.name] = {'sha256': archive_digest(path), 'rows': len(table), 'origin': origin, 'split': split}
    provenance = {
        'artifactVersion': 1,
        'dataset': 'UCI Condition monitoring of hydraulic systems', 'doi': report['datasetDOI'],
        'creators': ['Nikolai Helwig', 'Eliseo Pignanelli', 'Andreas Schütze'],
        'source': 'https://archive.ics.uci.edu/dataset/447/condition+monitoring+of+hydraulic+systems',
        'license': 'CC BY 4.0', 'licenseURL': 'https://creativecommons.org/licenses/by/4.0/',
        'originalArchiveSha256': report['archiveSha256'], 'generationScriptSha256': report['scriptSha256'],
        'artifactPackagingScriptSha256': report.get('artifactPackagingScriptSha256', report['scriptSha256']),
        'featureNames': names, 'featureDependencies': report['features']['dependencies'],
        'metadataNotFeatures': ['cycle_number', 'split', 'origin', 'internal_pump_leakage'],
        'baselineFeatures': names[:112], 'stressProtocol': report['stressProtocol'],
        'labelProvenance': 'Original controlled-rig profile.txt column 3, unchanged by feature perturbations.',
        'testReuse': report['testReuse'], 'files': files,
    }
    write_report(directory / 'provenance.json', provenance)
    (directory / 'README.txt').write_text(
        'Cyber102 derived UCI hydraulic-rig datasets, version 1\n\n'
        'Source: Helwig, Pignanelli and Schütze; DOI 10.24432/C5CW21; CC BY 4.0.\n'
        'License: https://creativecommons.org/licenses/by/4.0/\n'
        'Changes: per-cycle sensor feature extraction and explicitly synthetic feature-space stress variants.\n\n'
        'train.csv: cycles 1–1323. validation.csv: 1324–1764. test.csv: 1765–2205.\n'
        'The three additional CSV files perturb the same 441 test rows. They are evaluation-only.\n'
        'feature_noise.csv: independent Gaussian noise, standard deviation 0.10 times training feature std.\n'
        'PS1_missing.csv: PS1 features and all cross-channel features involving PS1 are missing.\n'
        'PS1_feature_drift.csv: add 0.50 times training feature std to all features involving PS1.\n'
        'Noise seed: 20261113. Noise/drift scales are estimated only from train.csv rows.\n'
        'Do not append them to training or use them to tune models after viewing their scores.\n'
        'They simulate numerical noise, channel absence and feature drift, not new physical defects.\n'
        'Leakage labels remain those of the original controlled experiment.\n\n'
        'Use only featureNames from provenance.json as predictors; never cycle_number, split, origin, or label.\n'
        'All 340 features use one completed 60-second cycle; the first 112 reproduce the historical baseline.\n'
        'Empty feature cells mean undefined/missing, not zero. Metadata and label columns are separate.\n'
        'The historical final test cycles are reused, not a newly blinded test.\n'
        'The archived CSVs include the target and provenance; generic AutoML must receive only the named predictors.\n'
        'To reproduce four fixed experiments, install ml/requirements-autogluon.txt and run\n'
        'python -m ml.benchmark_uci_autogluon --archive <official-UCI-ZIP> --output <new-directory>\n'
        'This laboratory dataset does not establish field diagnostic accuracy or remaining service life.\n',
        encoding='utf-8')
    archive = output / 'cyber102-uci-hydraulic-features-v1.zip'
    with ZipFile(archive, 'w', compression=ZIP_DEFLATED) as bundle:
        for path in sorted(directory.iterdir()):
            bundle.write(path, path.name)
    return {'path': archive.name, 'sha256': archive_digest(archive), 'files': files}


def run(archive, output, *, cpus=4, time_limit=300, memory_gb=4):
    import numpy as np
    if output.exists():
        raise ValueError('Output must be a new directory')
    if cpus < 1 or time_limit < 1 or not 0 < memory_gb < float('inf'):
        raise ValueError('CPU, time and memory budgets must be positive and finite')
    started = time.perf_counter()
    features, labels, names, dependencies = load_features(archive, np)
    extraction_seconds = time.perf_counter() - started
    balances = {}
    for name, (start, end) in SPLITS.items():
        balance = counts(labels[start:end])
        if not all(balance.values()):
            raise ValueError(f'Missing class in {name}: {balance}')
        balances[name] = {'cycles': end - start, 'first_cycle': start + 1, 'last_cycle': end,
                          'class_counts': balance}
    stress, scale = stress_features(features[:1323], features[1764:], names, dependencies, np)
    report = {'schemaVersion': 1, 'completed': False, 'startedAt': datetime.now(timezone.utc).isoformat(),
              'purpose': 'Controlled laboratory internal pump leakage, not field validation',
              'runtimeReady': False, 'datasetDOI': '10.24432/C5CW21', 'license': 'CC BY 4.0',
              'archiveSha256': archive_digest(archive), 'scriptSha256': archive_digest(Path(__file__)),
              'label': 'profile.txt column 3: 0=no leakage, 1=weak, 2=severe',
              'split': balances, 'testReuse': 'Same final 441 cycles as the published historical CatBoost benchmark; '
              'not a new blind holdout. No selection or parameter changes using this test.',
              'features': {'baseline': names[:112], 'enhanced': names, 'dependencies': dependencies,
                           'extractionSeconds': extraction_seconds,
                           'scope': 'Within-cycle sensor features only. No other profile labels, stable flag, '
                                    'cycle index, neighboring cycles or virtual efficiency channels.'},
              'stressProtocol': {**STRESS, 'trainScaleSha256': hashlib.sha256(scale.tobytes()).hexdigest()},
              'versions': {'python': platform.python_version(), 'numpy': np.__version__,
                           'catboost': version('catboost'), 'autogluon.tabular': version('autogluon.tabular')},
              'registeredArms': [{'name': name, 'engine': engine, 'features': group,
                                 'options': fit_options(engine, cpus, time_limit, memory_gb)}
                                for name, engine, group in ARMS],
              'selection': 'AutoGluon uses validation log_loss only; four arms registered before fit. '
                           'CatBoost early stopping=40, AutoGluon native early stopping.',
              'arms': {}}
    output.mkdir(parents=True)
    (output / 'benchmark_uci_autogluon.executed.py').write_bytes(Path(__file__).read_bytes())
    write_report(output / 'preregistered.json', report)
    np.savez_compressed(output / 'features.npz', enhanced=features, leakage=labels,
                        names=np.array(names), train_stress_scale=scale)
    report['derivedDatasets'] = export_datasets(output, features, labels, names, stress, report, np)
    train_counts = np.array([np.sum(labels[:1323] == label) for label in CLASSES])
    prevalence = train_counts / train_counts.sum()
    report['trainPrevalenceBaseline'] = {
        part: metrics(labels[start:end], np.tile(prevalence, (end - start, 1)), np)
        for part, (start, end) in SPLITS.items() if part != 'train'}
    try:
        for arm in report['registeredArms']:
            name, engine, group = arm['name'], arm['engine'], arm['features']
            width = 112 if group == 'baseline' else features.shape[1]
            directory = output / name
            directory.mkdir()
            print(json.dumps({'event': 'training', 'arm': name, 'features': width}), flush=True)
            predict, details, cleanup = fit_arm(engine, features[:, :width], labels, names[:width], directory, arm['options'], np)
            scored = {}
            for part, (start, end) in SPLITS.items():
                if part != 'train':
                    scored[part] = metrics(labels[start:end], predict(features[start:end, :width]), np)
            robustness = {case: metrics(labels[1764:], predict(table[:, :width]), np) for case, table in stress.items()}
            cleanup()
            report['arms'][name] = {'featureCount': width, **details, 'metrics': scored, 'stress': robustness,
                                    'cumulativeProcessPeakRSSBytes': peak_rss_bytes()}
            write_report(output / 'report.json', report)
            print(json.dumps({'event': 'completed_arm', 'arm': name, 'test': scored['test'],
                              'fitSeconds': details['fitSeconds']}), flush=True)
            del predict, cleanup
        report['completed'] = True
        report['bestArmByValidationLogLoss'] = min(report['arms'], key=lambda name: report['arms'][name]['metrics']['validation']['log_loss'])
        report['bestArmScope'] = 'Validation ranking of the four fixed experiments; no model is approved for production.'
    except BaseException as error:
        report['error'] = {'type': type(error).__name__, 'message': str(error)[:1000]}
        raise
    finally:
        report['finishedAt'] = datetime.now(timezone.utc).isoformat()
        report['processPeakRSSBytes'] = peak_rss_bytes()
        report['memoryScope'] = 'Whole-process cumulative peak RSS, including feature extraction and all arms; not isolated model memory.'
        write_report(output / 'report.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--cpus', type=int, default=4)
    parser.add_argument('--time-limit', type=int, default=300, help='per AutoGluon arm, seconds')
    parser.add_argument('--memory-gb', type=float, default=4, help='soft AutoGluon memory budget')
    args = parser.parse_args(argv)
    try:
        run(args.archive, args.output, cpus=args.cpus, time_limit=args.time_limit, memory_gb=args.memory_gb)
    except (ImportError, OSError, ValueError) as error:
        parser.error(str(error))


if __name__ == '__main__':
    main()
