import argparse
import hashlib
import json
import re
import sqlite3
import threading
from contextlib import closing
from functools import partial, wraps
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from .demo_data import PASSPORT, SCENARIOS
from . import history
from . import vision
from . import inspections, customer, recommendations, assessment, video_review, analysis_queue


ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / 'web'
MAX_OBSERVATIONS_BYTES = 32 * 1024


def connect(database):
    connection = sqlite3.connect(database, timeout=10)
    connection.execute('PRAGMA foreign_keys = ON')
    return connection


def analyze_photo_job(payload):
    return {**vision.analyze(payload['image'], backend=payload['backend'], profile=payload['profile']),
            'media': payload['media']}


def save_photo_job(payload, result):
    if result.get('status') in ('defect', 'clear'):
        with closing(connect(payload['database'])) as connection, connection:
            return {**result, **inspections.store_analysis(connection, payload['image'], result)}
    return result


def analyze_video_job(payload):
    return video_review._analyze_prepared(payload)


def bounded_analysis_upload(method):
    @wraps(method)
    def bounded(self):
        if not self.server.analysis_upload_slots.acquire(blocking=False):
            self.close_connection = True
            self.send_json({'error': 'Сервер принимает другие изображения. Повторите через несколько секунд.',
                            'code': 'analysis_upload_busy', 'retryAfterSeconds': 2}, 503,
                           headers={'Retry-After': '2'})
            return
        try:
            return method(self)
        finally:
            self.server.analysis_upload_slots.release()
    return bounded


def initialize(database, customer_root=None):
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(database)) as connection, connection:
        connection.execute('PRAGMA journal_mode = WAL')
        connection.executescript('''
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS pumps (
                serial TEXT PRIMARY KEY NOT NULL,
                passport_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS inspections (
                id TEXT NOT NULL,
                serial TEXT NOT NULL REFERENCES pumps(serial),
                label TEXT NOT NULL,
                telemetry_json TEXT NOT NULL,
                vision_json TEXT NOT NULL,
                zones_json TEXT NOT NULL,
                source TEXT NOT NULL,
                captured_at TEXT,
                PRIMARY KEY (serial, id)
            );
        ''')
        serial = PASSPORT['serial']
        connection.execute('INSERT OR IGNORE INTO pumps VALUES (?, ?)', (serial, json.dumps(PASSPORT)))
        connection.executemany('''
            INSERT OR IGNORE INTO inspections
                (id, serial, label, telemetry_json, vision_json, zones_json, source)
                VALUES (?, ?, ?, ?, ?, ?, 'demo')
        ''', ((case['id'], serial, case['label'], json.dumps(case['telemetry']),
               json.dumps(case['vision']), json.dumps(case['zones'])) for case in SCENARIOS))
        history.initialize(connection)
        inspections.initialize(connection)
        if customer_root is not None:
            for equipment in recommendations.registry(root=customer_root):
                if equipment['kind'] != 'pump':
                    continue
                passport = {**equipment, 'name': equipment['title'], 'thresholds': {},
                            'diagnosticsValidated': False,
                            'serviceLife': 'По паспорту и истории обслуживания',
                            'nextService': 'Сверить с паспортом выбранного узла'}
                connection.execute('INSERT OR IGNORE INTO pumps VALUES (?, ?)',
                                   (passport['serial'], json.dumps(passport, ensure_ascii=False)))


class BoundedHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(24)
        self.analysis_upload_slots = threading.BoundedSemaphore(2)
        super().__init__(*args, **kwargs)
        self.analysis_queue = analysis_queue.AnalysisQueue()

    def server_close(self):
        if hasattr(self, 'analysis_queue'):
            self.analysis_queue.close()
        super().server_close()

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        request.settimeout(15)
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, *args):
        try:
            super().process_request_thread(*args)
        finally:
            self.slots.release()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, database, public_url, vision_enabled=False, vision_backend='ollama', customer_root=None, **kwargs):
        from server import customer
        self.customer_root = customer_root or customer.ROOT
        self.database = database
        self.public_url = public_url
        self.vision_enabled = vision_enabled
        self.vision_backend = vision_backend
        super().__init__(*args, directory=str(WEB), **kwargs)

    def list_directory(self, path):
        self.send_error(HTTPStatus.NOT_FOUND)
        return None

    def translate_path(self, path):
        resolved = Path(super().translate_path(path)).resolve()
        if not resolved.is_relative_to(WEB.resolve()):
            return str(WEB / '__not_found__')
        return str(resolved)

    def end_headers(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('Permissions-Policy', 'camera=(self), microphone=()')
        super().end_headers()

    def send_json(self, data, status=HTTPStatus.OK, *, headers=None):
        self.send_body(json.dumps(data, ensure_ascii=False).encode(), 'application/json; charset=utf-8', status, headers=headers)

    def send_body(self, body, content_type, status=HTTPStatus.OK, *, headers=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def error(self, status, message):
        self.send_json({'error': message}, status)

    def send_pdf(self, body, *, head=False):
        """Support mobile PDF viewers requesting a single byte range."""
        length, start, end = len(body), 0, len(body) - 1
        status = HTTPStatus.OK
        requested = self.headers.get_all('Range', [])
        if requested and not head:
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', requested[0]) if len(requested) == 1 else None
            valid = match and any(match.groups()) and all(len(value) <= 16 for value in match.groups())
            if valid:
                first, last = match.groups()
                if first:
                    start = int(first)
                    end = min(int(last), length - 1) if last else length - 1
                else:
                    start = max(0, length - int(last))
                valid = 0 <= start <= end < length
            if not valid:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header('Content-Range', f'bytes */{length}')
                self.send_header('Content-Length', '0')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                return
            status = HTTPStatus.PARTIAL_CONTENT
        self.send_response(status)
        self.send_header('Content-Type', 'application/pdf')
        self.send_header('Content-Disposition', 'inline')
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(end - start + 1))
        self.send_header('Cache-Control', 'no-store')
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header('Content-Range', f'bytes {start}-{end}/{length}')
        self.end_headers()
        if not head:
            self.wfile.write(body[start:end + 1])

    def do_HEAD(self):
        target = urlsplit(self.path).path
        prefix = '/api/customer/documents/'
        if target.startswith(prefix) and target.endswith('/pdf'):
            from server import customer
            try:
                self.send_pdf(customer.document(target[len(prefix):-len('/pdf')], self.customer_root), head=True)
            except FileNotFoundError:
                self.send_error(HTTPStatus.NOT_FOUND)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except (ValueError, KeyError, OSError, RuntimeError):
                self.send_error(HTTPStatus.SERVICE_UNAVAILABLE)
        else:
            super().do_HEAD()

    def do_GET(self):
        target = urlsplit(self.path)
        if target.path.startswith('/api/analysis-jobs/'):
            self.analysis_job(target.path)
            return
        if target.path in ('/api/equipment', '/api/recommendations'):
            try:
                parameters = parse_qs(target.query)
                if target.path == '/api/equipment':
                    self.send_json({'equipment': [PASSPORT, *recommendations.registry(root=self.customer_root)]})
                else:
                    serial = parameters.get('serial', [PASSPORT['serial']])[0]
                    component = parameters.get('component', [None])[0]
                    if component is not None and component not in recommendations.COMPONENT_LABELS:
                        self.error(HTTPStatus.BAD_REQUEST, 'Неизвестный узел оборудования.')
                        return
                    self.send_json(assessment.references(serial, component, root=self.customer_root))
            except (ValueError, KeyError, OSError):
                self.error(HTTPStatus.SERVICE_UNAVAILABLE, 'Каталог оборудования временно недоступен.')
            return
        image_match = re.fullmatch(r'/api/analyses/([0-9a-f-]{36})/image', target.path)
        inspection_match = re.fullmatch(r'/api/inspections/([0-9a-f-]{36})', target.path)
        if image_match or inspection_match:
            try:
                with closing(connect(self.database)) as connection:
                    if image_match:
                        self.send_body(inspections.get_analysis(connection, image_match[1], include_image=True), 'image/jpeg')
                    else:
                        record = history.get_record(connection, inspection_match[1])
                        if record is None:
                            raise inspections.MissingEvidence('Осмотр не найден.')
                        self.send_json({'record': record})
            except inspections.MissingEvidence as error:
                self.error(HTTPStatus.NOT_FOUND, str(error))
            except (BrokenPipeError, ConnectionResetError):
                pass
            except sqlite3.Error:
                self.error(HTTPStatus.SERVICE_UNAVAILABLE, 'История временно недоступна.')
            return
        if target.path.startswith('/api/customer/'):
            from server import customer
            try:
                endpoint = target.path.removeprefix('/api/customer/')
                if endpoint in ('materials', 'sensors', 'sensor-import-report', 'documents', 'equipment-passports'):
                    self.send_json(customer.load(endpoint, self.customer_root))
                elif endpoint == 'rules':
                    self.send_json(customer.rules())
                elif endpoint.startswith('images/'):
                    self.send_body(customer.image(endpoint.removeprefix('images/'), self.customer_root), 'image/jpeg')
                elif endpoint.startswith('documents/') and endpoint.endswith('/pdf'):
                    self.send_pdf(customer.document(endpoint[len('documents/'):-len('/pdf')], self.customer_root))
                else:
                    self.error(HTTPStatus.NOT_FOUND, 'Материал не найден.')
            except FileNotFoundError:
                self.error(HTTPStatus.NOT_FOUND, 'Материал не импортирован или недоступен.')
            except (BrokenPipeError, ConnectionResetError):
                pass
            except (ValueError, KeyError, OSError, RuntimeError):
                self.error(HTTPStatus.SERVICE_UNAVAILABLE, 'Материалы временно недоступны.')
            return
        if target.path.startswith('/api/telemetry/'):
            from server import telemetry
            try:
                if target.path == '/api/telemetry/catalog':
                    self.send_json(telemetry.catalog(self.customer_root))
                elif target.path == '/api/telemetry/snapshot':
                    indexes = parse_qs(target.query, keep_blank_values=True).get('index', [])
                    if len(indexes) != 1 or not indexes[0].isascii() or not indexes[0].isdecimal() or len(indexes[0]) > 8:
                        self.error(HTTPStatus.BAD_REQUEST, 'Укажите один целый индекс строки: index=0, 1, …')
                    else:
                        self.send_json(telemetry.snapshot(int(indexes[0]), root=self.customer_root))
                else:
                    self.error(HTTPStatus.NOT_FOUND, 'Источник телеметрии не найден.')
            except IndexError:
                self.error(HTTPStatus.BAD_REQUEST, 'Индекс выходит за пределы набора.')
            except FileNotFoundError:
                self.error(HTTPStatus.NOT_FOUND, 'Примеры телеметрии не импортированы.')
            except (BrokenPipeError, ConnectionResetError):
                pass
            except (ValueError, KeyError, OSError, RuntimeError):
                self.error(HTTPStatus.SERVICE_UNAVAILABLE, 'Источник телеметрии временно недоступен.')
            return
        if target.path == '/':
            query = f'?{target.query}' if target.query else ''
            self.send_response(HTTPStatus.FOUND)
            self.send_header('Location', f'/index.html{query}')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if not target.path.startswith('/api/'):
            super().do_GET()
            return

        parameters = parse_qs(target.query)
        serial = parameters.get('serial', [PASSPORT['serial']])[0]
        with closing(connect(self.database)) as connection:
            if target.path == '/api/health':
                connection.execute('SELECT serial FROM pumps LIMIT 1').fetchone()
                self.send_json({'status': 'ok', 'source': 'demo'})
            elif target.path == '/api/capabilities':
                self.send_json({'vision': {'enabled': self.vision_enabled, 'backend': self.vision_backend,
                    'model': vision.model_spec().model_id if self.vision_backend == 'internvl3' else None,
                    'revision': vision.model_spec().revision if self.vision_backend == 'internvl3' else None,
                    'videoReview': self.vision_enabled and self.vision_backend == 'internvl3',
                    'queue': self.server.analysis_queue.capabilities(self.vision_enabled),
                    'profiles': list(vision.supported_profiles(self.vision_backend))},
                    'media': {'maxPhotoBytes': 20 * 1024 * 1024, 'maxVideoBytes': 150 * 1024 * 1024,
                              'maxVideoSeconds': 120, 'maxSampledFrames': 20, 'maxReviewFrames': 8}})
            elif target.path == '/api/scenarios':
                self.scenarios(connection, serial)
            elif target.path == '/api/diagnose':
                scenario = parameters.get('scenario', ['healthy'])[0]
                self.diagnose_demo(connection, serial, scenario)
            elif target.path == '/api/qr':
                self.qr(connection, serial)
            elif target.path in ('/api/history', '/api/inspections'):
                if self.passport(connection, serial):
                    self.send_json({'records': history.list_records(connection, serial)})
            else:
                self.error(HTTPStatus.NOT_FOUND, 'Маршрут не найден.')

    def do_POST(self):
        route = urlsplit(self.path).path
        job_route = route.startswith('/api/analysis-jobs/')
        if not job_route and route not in ('/api/diagnose', '/api/history', '/analyze', '/api/video-review', '/api/inspections', '/api/inspections/preview'):
            self.error(HTTPStatus.NOT_FOUND, 'Маршрут не найден.')
            return
        origins = self.headers.get_all('Origin', [])
        if origins and (len(origins) != 1 or not origins[0] or
                        origins[0] not in (self.public_url, f'http://{self.headers.get("Host")}')):
            self.error(HTTPStatus.FORBIDDEN, 'Запрос разрешён только с адреса приложения.')
            return
        if job_route:
            self.analysis_job(route, cancel=True)
            return
        if route == '/analyze':
            self.analyze_frame()
            return
        if route == '/api/video-review':
            self.analyze_video()
            return
        if self.headers.get_content_type() != 'application/json':
            self.error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, 'Ожидается application/json.')
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= MAX_OBSERVATIONS_BYTES:
                self.error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, 'Недопустимый размер запроса.')
                return
            data = json.loads(self.rfile.read(length), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            # Reject non-finite overflow numbers and unpaired Unicode surrogates before SQLite binding.
            json.dumps(data, ensure_ascii=False, allow_nan=False).encode('utf-8')
        except (ValueError, RecursionError):
            self.error(HTTPStatus.BAD_REQUEST, 'Некорректный JSON.')
            return
        if route == '/api/history':
            self.save_history(data)
            return
        if route in ('/api/inspections', '/api/inspections/preview'):
            self.camera_inspection(data, preview=route.endswith('/preview'))
            return
        if not isinstance(data, dict) or not isinstance(data.get('serial'), str) or any(
            not isinstance(data.get(key), dict) for key in ('telemetry', 'vision')
        ):
            self.error(HTTPStatus.BAD_REQUEST, 'Укажите serial и объекты telemetry и vision.')
            return
        with closing(connect(self.database)) as connection:
            passport = self.passport(connection, data['serial'])
            if passport:
                self.send_json({'source': 'submitted', 'passport': passport,
                                **assessment.diagnose(passport, data['telemetry'], data['vision'])})

    def camera_inspection(self, data, *, preview=False):
        try:
            with closing(connect(self.database)) as connection, connection:
                if preview:
                    result = inspections.preview(connection, data, customer_root=self.customer_root)
                    status = HTTPStatus.OK
                else:
                    record, created = inspections.save(connection, data, customer_root=self.customer_root)
                    result = {'record': record, 'created': created}
                    status = HTTPStatus.CREATED if created else HTTPStatus.OK
            self.send_json(result, status)
        except history.IdempotencyConflict as error:
            self.error(HTTPStatus.CONFLICT, str(error))
        except (history.UnknownPump, inspections.MissingEvidence, inspections.MissingScenario) as error:
            self.error(HTTPStatus.NOT_FOUND, str(error))
        except (ValueError, UnicodeError) as error:
            self.error(HTTPStatus.BAD_REQUEST, str(error))
        except sqlite3.Error:
            self.error(HTTPStatus.SERVICE_UNAVAILABLE, 'Не удалось сохранить осмотр. Повторите запрос.')

    def save_history(self, data):
        if not isinstance(data, dict) or not isinstance(data.get('serial'), str) or not isinstance(data.get('scenarioId'), str):
            self.error(HTTPStatus.BAD_REQUEST, 'Укажите serial и scenarioId.')
            return
        try:
            with closing(connect(self.database)) as connection, connection:
                passport = self.passport(connection, data['serial'])
                if not passport:
                    return
                row = connection.execute('SELECT telemetry_json, vision_json FROM inspections WHERE serial=? AND id=?',
                                         (data['serial'], data['scenarioId'])).fetchone()
                if row is None:
                    self.error(HTTPStatus.NOT_FOUND, 'Сценарий не найден.')
                    return
                telemetry, vision = map(json.loads, row)
                record, created = history.save(connection, data['serial'], data['scenarioId'], telemetry, vision,
                    assessment.diagnose(passport, telemetry, vision)['findings'], note=data.get('note', ''), request_id=data.get('requestId'))
            self.send_json({'record': record, 'created': created}, HTTPStatus.CREATED if created else HTTPStatus.OK)
        except history.IdempotencyConflict as error:
            self.error(HTTPStatus.CONFLICT, str(error))
        except ValueError as error:
            self.error(HTTPStatus.BAD_REQUEST, str(error))

    def analysis_tokens(self, request=False):
        names = ('X-Analysis-Client', 'X-Analysis-Request') if request else ('X-Analysis-Client',)
        values = []
        for name in names:
            headers = self.headers.get_all(name, [])
            if len(headers) != 1:
                raise ValueError('Нужны идентификаторы сессии и запроса анализа.')
            values.append(analysis_queue.token(headers[0]))
        return values

    def wants_async(self):
        values = self.headers.get_all('Prefer', [])
        if len(values) > 1:
            raise ValueError('Повторный заголовок Prefer недопустим.')
        return any(part.strip().lower() == 'respond-async' for part in ','.join(values).split(','))

    def queue_error(self, error):
        if isinstance(error, analysis_queue.QueueFull):
            self.send_json({'error': 'Очередь заполнена. Повторите через несколько секунд.',
                            'code': 'analysis_queue_full', 'retryAfterSeconds': 2}, 503,
                           headers={'Retry-After': '2'})
        elif isinstance(error, analysis_queue.RequestConflict):
            self.send_json({'error': 'Этот идентификатор уже использован для другого анализа.',
                            'code': 'analysis_request_conflict'}, 409)
        elif isinstance(error, analysis_queue.JobNotFound):
            self.error(404, 'Задание не найдено или срок его хранения истёк.')

    def analysis_job(self, route, *, cancel=False):
        try:
            client, = self.analysis_tokens()
            by_request = re.fullmatch(r'/api/analysis-jobs/by-request/([^/]+)/cancel', route) if cancel else None
            match = re.fullmatch(r'/api/analysis-jobs/([0-9a-f]{32})' + ('/cancel' if cancel else ''), route)
            if cancel:
                lengths = self.headers.get_all('Content-Length', [])
                if self.headers.get_all('Transfer-Encoding') or len(lengths) > 1 or (lengths and lengths != ['0']):
                    raise ValueError('Отмена анализа не принимает тело запроса.')
            if by_request:
                job = self.server.analysis_queue.cancel_request(by_request[1], client)
            elif match:
                method = self.server.analysis_queue.cancel if cancel else self.server.analysis_queue.get
                job = method(match[1], client)
            else:
                raise analysis_queue.JobNotFound()
            self.send_json({'job': job})
        except ValueError as error:
            self.error(400, str(error))
        except (analysis_queue.QueueFull, analysis_queue.JobNotFound) as error:
            self.queue_error(error)

    def enqueue_analysis(self, kind, payload, canonical, run, finalize=None):
        client, request = self.analysis_tokens(request=True)
        digest = hashlib.sha256(kind.encode() + b'\0' + canonical).hexdigest()
        job = self.server.analysis_queue.submit(client, request, digest, kind, payload,
                                               len(canonical), run, finalize)
        self.send_json({'job': job}, HTTPStatus.ACCEPTED,
                       headers={'Location': '/api/analysis-jobs/' + job['id'],
                                'Preference-Applied': 'respond-async', 'Retry-After': '2'})

    @bounded_analysis_upload
    def analyze_frame(self):
        if not self.vision_enabled:
            self.error(HTTPStatus.SERVICE_UNAVAILABLE, 'Локальное зрение не включено на этом сервере.')
            return
        lengths = self.headers.get_all('Content-Length', [])
        if self.headers.get_all('Transfer-Encoding') or len(lengths) != 1:
            self.error(HTTPStatus.BAD_REQUEST, 'Укажите одну длину тела запроса в Content-Length.')
            return
        try:
            asynchronous = self.wants_async()
            if asynchronous:
                self.analysis_tokens(request=True)
            length_header = lengths[0].strip()
            if not length_header.isascii() or not length_header.isdecimal():
                raise ValueError('Некорректное значение Content-Length.')
            length = int(length_header)
            if not 0 < length <= vision.MAX_IMAGE_BYTES + 8192:
                self.error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, 'Кадр должен занимать не более 3 МБ.')
                return
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError('Тело запроса с кадром получено не полностью.')
            image, profile, media = vision.extract_request(self.headers.get('Content-Type', ''), body, include_metadata=True)
            if profile not in vision.supported_profiles(self.vision_backend):
                raise ValueError('Выбранный режим не поддерживается моделью.')
            payload = {'image': image, 'backend': self.vision_backend, 'profile': profile,
                       'media': media, 'database': self.database}
            if asynchronous:
                canonical = json.dumps({'backend': self.vision_backend, 'profile': profile, 'media': media},
                                       sort_keys=True).encode() + b'\0' + image
                self.enqueue_analysis('photo', payload, canonical, analyze_photo_job, save_photo_job)
                return
            with self.server.analysis_queue.legacy():
                result = save_photo_job(payload, analyze_photo_job(payload))
        except TimeoutError:
            self.error(HTTPStatus.REQUEST_TIMEOUT, 'Не удалось получить кадр за отведённое время.')
        except ValueError as error:
            self.error(HTTPStatus.BAD_REQUEST, str(error))
        except (analysis_queue.QueueFull, analysis_queue.RequestConflict) as error:
            self.queue_error(error)
        except vision.VisionBusy as error:
            self.send_json({'error': str(error), 'code': 'vision_busy', 'retryAfterSeconds': 2},
                           HTTPStatus.SERVICE_UNAVAILABLE, headers={'Retry-After': '2'})
        except (vision.VisionUnavailable, ImportError) as error:
            self.error(HTTPStatus.SERVICE_UNAVAILABLE, str(error))
        except sqlite3.Error:
            self.error(HTTPStatus.SERVICE_UNAVAILABLE, 'Не удалось сохранить кадр. Повторите проверку.')
        else:
            self.send_json(result)

    @bounded_analysis_upload
    def analyze_video(self):
        if not self.vision_enabled or self.vision_backend != 'internvl3':
            self.error(HTTPStatus.SERVICE_UNAVAILABLE, 'Совместный анализ видео недоступен на этом сервере.')
            return
        if self.headers.get_content_type() != 'application/json':
            self.error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, 'Ожидается application/json с кадрами видео.')
            return
        lengths = self.headers.get_all('Content-Length', [])
        if self.headers.get_all('Transfer-Encoding') or len(lengths) != 1:
            self.error(HTTPStatus.BAD_REQUEST, 'Укажите одну длину тела запроса в Content-Length.')
            return
        try:
            asynchronous = self.wants_async()
            if asynchronous:
                self.analysis_tokens(request=True)
            length_header = lengths[0].strip()
            if not length_header.isascii() or not length_header.isdecimal():
                raise ValueError('Некорректное значение Content-Length.')
            length = int(length_header)
            if not 0 < length <= video_review.MAX_REQUEST_BYTES:
                self.error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, 'Слишком большой набор кадров. Выберите более короткое видео.')
                return
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError('Кадры получены не полностью. Повторите отправку.')
            if asynchronous:
                payload = video_review.prepare_request(video_review.strict_json(body))
                canonical = json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
                self.enqueue_analysis('video', payload, canonical, analyze_video_job)
                return
            with self.server.analysis_queue.legacy():
                result = video_review.analyze_request(self.headers.get('Content-Type', ''), body)
        except TimeoutError:
            self.error(HTTPStatus.REQUEST_TIMEOUT, 'Не удалось получить кадры за отведённое время.')
        except (ValueError, UnicodeError, RecursionError) as error:
            self.error(HTTPStatus.BAD_REQUEST, str(error))
        except (analysis_queue.QueueFull, analysis_queue.RequestConflict) as error:
            self.queue_error(error)
        except vision.VisionBusy as error:
            self.send_json({'error': str(error), 'code': 'vision_busy', 'retryAfterSeconds': 2},
                           HTTPStatus.SERVICE_UNAVAILABLE, headers={'Retry-After': '2'})
        except (vision.VisionUnavailable, ImportError) as error:
            self.error(HTTPStatus.SERVICE_UNAVAILABLE, str(error))
        else:
            self.send_json(result)

    def passport(self, connection, serial):
        row = connection.execute('SELECT passport_json FROM pumps WHERE serial = ?', (serial,)).fetchone()
        if row is None:
            self.error(HTTPStatus.NOT_FOUND, 'Насос с таким серийным номером не найден.')
            return None
        return json.loads(row[0])

    def scenarios(self, connection, serial):
        passport = self.passport(connection, serial)
        if passport is None:
            return
        rows = connection.execute('SELECT id, label FROM inspections WHERE serial = ? ORDER BY rowid', (serial,))
        self.send_json({'passport': passport, 'scenarios': [{'id': id_, 'label': label} for id_, label in rows],
                        'qrAvailable': bool(self.public_url)})

    def diagnose_demo(self, connection, serial, scenario):
        passport = self.passport(connection, serial)
        if passport is None:
            return
        row = connection.execute('''
            SELECT label, telemetry_json, vision_json, zones_json, source, captured_at
            FROM inspections WHERE serial = ? AND id = ?
        ''', (serial, scenario)).fetchone()
        if row is None:
            self.error(HTTPStatus.NOT_FOUND, 'Сценарий для этого насоса не найден.')
            return
        label, telemetry_json, vision_json, zones_json, source, captured_at = row
        telemetry = json.loads(telemetry_json)
        vision = json.loads(vision_json)
        self.send_json({'source': source, 'capturedAt': captured_at, 'passport': passport,
                        'scenario': {'id': scenario, 'label': label}, 'telemetry': telemetry, 'vision': vision,
                        'zones': json.loads(zones_json), **assessment.diagnose(passport, telemetry, vision)})

    def qr(self, connection, serial):
        if self.passport(connection, serial) is None:
            return
        if not self.public_url:
            self.error(HTTPStatus.BAD_REQUEST, 'Задайте --public-url с адресом вебки, доступным телефону.')
            return
        try:
            import qrcode
            from qrcode.image.pure import PyPNGImage
            url = f'{self.public_url}/checkpoint.html?{urlencode({"serial": serial})}'
            buffer = BytesIO()
            qrcode.make(url, image_factory=PyPNGImage, box_size=7).save(buffer)
        except ImportError:
            self.error(HTTPStatus.SERVICE_UNAVAILABLE, 'Для QR установите зависимости из requirements.txt.')
            return
        self.send_body(buffer.getvalue(), 'image/png')


def main():
    parser = argparse.ArgumentParser(description='Локальный сервер первого чекпоинта Cyber102')
    parser.add_argument('--bind', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--database', type=Path, default=ROOT / 'var' / 'cyber102.sqlite3')
    parser.add_argument('--public-url', default='', help='HTTPS-адрес вебки для QR, без пути')
    parser.add_argument('--vision', action='store_true', help='Включить локальный анализ кадров')
    parser.add_argument('--vision-backend', choices=vision.BACKENDS, default='ollama',
                        help='Локальный сервис анализа кадров (по умолчанию ollama)')
    args = parser.parse_args()
    public_url = args.public_url.rstrip('/')
    if public_url:
        url = urlsplit(public_url)
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.path or url.query or url.fragment:
            parser.error('--public-url должен быть HTTPS-адресом без пути, параметров и учётных данных')
    initialize(args.database, customer_root=customer.ROOT)
    handler = partial(Handler, database=args.database, public_url=public_url,
                      vision_enabled=args.vision, vision_backend=args.vision_backend)
    with BoundedHTTPServer((args.bind, args.port), handler) as server:
        print(f'Cyber102 · http://{args.bind}:{server.server_port}/', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
