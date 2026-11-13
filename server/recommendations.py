"""Manufacturer references scoped to an indexed serial and an explicit component.

These references do not validate the demonstration diagnostic rules for the AMS
equipment. PDF bytes are hash-checked by customer.document when opened; this
module validates catalog identity, page bounds and curated citation metadata.
"""
from copy import deepcopy
import re

from server import customer
from server.diagnostics import RULE_METADATA


COMPONENT_LABELS = {
    'pump': 'Насос',
    'seal': 'Уплотнение вала',
    'foundation': 'Крепления насоса',
    'shaft_joints': 'Соединение вала и шарниры',
    'bearing': 'Подшипниковый узел',
    'gearbox': 'Редуктор',
    'motor': 'Электродвигатель',
    'temperature_transmitter': 'Термопреобразователь',
}
KINDS = {'pump', 'gearbox', 'motor', 'temperature_transmitter'}
PUMP_MODEL = 'AMS PCP60S12/3 01 Ex B1 HNBR DTS'
GEARBOX_MODEL = 'WXRF87-AM160-5.30-M1-Ø300'
GROUP_LIMIT = ('Номер группы отражает комплектность переданных PDF. '
               'Монтажная связь компонентов и замены на площадке не подтверждены.')
RULE_LIMIT = ('Диагностические правила демонстрационного P-102, их пороги и интервалы '
              '4000, 8000 и 16000 ч для этого оборудования не подтверждены. '
              'Ни диагноз, ни остаточный ресурс из паспортных ссылок не вычисляются.')

# Stable identities select reviewed catalog entries, not client text or URLs.
REQUIREMENTS = {
    'routine_inspection': {
        'title': 'Текущий технический осмотр',
        'sources': [('operation-manual', 7)],
        'components': {'pump', 'foundation'},
        'rules': {'scheduled_maintenance', 'foundation_looseness'},
    },
    'mechanical_seal': {
        'title': 'Обслуживание торцевого уплотнения',
        'sources': [('operation-manual', 8)],
        'components': {'seal'},
        'rules': {'seal_leak'},
    },
    'gearbox_oil': {
        'title': 'Масло редуктора',
        'sources': [('lubricants', 1)],
        'components': {'gearbox'},
        'rules': {'oil_degradation', 'scheduled_maintenance'},
    },
    'shaft_joint_lubricants': {
        'title': 'Смазочные материалы соединения вала и шарниров',
        'sources': [('lubricants', 1), ('lubricants', 1)],
        'components': {'shaft_joints'},
        'rules': {'scheduled_maintenance'},
    },
}


def _serial(value):
    if not isinstance(value, str) or not value or len(value) > 100 or value != value.strip():
        raise ValueError('Invalid equipment serial')
    return value


def _index(root):
    catalog = customer.load('equipment-passports', root)
    rows = catalog.get('equipment')
    documents = customer.load('documents', root).get('documents')
    if not isinstance(rows, list) or not isinstance(documents, list):
        raise ValueError('Invalid manufacturer reference catalog')
    by_id = {}
    for doc in documents:
        if not isinstance(doc, dict) or not isinstance(doc.get('id'), str):
            raise ValueError('Invalid document index')
        if doc['id'] in by_id:
            raise ValueError('Duplicate document identity')
        by_id[doc['id']] = doc
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Invalid equipment catalog')
        serial = _serial(row.get('serial'))
        if serial in seen:
            raise ValueError('Duplicate equipment serial')
        seen.add(serial)
        if (row.get('kind') not in KINDS or type(row.get('groupNumber')) is not int
                or row['groupNumber'] < 1):
            raise ValueError('Invalid equipment identity')
    return catalog, rows, by_id


def _source(reference, documents):
    if not isinstance(reference, dict):
        raise ValueError('Missing citation')
    identifier, page, quote = (reference.get('documentId'), reference.get('page'),
                               reference.get('quote'))
    if not isinstance(identifier, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}', identifier):
        raise ValueError('Invalid citation document')
    doc = documents.get(identifier)
    if not doc:
        raise ValueError('Unknown citation document')
    digest, count = doc.get('sha256'), doc.get('pageCount')
    if not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest):
        raise ValueError('Invalid document digest')
    if type(count) is not int or count < 1 or type(page) is not int or not 1 <= page <= count:
        raise ValueError('Invalid citation page')
    pages = doc.get('pages')
    if not isinstance(pages, list) or sum(
            isinstance(item, dict) and type(item.get('page')) is int and item['page'] == page
            for item in pages) != 1:
        raise ValueError('Citation page is not indexed')
    if not isinstance(quote, str) or not quote.strip() or len(quote) > 4000:
        raise ValueError('Missing citation quote')
    if not isinstance(doc.get('title'), str) or not doc['title'].strip():
        raise ValueError('Missing document title')
    return {'documentId': identifier, 'page': page, 'quote': quote,
            'title': doc['title'], 'documentSha256': digest,
            'url': f'/api/customer/documents/{identifier}/pdf#page={page}',
            'validation': 'catalog_reference_validated'}


def _equipment(row, catalog, documents):
    """Expose only fields with valid citations; serial/model evidence is required."""
    raw_fields = row.get('fields')
    if not isinstance(raw_fields, dict):
        raise ValueError('Missing equipment fields')
    fields, sources = {}, []
    for key, field in raw_fields.items():
        if not isinstance(field, dict):
            continue
        try:
            source = _source(field.get('source'), documents)
        except ValueError:
            continue
        fields[key] = {'value': deepcopy(field.get('value')), 'unit': field.get('unit'),
                       'label': field.get('label', key), 'source': source}
        sources.append({**source, 'field': key, 'label': field.get('label', key)})
    for key in ('serial', 'model'):
        if key not in fields or fields[key]['value'] != row.get(key):
            raise ValueError('Equipment identity has no valid passport citation')
    return {key: deepcopy(row.get(key)) for key in
            ('id', 'serial', 'kind', 'model', 'title', 'manufacturer', 'groupNumber')} | {
        'source': 'organizer_provided',
        'equipmentType': catalog.get('equipmentType') if row['kind'] == 'pump' else row['kind'],
        'typeLabel': row.get('typeLabel', COMPONENT_LABELS[row['kind']]),
        'applicableDiagnostics': 'not_validated',
        'fields': fields, 'sources': sources,
    }


def registry(root=customer.ROOT):
    """Return the supplied equipment registry; no demo identities or thresholds."""
    catalog, rows, documents = _index(root)
    return [_equipment(row, catalog, documents) for row in rows]


def get_equipment(serial, root=customer.ROOT):
    """Resolve an exact serial; unknown/demo serials return None."""
    serial = _serial(serial)
    if serial == 'P-102':
        return None
    catalog, rows, documents = _index(root)
    row = next((row for row in rows if row['serial'] == serial), None)
    return _equipment(row, catalog, documents) if row else None


def _brief(equipment):
    return {key: equipment[key] for key in ('id', 'serial', 'kind', 'model', 'title', 'groupNumber')}


def for_equipment(serial, component=None, rule_ids=None, root=customer.ROOT):
    """Return reference-only maintenance guidance for a selected asset/node.

    ``rule_ids`` filters references by diagnostic topic, never proves that a rule
    applies to this model. An omitted/empty list returns the node's references.
    Cross-equipment component selection is available only from a pump and only
    within its document group; it does not assert physical installation.
    """
    serial = _serial(serial)
    if component is not None and (not isinstance(component, str) or component not in COMPONENT_LABELS):
        raise ValueError('Unknown component')
    if rule_ids is not None and (not isinstance(rule_ids, (list, tuple))
                                 or len(rule_ids) > 12 or any(not isinstance(x, str) for x in rule_ids)):
        raise ValueError('Invalid rule identifiers')
    known_rules = {row['id'] for row in RULE_METADATA}
    if any(rule not in known_rules for rule in (rule_ids or [])):
        raise ValueError('Unknown rule identifier')
    result = {'equipment': None, 'selectedComponent': component,
              'applicability': 'unknown_equipment', 'recommendations': [],
              'availableComponents': [], 'relatedEquipment': [], 'limitations': []}
    if serial == 'P-102':
        result.update(applicability='unverified', limitations=[
            'Для демонстрационного P-102 нет подтверждённого источника изготовителя. '
            'Паспорта АМС и масло редуктора 3,7 л к нему не привязаны.'])
        return result
    catalog, rows, documents = _index(root)
    row = next((item for item in rows if item['serial'] == serial), None)
    if row is None:
        result['limitations'].append('Серийный номер отсутствует в реестре паспортов; рекомендации изготовителя не найдены.')
        return result
    try:
        equipment = _equipment(row, catalog, documents)
    except ValueError:
        result.update(applicability='unverified', limitations=[
            'Не удалось подтвердить серийный номер и модель ссылками на паспорт. Рекомендации скрыты.'])
        return result
    result.update(equipment=equipment, applicability='reference_only')
    result['limitations'] = [GROUP_LIMIT, RULE_LIMIT]
    related = []
    for item in rows:
        if item['groupNumber'] == row['groupNumber']:
            try:
                related.append(_equipment(item, catalog, documents))
            except ValueError:
                result['limitations'].append('Часть компонентов группы не имеет проверяемых ссылок на паспорт.')
    result['relatedEquipment'] = [_brief(item) for item in related]
    targets = {equipment['kind']: equipment}
    if row['kind'] == 'pump':
        for kind in ('gearbox', 'motor', 'temperature_transmitter'):
            matches = [item for item in related if item['kind'] == kind]
            if len(matches) == 1:
                targets[kind] = matches[0]
        targets.update({key: equipment for key in ('seal', 'foundation', 'shaft_joints', 'bearing')})
    elif row['kind'] in ('gearbox', 'motor'):
        targets['bearing'] = equipment
    result['availableComponents'] = [
        {'key': key, 'label': COMPONENT_LABELS[key], 'equipment': _brief(target)}
        for key, target in targets.items()]
    selected = component or row['kind']
    result['selectedComponent'] = selected
    target = targets.get(selected)
    if target is None:
        result['limitations'].append('Выбранный узел не подтверждён в этой паспортной группе. Рекомендации скрыты.')
        return result

    requirements = catalog.get('requirements', [])
    if not isinstance(requirements, list):
        raise ValueError('Invalid requirement catalog')
    for identifier, spec in REQUIREMENTS.items():
        # A seal-related finding may select the seal reference from pump view.
        topic_match = selected in spec['components'] or (
            selected == 'pump' and identifier == 'mechanical_seal' and 'seal_leak' in (rule_ids or []))
        if not topic_match or (rule_ids and not spec['rules'].intersection(rule_ids)):
            continue
        if (identifier == 'gearbox_oil' and (target['kind'] != 'gearbox' or target['model'] != GEARBOX_MODEL)):
            continue
        if identifier != 'gearbox_oil' and (target['kind'] != 'pump' or target['model'] != PUMP_MODEL):
            continue
        matches = [item for item in requirements if isinstance(item, dict) and item.get('title') == spec['title']]
        try:
            if len(matches) != 1:
                raise ValueError('Requirement missing or ambiguous')
            requirement = matches[0]
            raw_sources = requirement.get('sources')
            if not isinstance(raw_sources, list) or len(raw_sources) != len(spec['sources']):
                raise ValueError('Missing requirement citations')
            sources = [_source(source, documents) for source in raw_sources]
            if [(source['documentId'], source['page']) for source in sources] != spec['sources']:
                raise ValueError('Unexpected requirement citation')
            if any(not isinstance(requirement.get(key), str) or not requirement[key].strip()
                   for key in ('text', 'appliesTo')):
                raise ValueError('Missing requirement text')
        except ValueError:
            result['limitations'].append(f'«{spec["title"]}»: источник или страница не подтверждены; рекомендация скрыта.')
            continue
        conditions = ['Справочная выдержка; применимость к текущей неисправности требует проверки.']
        if identifier == 'mechanical_seal':
            conditions.append('Применять только после подтверждения одинарного торцевого уплотнения; тип установленного уплотнения в реестре не подтверждён.')
        if identifier == 'gearbox_oil':
            conditions.append('Марка и объём относятся только к этому исполнению редуктора, не к подшипникам насоса или электродвигателя.')
        if target['serial'] != serial:
            conditions.append(GROUP_LIMIT)
        recommendation_component = 'seal' if identifier == 'mechanical_seal' else selected
        result['recommendations'].append({
            'id': identifier, 'title': requirement['title'], 'text': requirement['text'],
            'appliesTo': f'{target["serial"]} · {COMPONENT_LABELS[recommendation_component]} · {requirement["appliesTo"]}',
            'target': _brief(target), 'component': recommendation_component,
            'applicability': 'reference_only', 'conditions': conditions, 'sources': sources,
        })
    if not result['recommendations']:
        result['limitations'].append('Для выбранного узла и темы нет подтверждённой инструкции в проиндексированных выдержках.')
    return result
