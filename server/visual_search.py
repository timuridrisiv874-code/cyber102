"""Photo catalogue and lightweight proxy to the isolated, CPU-only visual search worker."""
import hashlib
import json
import logging
import math
import re
import time
from pathlib import Path
from urllib.error import URLError, HTTPError
from urllib.request import Request, ProxyHandler, build_opener

from . import customer, knowledge, vision

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = 'http://127.0.0.1:11437'
MODEL = 'timm/vit_small_patch14_dinov2.lvd142m'
REVISION = '4610ca143709d58a633b6397a74412c2c3842454'
WEIGHTS_SHA256 = '04d27f3400d059fc0cfd7d17dd1909a75bf3ea8fb3eeb48b97cb99e57ee20081'
PIPELINE = 'dinov2-small-pad224-v1'


class SearchUnavailable(Exception):
    pass


class SearchBusy(SearchUnavailable):
    pass


def _request(path, image=None):
    request = Request(ENDPOINT + path, data=image,
                      headers={'Content-Type': 'image/jpeg'} if image is not None else {})
    try:
        with build_opener(ProxyHandler({}), vision.NoRedirects()).open(request, timeout=12 if image else 1) as response:
            body = response.read(128 * 1024 + 1)
            if len(body) > 128 * 1024:
                raise ValueError('Oversized search response')
            result = json.loads(body)
            if not isinstance(result, dict) or result.get('model') != MODEL or result.get('revision') != REVISION:
                raise ValueError('Unexpected search model')
            return result
    except HTTPError as error:
        if error.code == 503:
            raise SearchBusy() from error
        raise SearchUnavailable() from error
    except (URLError, OSError, ValueError) as error:
        raise SearchUnavailable() from error


def search(image):
    return _request('/search', image)


def health():
    return _request('/health')


def _context_candidates(result, root, passport):
    """Resolve worker IDs to local, cited text; never accept a caller's URLs or excerpts."""
    data = customer.load('inspection-knowledge', root)
    curated = json.loads((Path(root) / 'visual-catalog.json').read_text(encoding='utf-8'))
    rows = {row['id']: row for row in curated['entries']}
    for item in customer.load('materials', root).get('images', []):
        rows[item['id']] = {'id': item['id'], 'title': item['categoryTitle'] + ' · фото из подборки',
                            'scope': item['category']}
    selected = knowledge.recommendations.get_equipment(passport['serial'], root) if passport else None
    candidates = []
    for match in result.get('matches', [])[:3]:
        if not isinstance(match, dict) or not isinstance(match.get('id'), str):
            continue
        score = match.get('score')
        if type(score) not in (int, float) or not math.isfinite(score) or not .45 <= score <= 1.0001:
            continue
        row = rows.get(match['id'])
        if row is None:
            continue
        scope = row['scope']
        kind = 'pump' if scope in ('pump', 'progressive_cavity', 'centrifugal') else scope
        if selected:
            if selected['kind'] != 'pump' and kind != selected['kind']:
                continue
            if kind == 'pump' and scope not in ('pump', selected['equipmentType']):
                continue
        # An unlabelled pump photograph does not establish its internal pump type.
        scopes = ({selected['equipmentType'], 'seal', 'bearing', 'pressure'} if selected and scope == 'pump'
                  else {'seal', 'bearing', 'pressure'} if scope == 'pump' else {scope})
        refs = []
        if row.get('modelId'):
            model = knowledge.lookup(model=row['modelId'], root=root)
            # Use procedural excerpts, not the lookalike model's numerical specifications.
            refs = [item for item in model['matches'] if item['applicability'] == 'manufacturer_model'
                    and '#page=' in item['source']['url']]
            refs = [{**item, 'source': {**item['source'], 'publisher': model['equipment']['manufacturer']}}
                    for item in refs]
        refs.extend(knowledge._public_entries(data, scopes))
        candidates.append(({'id': row['id'], 'title': row['title'], 'score': score, 'scope': scope}, refs))
    return candidates


def photo_context(image, passport=None, *, root=customer.ROOT):
    """Combine a selected passport with bounded, explicitly tentative visual references."""
    result = None
    for attempt in range(3):
        try:
            result = search(image)
            break
        except SearchBusy:
            if attempt < 2:
                time.sleep(.1 * (attempt + 1))
        except SearchUnavailable:
            break
    if result is None:
        return passport
    try:
        candidates = _context_candidates(result, root, passport)
    except (OSError, ValueError, KeyError, TypeError):
        logging.warning('Visual reference text unavailable; retaining selected passport context')
        return passport
    automatic = []
    for match, refs in candidates:
        for ref in refs:
            automatic.append({**ref, 'selection': 'visual_similarity', 'candidateId': match['id'],
                              'candidateTitle': match['title'], 'targetSerial': None})
    if not automatic:
        return passport
    lines = [passport['text'].split('\n', 1)[0]] if passport else []
    lines.append('Visual matches are unconfirmed hypotheses. Operator-selected passport takes precedence. '
                 'Never transfer lookalike specifications or infer defects from these references.')
    manual = passport.get('references', []) if passport else []
    references, used, matched_ids = [], set(), set()

    def add(item):
        source = item['source']
        if source['url'] in used or len(references) >= 3:
            return False
        label = (f'{source["documentId"]}, стр. {source["page"]}' if source.get('documentId')
                 else source.get('publisher', source.get('title', 'Справка')))
        prefix = f'По сходству с «{item["candidateTitle"]}»: ' if item.get('selection') == 'visual_similarity' else ''
        line = f'{prefix}[{label}] {item["title"]}: {item["text"]}'
        if len('\n'.join([*lines, line])) > knowledge.MAX_CONTEXT:
            return False
        lines.append(line)
        references.append(item)
        used.add(source['url'])
        if item.get('candidateId'):
            matched_ids.add(item['candidateId'])
        return True

    for item in manual[:1]:
        add(item)
    for item in automatic:
        if add(item):
            break
    for item in manual[1:]:
        add(item)
    for item in automatic:
        add(item)
    if not matched_ids:
        return passport
    return {'text': '\n'.join(lines), 'serial': passport['serial'] if passport else None,
            'references': references, 'selection': 'passport_and_visual' if passport else 'visual_similarity',
            'visualMatches': [match for match, _ in candidates if match['id'] in matched_ids],
            'retrieval': {'model': MODEL, 'revision': REVISION, 'indexVersion': result.get('indexVersion')}}


def catalogue(customer_root=customer.ROOT, web_root=ROOT / 'web'):
    """Keep catalogue/technical references separate from serial-number identity."""
    customer_root, web_root = Path(customer_root), Path(web_root)
    reference = customer.load('inspection-knowledge', customer_root)
    curated = json.loads((customer_root / 'visual-catalog.json').read_text(encoding='utf-8'))
    entries = []

    def references(scopes, model=None):
        if model:
            matches = knowledge.lookup(model=model, root=customer_root)['matches']
        else:
            matches = list(knowledge._public_entries(reference, scopes))
        return [{'title': row['title'], 'url': row['source']['url'],
                 'applicability': row['applicability']} for row in matches[:3]]

    for row in curated['entries']:
        if not re.fullmatch(r'[a-z0-9-]+\.jpg', row['file']):
            raise ValueError('Invalid catalogue photo')
        path = web_root / 'equipment-photos' / row['file']
        if not path.is_file():
            continue
        docs = references({row['scope']}, row.get('modelId'))
        if row.get('sourceUrl'):
            docs.insert(0, {'title': 'Каталог серии', 'url': row['sourceUrl'], 'applicability': 'series_reference'})
        entries.append({'id': row['id'], 'title': row['title'], 'imageUrl': '/equipment-photos/' + row['file'],
                        'scope': row['scope'], 'source': 'catalogue', 'references': docs[:3], '_path': path})
    for row in customer.load('materials', customer_root).get('images', []):
        if not re.fullmatch(r'[a-f0-9]{64}', row['id']):
            raise ValueError('Invalid customer photo')
        path = customer_root / 'previews' / (row['id'] + '.jpg')
        if not path.is_file():
            continue
        scopes = {'pump': {'progressive_cavity', 'centrifugal'}, 'motor': {'motor'},
                  'valve': {'valve'}, 'pipe': {'pipe'}}.get(row['category'], set())
        entries.append({'id': row['id'], 'title': row['categoryTitle'] + ' · фото из подборки',
                        'imageUrl': '/api/customer/images/' + row['id'], 'scope': row['category'],
                        'source': 'customer_example', 'references': references(scopes), '_path': path})
    if not entries:
        raise ValueError('Visual catalogue is empty')
    # Text, links and pixels all affect the persisted index version.
    digest = hashlib.sha256((PIPELINE + WEIGHTS_SHA256).encode())
    for row in entries:
        digest.update(json.dumps({k: v for k, v in row.items() if k != '_path'}, sort_keys=True).encode())
        digest.update(hashlib.sha256(row['_path'].read_bytes()).digest())
    return entries, digest.hexdigest()
