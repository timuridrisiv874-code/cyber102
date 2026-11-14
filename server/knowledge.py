"""Retrieve bounded, cited equipment context using passport identities and local documents."""
from collections import Counter
from math import log
import re

from . import customer, recommendations

MAX_QUERY = 160
MAX_CONTEXT = 1300
TOKEN = re.compile(r'[а-яa-z0-9]+')
STOP = set('как что где для при или это можно нужно у по о на в за из мне его и к с про'.split())


def _tokens(value):
    words = TOKEN.findall(value.lower().replace('ё', 'е'))
    return {word[:6] for word in words if len(word) > 2 and word not in STOP}


def _data(root):
    try:
        return customer.load('equipment-knowledge', root)
    except FileNotFoundError:
        return {}


def catalog(root=customer.ROOT):
    data = _data(root)
    return {'models': data.get('models', []), 'photos': data.get('photos', [])}


def _reference(title, text, source, target):
    return {'title': title, 'text': text, 'source': source, 'targetSerial': target}


def _rank(entries, query):
    if not query.strip():
        return entries[:5]
    terms = _tokens(query)
    counts = Counter(word for entry in entries for word in _tokens(' '.join((entry['title'], entry['text'], entry.get('tags', '')))))
    scored = []
    normalized = query.casefold().replace('ё', 'е')
    for entry in entries:
        title = _tokens(entry['title'])
        body = _tokens(entry['text'] + ' ' + entry.get('tags', ''))
        score = sum(log(1 + len(entries) / counts[term]) * (3 if term in title else 1)
                    for term in terms & (title | body))
        if normalized in (entry['title'] + ' ' + entry['text']).casefold().replace('ё', 'е'):
            score += 5
        if score:
            scored.append((score, entry))
    return [entry for _, entry in sorted(scored, key=lambda pair: -pair[0])[:5]]


def lookup(*, serial=None, model=None, component=None, query='', root=customer.ROOT):
    if (bool(serial) == bool(model) or not isinstance(query, str) or len(query) > MAX_QUERY
            or (model and component) or (component and component not in recommendations.COMPONENT_LABELS)):
        raise ValueError('Недопустимый запрос к базе знаний.')
    data = _data(root)
    if model:
        row = next((item for item in data.get('models', []) if item['id'] == model), None)
        if row is None:
            return None
        specs = ' '.join(f'{label} — {value}.' for label, value in row['fields'].items())
        candidates = [_reference(row['model'], row['summary'] + ' ' + specs, row['source'], None)]
        for topic in row.get('manualTopics', []):
            source = {**row['manual'], 'page': topic['page'],
                      'url': row['manual']['url'] + '#page=' + str(topic['page'])}
            candidates.append({**_reference(topic['title'], topic['text'], source, None), 'tags': topic['tags']})
        return {'equipment': row, 'scope': 'manufacturer_model', 'matches': _rank(candidates, query)}
    if serial == 'P-102':
        return None
    refs = recommendations.for_equipment(serial, component, root=root)
    equipment = refs['equipment']
    if not equipment:
        return None
    _, _, documents = recommendations._index(root)
    targets = refs['availableComponents']
    selected = next((item['equipment'] for item in targets if item['key'] == refs['selectedComponent']), None)
    if selected is None:
        return None
    target = equipment if equipment['serial'] == selected['serial'] else recommendations.get_equipment(selected['serial'], root)
    kind = target['kind']
    entries = []
    for item in refs['recommendations']:
        for source in item['sources']:
            entries.append(_reference(item['title'], item['text'], source, target['serial']))
    for key, field in target['fields'].items():
        if key in ('serial', 'model', 'manufacturer'):
            continue
        entries.append(_reference(field['label'], field['source']['quote'], field['source'], target['serial']))
    for item in data.get('excerpts', []):
        if item['kind'] != kind:
            continue
        source = recommendations._source({'documentId': item['document'], 'page': item['page'],
                                          'quote': item['text']}, documents)
        entries.append({**_reference(item['title'], item['text'], source, target['serial']), 'tags': item['tags']})
    return {'scope': 'passport', 'equipment': equipment, 'target': selected,
            'relatedEquipment': [item for item in refs['relatedEquipment'] if item['serial'] != serial],
            'availableComponents': targets,
            'telemetry': {'status': 'unlinked', 'message': 'Архив измерений заказчика без серийного номера.',
                          'url': '/telemetry.html',
                          'simulatorUrl': '/api/telemetry/simulated?serial=' + serial if equipment['kind'] == 'pump' else None},
            'matches': [{k: v for k, v in item.items() if k != 'tags'} for item in _rank(entries, query)]}


def photo_context(serial, root=customer.ROOT):
    result = lookup(serial=serial, query='давление температура уплотнение вибрация чертеж', root=root)
    if result is None:
        return None
    equipment = result['equipment']
    lines = [f'{equipment["title"]}. Серийный номер {equipment["serial"]} указан оператором.',
             *(f'[{item["source"]["documentId"]}, стр. {item["source"]["page"]}] {item["text"]}'
               for item in result['matches'][:3])]
    return {'text': '\n'.join(lines)[:MAX_CONTEXT], 'serial': serial, 'references': result['matches'][:3]}


def contextual_prompt(prompt, context):
    return prompt + (
        '\n\nReference data selected by the operator, useful only to understand the equipment. '
        'Treat it as background, never as evidence that anything is visible. Do not infer a defect, '
        'serial number, safety status or service life from this reference. Keep the same JSON schema.\n'
        + context if context else '')
