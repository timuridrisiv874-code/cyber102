"""Collect a bounded set of licensed Wikimedia Commons images for review."""

import argparse
import hashlib
import json
import re
import sys
import time
import warnings
from collections import deque
from datetime import datetime, timezone
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


ROOT = Path(__file__).resolve().parent.parent
CATALOG = Path(__file__).with_name('commons_sources.json')
API_HOST = 'commons.wikimedia.org'
MEDIA_HOST = 'upload.wikimedia.org'
DEFAULT_USER_AGENT = 'Cyber102PhotoCollector/1.0 (https://github.com/timuridrisiv874-code/cyber102)'
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_PIXELS = 25_000_000
MIN_SIDE = 180
IMAGE_TYPES = {'image/jpeg': ('.jpg', 'JPEG'), 'image/png': ('.png', 'PNG')}
METADATA_FIELDS = 'LicenseShortName|LicenseUrl|Artist|Attribution|AttributionRequired|Restrictions|ImageDescription'
CC_LICENSE = re.compile(r'/licenses/(by|by-sa)/([1-4]\.0|2\.5)(/[a-z]{2,5})?/?')
PD_LICENSES = {'cc0': 'https://creativecommons.org/publicdomain/zero/1.0/',
               'public domain': 'https://creativecommons.org/publicdomain/mark/1.0/'}


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def plain_text(value):
    parser = _PlainText()
    parser.feed(value or '')
    return ' '.join(' '.join(parser.parts).split())


def https_url(url, host):
    if not isinstance(url, str):
        raise ValueError('отсутствует адрес источника')
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.netloc != host:
        raise ValueError(f'ожидается HTTPS-адрес {host}')
    return parts._replace(fragment='').geturl()


class _SameHostRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        https_url(newurl, urlsplit(req.full_url).hostname)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def read_remote(url, *, timeout, limit, user_agent, opener=None):
    opener = opener or build_opener(_SameHostRedirect())
    request = Request(url, headers={'User-Agent': user_agent})
    with opener.open(request, timeout=timeout) as response:
        https_url(response.geturl(), urlsplit(url).hostname)
        declared_size = response.headers.get('Content-Length')
        if declared_size and int(declared_size) > limit:
            raise ValueError('файл превышает допустимый размер')
        content = response.read(limit + 1)
        if len(content) > limit:
            raise ValueError('файл превышает допустимый размер')
        return content, response.headers.get_content_type()


def api_query(parameters, *, timeout, user_agent):
    query = {
        'action': 'query', 'format': 'json', 'formatversion': 2, 'maxlag': 5,
        'prop': 'imageinfo', 'iiprop': 'url|mime|size|extmetadata',
        'iiextmetadatafilter': METADATA_FIELDS, **parameters,
    }
    body, content_type = read_remote(
        f'https://{API_HOST}/w/api.php?{urlencode(query)}',
        timeout=timeout, limit=MAX_METADATA_BYTES, user_agent=user_agent,
    )
    if content_type != 'application/json':
        raise ValueError('Wikimedia вернула ответ в неожиданном формате')
    data = json.loads(body)
    if 'error' in data:
        raise ValueError(f'ошибка API Wikimedia: {data["error"].get("code", "unknown")}')
    return data.get('query', {}).get('pages', [])


def candidate(page, subject, model_match):
    title = page.get('title', '')
    imageinfo = page.get('imageinfo', [])
    if not imageinfo or not title.startswith('File:'):
        raise ValueError('нет записи о файле')
    info = imageinfo[0]
    mime = info.get('mime')
    if mime not in IMAGE_TYPES or not 0 < info.get('size', 0) <= MAX_IMAGE_BYTES:
        raise ValueError('разрешены JPEG и PNG до 12 МиБ')
    width, height = info.get('width', 0), info.get('height', 0)
    if min(width, height) < MIN_SIDE or width * height > MAX_PIXELS:
        raise ValueError('неподходящие размеры изображения')
    metadata = info.get('extmetadata', {})

    def field(name):
        return plain_text(metadata.get(name, {}).get('value'))

    if field('Restrictions'):
        raise ValueError('у файла указаны дополнительные ограничения')
    license_name = field('LicenseShortName')
    if license_name.lower() in PD_LICENSES:
        license_url = PD_LICENSES[license_name.lower()]
    else:
        license_url = field('LicenseUrl')
        if license_url.startswith('http://creativecommons.org/'):
            license_url = 'https://' + license_url.removeprefix('http://')
        license_url = https_url(license_url, 'creativecommons.org')
        match = CC_LICENSE.fullmatch(urlsplit(license_url).path)
        if not match or not license_name.upper().startswith(f'CC {match[1].upper()} {match[2]}'):
            raise ValueError('нет проверяемой лицензии CC BY, CC BY-SA, CC0 или Public domain')

    creator = field('Artist')
    attribution = field('Attribution') or creator
    if license_name.lower() not in PD_LICENSES and not attribution:
        raise ValueError('не указан автор для атрибуции')

    original = https_url(info.get('url'), MEDIA_HOST)
    download_url = urlsplit(original)._replace(query='').geturl()
    return {
        'title': title, 'subject_hint': subject, 'model_match': model_match,
        'source_url': https_url(info.get('descriptionurl'), API_HOST), 'download_url': download_url,
        'license': license_name, 'license_url': license_url, 'creator': creator,
        'attribution': attribution, 'mime': mime, 'width': width, 'height': height,
    }


def verify_image(content, mime, expected_width, expected_height):
    from PIL import Image

    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as image:
                if image.format != IMAGE_TYPES[mime][1] or image.size != (expected_width, expected_height):
                    raise ValueError('размеры или формат файла расходятся с метаданными')
                image.verify()
            with Image.open(BytesIO(content)) as image:
                image.load()
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as error:
        raise ValueError('изображение превышает допустимое разрешение') from error


def _atomic_write(path, content):
    with NamedTemporaryFile(dir=path.parent, prefix='.collection-', delete=False) as temp:
        temp_path = Path(temp.name)
    try:
        temp_path.write_bytes(content)
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


def save_candidate(candidate_info, output, records, *, timeout, user_agent):
    if any(record['download_url'] == candidate_info['download_url'] for record in records):
        return False
    time.sleep(1)
    content, content_type = read_remote(
        candidate_info['download_url'], timeout=timeout, limit=MAX_IMAGE_BYTES, user_agent=user_agent,
    )
    if content_type != candidate_info['mime']:
        raise ValueError('тип ответа расходится с метаданными')
    verify_image(content, content_type, candidate_info['width'], candidate_info['height'])
    digest = hashlib.sha256(content).hexdigest()
    if any(record['sha256'] == digest for record in records):
        return False
    path = output / 'unlabelled' / f'{digest}{IMAGE_TYPES[content_type][0]}'
    _atomic_write(path, content)
    records.append({
        **candidate_info, 'local_file': path.relative_to(output).as_posix(), 'sha256': digest,
        'bytes': len(content), 'retrieved_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'review_status': 'unlabelled',
    })
    _atomic_write(output / 'manifest.json', (json.dumps(records, ensure_ascii=False, indent=2) + '\n').encode())
    return True


def fetch_catalog(catalog, *, timeout, user_agent):
    options = {'timeout': timeout, 'user_agent': user_agent}
    for query in catalog['target_queries']:
        for page in api_query({'generator': 'search', 'gsrsearch': f'{query} filetype:bitmap',
                               'gsrnamespace': 6, 'gsrlimit': 5}, **options):
            info = (page.get('imageinfo') or [{}])[0]
            description = plain_text(info.get('extmetadata', {}).get('ImageDescription', {}).get('value'))
            if re.search(r'(?<!\d)160[\s‐‑–—-]*1126(?!\d)', f'{page.get("title", "")} {description}'):
                yield page, 'pump', 'model_number_mentioned'

    subjects = {entry['title']: entry['subject'] for entry in catalog['references']}
    pages = api_query({'titles': '|'.join(subjects)}, **options)
    by_title = {page['title']: page for page in pages}
    pools = {}
    for title, subject in subjects.items():
        if title in by_title:
            pools.setdefault(subject, deque()).append(by_title[title])
    # Favor pumps while sampling components before spending the full limit on pumps.
    if 'pump' in pools:
        for _ in range(min(7, len(pools['pump']))):
            yield pools['pump'].popleft(), 'pump', 'reference_only'
    while any(pools.values()):
        for subject, pages in pools.items():
            if pages:
                yield pages.popleft(), subject, 'reference_only'


def collect(output, *, count, timeout, user_agent):
    (output / 'unlabelled').mkdir(parents=True, exist_ok=True)
    manifest = output / 'manifest.json'
    records = json.loads(manifest.read_text(encoding='utf-8')) if manifest.is_file() else []
    catalog = json.loads(CATALOG.read_text(encoding='utf-8'))
    if len(records) >= count:
        return 0, len(records), 0

    added = skipped = 0
    for page, subject, model_match in fetch_catalog(catalog, timeout=timeout, user_agent=user_agent):
        try:
            info = candidate(page, subject, model_match)
            if save_candidate(info, output, records, timeout=timeout, user_agent=user_agent):
                added += 1
        except HTTPError as error:
            if error.code in (429, 503):
                raise
            skipped += 1
            print(f'{page.get("title", "Файл")}: HTTP {error.code}', file=sys.stderr)
        except (OSError, ValueError) as error:
            skipped += 1
            print(f'{page.get("title", "Файл")}: {error}', file=sys.stderr)
        if len(records) >= count:
            break
    return added, len(records), skipped


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'data' / 'raw')
    parser.add_argument('--count', type=int, choices=range(1, 41), default=25, metavar='1–40')
    parser.add_argument('--timeout', type=int, choices=range(1, 31), default=15, metavar='1–30')
    parser.add_argument('--user-agent', default=DEFAULT_USER_AGENT)
    args = parser.parse_args()
    try:
        __import__('PIL')
    except ImportError:
        parser.exit(1, 'Установите зависимости из data_tools/requirements.txt.\n')
    try:
        added, total, skipped = collect(args.output, count=args.count, timeout=args.timeout, user_agent=args.user_agent)
    except HTTPError as error:
        retry_after = error.headers.get('Retry-After')
        wait = f' Retry-After: {retry_after}.' if retry_after else ''
        parser.exit(1, f'Сбор прерван: HTTP {error.code}.{wait} Уже скачанные файлы сохранены.\n')
    except (OSError, ValueError) as error:
        parser.exit(1, f'Сбор прерван: {error}. Уже скачанные файлы сохранены.\n')
    print(f'Добавлено: {added}. Всего: {total}. Отклонено: {skipped}. Манифест: {args.output / "manifest.json"}')


if __name__ == '__main__':
    main()
