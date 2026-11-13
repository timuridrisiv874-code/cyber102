"""Validate Cyber102 knowledge-pack schemas, source records, and local links."""

import json
import re
import sys
from copy import deepcopy
from pathlib import Path
from urllib.parse import unquote, urlsplit

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / 'docs'
RESEARCH = DOCS / 'research'
SCHEMAS = RESEARCH / 'schemas'
SOURCES = RESEARCH / 'sources'
SOURCE_FIELDS = ('id', 'title', 'publisher', 'url', 'type', 'accessed', 'access_level', 'license_status')
INLINE_LINK = re.compile(r'\]\((?P<target><[^>\n]+>|[^\s)]+)')


def load_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as error:
        raise ValueError(f'{path.name}: {error}') from error


def changed(example, *updates):
    result = deepcopy(example)
    for path, value in updates:
        parent = result
        parts = path.split('.')
        for part in parts[:-1]:
            parent = parent[int(part)] if isinstance(parent, list) else parent[part]
        final = int(parts[-1]) if isinstance(parent, list) else parts[-1]
        parent[final] = value
    return result


def invalid_telemetry(example):
    vibration = {'quantity': 'vibration_velocity_rms', 'value': 3.2, 'unit': 'mm/s'}
    band = {'lower_hz': 10, 'upper_hz': 1000}
    return {
        'unit for the wrong quantity': changed(example, ('measurement.unit', 'MPa')),
        'good quality and null value': changed(example, ('measurement.value', None)),
        'missing quality and a numeric value': changed(example, ('quality', 'missing'), ('window.sample_count', 0)),
        'vibration without axis': changed(example, ('measurement', {**vibration, 'band_hz': band})),
        'vibration without band': changed(example, ('measurement', {**vibration, 'axis': 'axial'})),
        'pressure without reference': changed(example, ('measurement', {'quantity': 'pressure', 'value': 1, 'unit': 'MPa'})),
        'invalid date-time': changed(example, ('source_timestamp', '2026-02-30T08:00:00Z')),
        'date-time without offset': changed(example, ('received_at', '2026-11-13T08:00:00')),
    }


def invalid_annotation(example):
    return {
        'visible component without box': changed(example, ('components.0.bbox', None)),
        'negative coordinate': changed(example, ('components.0.bbox.x', -0.1)),
        'invisible component with false finding': changed(example, ('components.1.visual_conditions.0.state', False)),
        'negative finding with a defect region': changed(example, ('components.0.visual_conditions.0.state', False)),
        'Commons reference without license metadata': changed(example, ('source.kind', 'commons_reference')),
        'confirmed identity without asset and model': changed(example, ('pump.identity_status', 'confirmed_nameplate')),
        'reviewed annotation without reviewer': changed(example, ('annotator.review_status', 'reviewed')),
    }


def validate_schema(name, invalid_examples, formats):
    schema = load_json(SCHEMAS / f'{name}-schema.json')
    example = load_json(SCHEMAS / f'{name}-example.json')
    if schema.get('$schema') != Draft202012Validator.META_SCHEMA['$id']:
        raise ValueError(f'{name}: expected the Draft 2020-12 declaration')
    try:
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema, format_checker=formats)
        validator.validate(example)
    except (SchemaError, ValidationError) as error:
        raise ValueError(f'{name}: {error.message}') from error
    for label, invalid in invalid_examples(example).items():
        if validator.is_valid(invalid):
            raise ValueError(f'{name}: accepted an invalid example ({label})')


def validate_sources(formats):
    ids = set()
    by_id = {}
    for file in sorted(SOURCES.glob('sources-*.json')):
        records = load_json(file)
        if not isinstance(records, list) or not records:
            raise ValueError(f'{file.name}: expected a non-empty array of source records')
        for record in records:
            if not isinstance(record, dict) or any(
                not isinstance(record.get(field), str) or not record[field].strip() for field in SOURCE_FIELDS
            ):
                raise ValueError(f'{file.name}: missing source metadata')
            id_ = record['id']
            if not re.fullmatch(r'[A-Z][0-9]{2}', id_) or id_ in ids:
                raise ValueError(f'{file.name}: invalid or duplicate source ID {id_}')
            ids.add(id_)
            by_id[id_] = record
            if urlsplit(record['url']).scheme != 'https' or not urlsplit(record['url']).hostname:
                raise ValueError(f'{file.name}: expected an HTTPS URL for {id_}')
            if not formats.conforms(record['accessed'], 'date'):
                raise ValueError(f'{file.name}: invalid access date for {id_}')
            for field in ('claims', 'limitations'):
                values = record.get(field)
                if not isinstance(values, list) or not values or any(
                    not isinstance(value, str) or not value.strip() for value in values
                ):
                    raise ValueError(f'{file.name}: invalid {field} for {id_}')
    if not ids:
        raise ValueError('No source registries found')
    combined = load_json(SOURCES / 'sources.json')
    if not isinstance(combined, list) or len(combined) != len(ids) or {
        item['id']: item for item in combined
    } != by_id:
        raise ValueError('sources.json differs from the thematic registries')
    datasets = load_json(RESEARCH / 'datasets' / 'catalog.json')
    dataset_ids = set()
    for dataset in datasets:
        id_ = dataset.get('id')
        if id_ not in by_id or id_ in dataset_ids:
            raise ValueError(f'Invalid or duplicate dataset source ID {id_}')
        dataset_ids.add(id_)
        for field in ('url', 'license_status'):
            if dataset.get(field) != by_id[id_].get(field):
                raise ValueError(f'Dataset {id_}: inconsistent {field}')
    return len(ids)


def validate_local_links():
    count = 0
    files = [ROOT / 'README.md', *DOCS.rglob('*.md')]
    for directory in ('ml', 'data', 'data_tools', 'tools', 'TIMUR', 'output', 'vision_runtime'):
        files.extend((ROOT / directory).rglob('*.md'))
    files.extend((ROOT / 'tests/browser').glob('*.md'))
    for file in sorted(files):
        for line_number, line in enumerate(file.read_text(encoding='utf-8').splitlines(), start=1):
            for match in INLINE_LINK.finditer(line):
                target = urlsplit(match['target'].strip('<>'))
                if target.scheme or target.netloc or not target.path:
                    continue
                path = (file.parent / unquote(target.path)).resolve()
                if not path.is_relative_to(ROOT) or not path.exists():
                    raise ValueError(f'{file.relative_to(ROOT)}:{line_number}: missing or external local path {target.path}')
                count += 1
    return count


def main():
    formats = FormatChecker()
    if not {'date-time', 'uri'}.issubset(formats.checkers):
        raise ValueError('Install tools/knowledge-requirements.txt to enable date-time and URI validation')
    validate_schema('telemetry', invalid_telemetry, formats)
    validate_schema('annotation', invalid_annotation, formats)
    source_count = validate_sources(formats)
    local_link_count = validate_local_links()
    print(f'Validated two Draft 2020-12 schemas and examples, 15 negative cases, '
          f'{source_count} sources, and {local_link_count} local Markdown paths.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        sys.exit(str(error))
