"""Fixed CPU feature/weight ablation; the historical test is diagnostic, not blind."""
import argparse
from datetime import datetime, timezone
from importlib.metadata import version
import json
from pathlib import Path
import platform
import shutil
import time
from zipfile import ZipFile

try:
    import resource
except ImportError:  # Windows needs a separate process-RSS measurement implementation.
    resource = None

from .benchmark_uci import CLASSES, CYCLE_COUNT, FIT, SENSORS, archive_digest
from .benchmark_uci_autogluon import PAIRS, SPLITS, metrics

SEEDS = (42, 43, 44)
GROUPS = ('full340', 'shape53', 'phase389')
WEIGHTS = ('None', 'Balanced')
EXCLUDED_RATIO = 'PS3_PS4_mean_ratio'
PAPER = 'https://jsss.copernicus.org/articles/7/359/2018/jsss-7-359-2018.pdf'


def shape_names():
    selected = [f'{sensor}_{statistic}' for sensor in SENSORS
                for statistic in ('skewness', 'excess_kurtosis', 'lag1_correlation')]
    selected += [f'{a}_{b}_correlation_1s_means' for a, b in PAIRS]
    selected += [f'{a}_{b}_mean_ratio' for a, b in PAIRS if f'{a}_{b}_mean_ratio' != EXCLUDED_RATIO]
    assert len(selected) == len(set(selected)) == 53
    return selected


def phase_features(values, np):
    """One cycle per row; fixed 5-second means/std divided by that cycle's RMS."""
    values = np.asarray(values, dtype=np.float64)
    if values.ndim != 2 or values.shape[1] == 0 or values.shape[1] % 12 or not np.isfinite(values).all():
        raise ValueError('Expected finite cycles divisible into twelve equal windows')
    blocks = values.reshape(len(values), 12, values.shape[1] // 12)
    rms = np.sqrt(np.mean(values * values, axis=1))
    denominator = np.maximum(rms, 1e-12)[:, None]
    result = np.stack((blocks.mean(axis=2) / denominator, blocks.std(axis=2) / denominator), axis=2)
    return result.reshape(len(values), 24)


def load_inputs(archive, previous, np):
    old_report = json.loads((previous / 'report.json').read_text(encoding='utf-8'))
    if archive_digest(archive) != old_report['archiveSha256']:
        raise ValueError('Raw archive differs from the previous verified benchmark')
    with np.load(previous / 'features.npz', allow_pickle=False) as stored:
        full = stored['enhanced'].copy()
        labels = stored['leakage'].copy()
        names = stored['names'].tolist()
    if full.shape != (CYCLE_COUNT, 340) or names != old_report['features']['enhanced'] or np.isinf(full).any():
        raise ValueError('Cached feature contract differs from the previous benchmark')
    shape = shape_names()
    shape_table = full[:, [names.index(name) for name in shape]]
    normalized, normalized_names = [], []
    with ZipFile(archive) as source:
        with source.open('profile.txt') as handle:
            profile = np.loadtxt(handle, dtype=np.int64)
        if profile.shape != (CYCLE_COUNT, 5) or not np.array_equal(labels, profile[:, 2]):
            raise ValueError('Labels do not match the raw profile leakage column')
        for sensor, sample_count in SENSORS.items():
            with source.open(sensor + '.txt') as handle:
                values = np.loadtxt(handle, dtype=np.float64)
            if values.shape != (CYCLE_COUNT, sample_count):
                raise ValueError('Unexpected raw sensor dimensions: ' + sensor)
            normalized.append(phase_features(values, np))
            normalized_names += [f'{sensor}_window{block:02d}_{stat}_over_cycle_rms'
                                 for block in range(12) for stat in ('mean', 'std')]
            del values
    tables = {'full340': full, 'shape53': shape_table,
              'phase389': np.column_stack((shape_table, *normalized))}
    group_names = {'full340': names, 'shape53': shape, 'phase389': shape + normalized_names}
    for group in GROUPS:
        if tables[group].shape != (CYCLE_COUNT, len(group_names[group])):
            raise ValueError('Feature shape mismatch')
    return tables, group_names, labels, profile, old_report


def severe_strata(labels, probabilities, profile, np):
    predictions = probabilities.argmax(axis=1)
    result = {}
    for field, column in (('cooler', 0), ('valve', 1), ('accumulator', 3), ('stability', 4)):
        groups = {}
        for value in sorted(set(profile[:, column].tolist())):
            mask = (profile[:, column] == value) & (labels == 2)
            support = int(mask.sum())
            found = int(np.sum(predictions[mask] == 2))
            groups[str(value)] = {'severeSupport': support, 'severeFound': found,
                                 'severeRecall': found / support if support else None}
        result[field] = groups
    return result


def validation_selection(records, np):
    """No test argument is accepted; seed summaries determine the feature/weight arm."""
    summaries = []
    for group in GROUPS:
        for weighting in WEIGHTS:
            rows = [row for row in records if row['featureGroup'] == group and row['weighting'] == weighting]
            if sorted(row['seed'] for row in rows) != list(SEEDS):
                raise ValueError('Every arm must contain the same three registered seeds')
            summaries.append({'featureGroup': group, 'weighting': weighting,
                'features': rows[0]['features'],
                'meanValidationBalancedAccuracy': float(np.mean([r['validation']['balanced_accuracy'] for r in rows])),
                'meanValidationLogLoss': float(np.mean([r['validation']['log_loss'] for r in rows]))})
    ranking = sorted(summaries, key=lambda row: (-row['meanValidationBalancedAccuracy'],
        row['meanValidationLogLoss'], row['features'], row['weighting'] != 'None', row['featureGroup']))
    return {'rule': 'Highest mean validation balanced accuracy over seeds42/43/44; exact ties: lower mean '
                    'unweighted log loss, fewer features, no weighting, lexical group name. No best-seed selection.',
            'ranking': ranking, 'winner': ranking[0], 'representativeSeed': 42,
            'testPredictionsAvailableAtSelection': False}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def evaluate_split(model, table, labels, profile, partition, np):
    start, end = SPLITS[partition]
    probabilities = model.predict_proba(table[start:end], thread_count=4)
    return metrics(labels[start:end], probabilities, np), severe_strata(
        labels[start:end], probabilities, profile[start:end], np), probabilities


def run(archive, previous, output):
    import numpy as np
    from catboost import CatBoostClassifier, Pool
    from threadpoolctl import threadpool_limits

    if output.exists():
        raise ValueError('Output must be a new directory')
    output.mkdir(parents=True)
    started = time.perf_counter()
    with threadpool_limits(limits=4):
        tables, names, labels, profile, old_report = load_inputs(archive, previous, np)
    counts = [int(np.sum(labels[:1323] == value)) for value in CLASSES]
    options = {**FIT, 'thread_count': 4, 'use_best_model': True}
    protocol = {'schemaVersion': 1, 'registeredAt': datetime.now(timezone.utc).isoformat(),
        'purpose': 'Fixed feature and loss-weight ablation under the existing operating-condition shift.',
        'archiveSha256': archive_digest(archive), 'previousReportSha256': archive_digest(previous / 'report.json'),
        'cachedFeaturesSha256': archive_digest(previous / 'features.npz'),
        'scriptSha256': archive_digest(Path(__file__)),
        'split': {name: {'startInclusive': start, 'endExclusive': end} for name, (start, end) in SPLITS.items()},
        'features': names, 'seeds': list(SEEDS), 'weightings': list(WEIGHTS), 'fitCount': 18,
        'fitOptions': options, 'earlyStoppingRounds': 40,
        'validationMetricRule': {'None': 'MultiClass', 'Balanced': 'MultiClass:use_weights=false'},
        'balancedClassWeightsFromTrain': [max(counts) / count for count in counts],
        'shapeRule': '14 physical channels x skewness/excess_kurtosis/lag1_correlation, six pair correlations, '
                     'five pair mean ratios; omit PS3_PS4_mean_ratio because zero meanPS4 makes it undefined.',
        'phaseRule': 'Append 14 x 12 x 2 features: equal5s block mean and population std divided by '
                     'max(current-cycle RMS,1e-12). Zero channels yield zero. No neighboring cycles, fitted scaling or profile predictors.',
        'phaseScope': 'Generic within-cycle normalization, not physical efficiency or exactly the paper operational phase boundaries.',
        'paper': {'url': PAPER, 'sectionsPDFPagesZeroBased': [3, 5]},
        'selectionRule': 'Mean validation balanced accuracy across three fixed seeds; exact ties use lower mean '
                         'validation log loss, fewer features, None weighting, then lexical feature group. Representative seed42 fixed.',
        'testPolicy': 'Historical test has already been inspected. No test prediction from this experiment until all18fits '
                      'finish and selection.json is written. One final diagnostic test pass; no adaptive rounds afterwards.',
        'limits': ['Profile cooler/valve/accumulator/stability only used for audit strata, never model input.',
                   'Seeds measure fitting sensitivity, not independent operating regimes.',
                   'Only one severe validation block has cooler100 and it is confounded with accumulator130.',
                   'Neither source photographs nor organizer live snapshots are inputs to this laboratory-cycle model.',
                   'No production runtime integration or model replacement.'],
        'versions': {name: version(name) for name in ('numpy', 'catboost')},
        'featureExtractionSeconds': time.perf_counter() - started}
    write_json(output / 'preregistered.json', protocol)
    shutil.copy2(__file__, output / 'benchmark_uci_shift.executed.py')
    np.savez_compressed(output / 'features.npz', **tables, leakage=labels,
                        **{group + '_names': np.array(values) for group, values in names.items()})
    report = {'schemaVersion': 1, 'completed': False, 'protocol': protocol,
              'preregisteredSha256': archive_digest(output / 'preregistered.json'),
              'runtimeReady': False, 'records': [], 'productionChanged': False}
    models = {}
    try:
        with threadpool_limits(limits=4):
            for group in GROUPS:
                for weighting in WEIGHTS:
                    for seed in SEEDS:
                        identifier = f'{group}-{weighting.lower()}-seed{seed}'
                        # CatBoost rejects an explicit use_weights flag when no weights exist.
                        # Both branches evaluate the same unweighted validation loss.
                        settings = {**options, 'random_seed': seed, 'auto_class_weights': weighting,
                                    'eval_metric': 'MultiClass' if weighting == 'None' else 'MultiClass:use_weights=false'}
                        model = CatBoostClassifier(**settings)
                        fit_started = time.perf_counter()
                        model.fit(Pool(tables[group][:1323], labels[:1323], feature_names=names[group]),
                            eval_set=Pool(tables[group][1323:1764], labels[1323:1764], feature_names=names[group]),
                            early_stopping_rounds=40, verbose=False)
                        fit_seconds = time.perf_counter() - fit_started
                        directory = output / identifier
                        directory.mkdir()
                        model.save_model(str(directory / 'model.cbm'))
                        if model.classes_.tolist() != list(CLASSES):
                            raise ValueError('Unexpected class order')
                        validation, strata, probabilities = evaluate_split(model, tables[group], labels, profile, 'validation', np)
                        np.save(directory / 'validation-probabilities.npy', probabilities)
                        record = {'id': identifier, 'featureGroup': group, 'weighting': weighting, 'seed': seed,
                            'features': len(names[group]), 'trees': model.tree_count_, 'fitSeconds': fit_seconds,
                            'modelSha256': archive_digest(directory / 'model.cbm'),
                            'validation': validation, 'validationSevereStrata': strata}
                        report['records'].append(record)
                        models[identifier] = model
                        write_json(output / 'validation-progress.json', report)
                        print(json.dumps({'fit': len(report['records']), 'total': 18, 'id': identifier,
                                          'seconds': round(fit_seconds, 3), 'valBalancedAccuracy': validation['balanced_accuracy']}), flush=True)
            report['selection'] = validation_selection(report['records'], np)
            report['selection']['frozenAt'] = datetime.now(timezone.utc).isoformat()
            write_json(output / 'selection.json', report['selection'])
            report['selectionSha256'] = archive_digest(output / 'selection.json')
            print(json.dumps({'selectionFrozen': report['selection']['winner'], 'testPredictionsStarted': False}), flush=True)
            report['testEvaluationStartedAt'] = datetime.now(timezone.utc).isoformat()
            for record in report['records']:
                model, table = models[record['id']], tables[record['featureGroup']]
                test, strata, probabilities = evaluate_split(model, table, labels, profile, 'test', np)
                np.save(output / record['id'] / 'test-probabilities.npy', probabilities)
                record.update(test=test, testSevereStrata=strata)
                loaded = CatBoostClassifier()
                loaded.load_model(str(output / record['id'] / 'model.cbm'))
                reloaded = loaded.predict_proba(table[1764:], thread_count=4)
                record['serializationExact'] = bool(np.array_equal(probabilities, reloaded))
                if not record['serializationExact']:
                    raise ValueError('Reloaded model changed predictions')
            baseline = next(row for row in report['records'] if row['id'] == 'full340-none-seed42')
            report['historicalBaselineReproduced'] = (baseline['validation'] == old_report['arms']['catboost_enhanced']['metrics']['validation']
                and baseline['test'] == old_report['arms']['catboost_enhanced']['metrics']['test'])
        report['completed'] = True
    finally:
        report['finishedAt'] = datetime.now(timezone.utc).isoformat()
        report['totalSeconds'] = time.perf_counter() - started
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss if resource else None
        report['processPeakRSSBytes'] = (int(rss if platform.system() == 'Darwin' else rss * 1024)
                                         if rss is not None else None)
        report['memoryScope'] = ('Peak resident memory of this whole process, including raw feature extraction and all18models; '
                                'not individual model memory. Not measured where the resource module is unavailable.')
        report['host'] = {'system': platform.system(), 'machine': platform.machine(), 'python': platform.python_version()}
        write_json(output / 'report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True, type=Path)
    parser.add_argument('--previous-run', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    run(args.archive, args.previous_run, args.output)


if __name__ == '__main__':
    main()
