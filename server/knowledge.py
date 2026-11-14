"""Retrieve bounded, cited equipment context using passport identities and local documents."""
from collections import Counter
from math import log
import re
from urllib.parse import urlsplit

from . import customer, recommendations

MAX_QUERY = 160
MAX_CONTEXT = 1300
TOKEN = re.compile(r'[а-яa-z0-9]+')
STOP = set('как что где для при или это можно нужно его про если когда почему какой какая насос агрегат оборудование проверка'.split())
ALIASES = {
    'сальни': 'уплотн', 'подтек': 'утечка', 'потекл': 'утечка', 'утечки': 'утечка', 'течь': 'утечка',
    'расцен': 'соосно', 'несоос': 'соосно', 'центро': 'соосно', 'центри': 'соосно',
    'ржавчи': 'корроз', 'ржавый': 'корроз', 'мутное': 'мутнос', 'мутный': 'мутнос',
    'всас': 'всасыв', 'входно': 'всасыв', 'перегр': 'нагрев', 'греетс': 'нагрев',
    'импелл': 'колесо', 'колеса': 'колесо', 'колесу': 'колесо', 'крыльч': 'колесо',
    'клеммы': 'клемма', 'клеммн': 'клемма', 'огражд': 'кожух', 'нарабо': 'моточа',
}


def _tokens(value):
    words = TOKEN.findall(value.lower().replace('ё', 'е'))
    return {ALIASES.get(word[:6], word[:6]) for word in words if len(word) > 2 and word not in STOP}


def _data(root, name='equipment-knowledge'):
    try:
        return customer.load(name, root)
    except FileNotFoundError:
        return {}


def catalog(root=customer.ROOT):
    data = _data(root)
    return {'models': data.get('models', []), 'photos': data.get('photos', []),
            'sections': _data(root, 'inspection-knowledge').get('sections', [])}


def _reference(title, text, source, target, applicability='passport'):
    return {'title': title, 'text': text, 'source': source, 'targetSerial': target,
            'applicability': applicability}


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
        overlap = terms & (title | body)
        if not overlap:
            continue
        score = sum(log(1 + len(entries) / counts[term]) * (2.5 if term in title else 1)
                    for term in sorted(overlap))
        score += len(overlap)
        if normalized in (entry['title'] + ' ' + entry['text']).casefold().replace('ё', 'е'):
            score += 5
        if entry['applicability'] in ('passport', 'manufacturer_model'):
            score += 3
        scored.append((score, entry))
    return [entry for _, entry in sorted(scored, key=lambda pair: -pair[0])[:5]]


def _public_entries(data, scopes):
    for item in data.get('entries', []):
        if scopes.isdisjoint(item['scopes']):
            continue
        source = data['sources'][item['source']]
        address = urlsplit(source['url'])
        if address.scheme != 'https' or not address.hostname or address.username or address.password:
            raise ValueError('Invalid external knowledge source')
        yield {**_reference(item['title'], item['text'], source, None, 'technical_reference'), 'tags': item['tags']}


def _matches(entries, query):
    return [{key: value for key, value in item.items() if key != 'tags'} for item in _rank(entries, query)]


def lookup(*, serial=None, model=None, section=None, component=None, query='', root=customer.ROOT):
    if (sum(bool(value) for value in (serial, model, section)) != 1
            or not isinstance(query, str) or len(query) > MAX_QUERY
            or (component and (not serial or component not in recommendations.COMPONENT_LABELS))):
        raise ValueError('Недопустимый запрос к базе знаний.')
    data = _data(root)
    reference_data = _data(root, 'inspection-knowledge')
    if section:
        row = next((item for item in reference_data.get('sections', []) if item['id'] == section), None)
        if row is None:
            return None
        return {'scope': 'technical_reference', 'section': row,
                'matches': _matches(list(_public_entries(reference_data, {section})), query)}
    if model:
        row = next((item for item in data.get('models', []) if item['id'] == model), None)
        if row is None:
            return None
        specs = ' '.join(f'{label} — {value}.' for label, value in row['fields'].items())
        candidates = [_reference(row['model'], row['summary'] + ' ' + specs, row['source'], None, 'manufacturer_model')]
        for topic in row.get('manualTopics', []):
            source = {**row['manual'], 'page': topic['page'],
                      'url': row['manual']['url'] + '#page=' + str(topic['page'])}
            candidates.append({**_reference(topic['title'], topic['text'], source, None, 'manufacturer_model'),
                               'tags': topic['tags']})
        candidates.extend(_public_entries(reference_data, {row['equipmentType'], 'seal', 'shaft_joints', 'bearing', 'pressure'}))
        return {'equipment': row, 'scope': 'manufacturer_model', 'matches': _matches(candidates, query)}
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
    selected_key = refs['selectedComponent']
    scopes = {selected_key}
    if selected_key == 'pump':
        scopes.update({equipment['equipmentType'], 'seal', 'foundation', 'shaft_joints', 'bearing', 'pressure'})
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
        if item['kind'] != kind or (selected_key != kind and selected_key not in item.get('components', [])):
            continue
        source = recommendations._source({'documentId': item['document'], 'page': item['page'],
                                          'quote': item['text']}, documents)
        entries.append({**_reference(item['title'], item['text'], source, target['serial']), 'tags': item['tags']})
    entries.extend(_public_entries(reference_data, scopes))
    return {'scope': 'passport', 'equipment': equipment, 'target': selected,
            'relatedEquipment': [item for item in refs['relatedEquipment'] if item['serial'] != serial],
            'availableComponents': targets,
            'telemetry': {'status': 'unlinked', 'message': 'Архив измерений заказчика без серийного номера.',
                          'url': '/telemetry.html',
                          'simulatorUrl': '/api/telemetry/simulated?serial=' + serial if equipment['kind'] == 'pump' else None},
            'matches': _matches(entries, query)}


def photo_context(serial, root=customer.ROOT):
    equipment = recommendations.get_equipment(serial, root)
    if equipment is None:
        return None
    topics = {
        'pump': 'установочный чертеж вход выход герметичность крепления торцевое уплотнение',
        'gearbox': 'масло редуктора объем смазка',
        'motor': 'точки измерения вибрации двигателя',
        'temperature_transmitter': 'сухого хода контроль обмотки преобразователь сигнал',
    }
    background_topics = {
        'progressive_cavity': 'ротор статор',
        'gearbox': 'уровень масла сапун манжета',
        'motor': 'охлаждение вентиляция',
        'temperature_transmitter': 'запаздывание гильза',
    }
    result = lookup(serial=serial, query=topics[equipment['kind']], root=root)
    if result is None:
        return None
    identity = [equipment['title'], f'Серийный номер {serial} указан оператором.']
    if equipment['equipmentType'] == 'progressive_cavity':
        identity.append('Тип агрегата: одновинтовой эксцентриковый насос. Внутренние ротор и статор скрыты корпусом.')
    identity.append('Привязанных показаний датчиков нет.')
    lines = [' '.join(identity)]
    references = []
    used_pages = set()
    candidates = [item for item in result['matches'] if item['applicability'] == 'passport']
    scope = equipment['equipmentType']
    reference_data = _data(root, 'inspection-knowledge')
    background = _rank(list(_public_entries(reference_data, {scope})), background_topics.get(scope, ''))
    candidates = [*candidates[:2], *background[:1], *candidates[2:]]
    for item in candidates:
        source = item['source']
        if item['applicability'] == 'passport':
            label = f'{source["documentId"]}, стр. {source["page"]}'
        else:
            label = f'{source["publisher"]}, справка по типу оборудования'
        line = f'[{label}] {item["title"]}: {item["text"]}'
        if source['url'] in used_pages or len('\n'.join([*lines, line])) > MAX_CONTEXT:
            continue
        lines.append(line)
        references.append(item)
        used_pages.add(source['url'])
        if len(references) == 3:
            break
    return {'text': '\n'.join(lines), 'serial': serial, 'references': references}


def contextual_prompt(prompt, context):
    return prompt + (
        '\n\nReference data selected by the operator, useful only to understand the equipment. '
        'Treat it as background, never as evidence that anything is visible. Do not infer a defect, '
        'serial number, safety status or service life from this reference. Keep the same JSON schema.\n'
        + context if context else '')
