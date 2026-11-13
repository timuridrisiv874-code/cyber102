import argparse
import json
import sqlite3
from contextlib import closing
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from .demo_data import PASSPORT, SCENARIOS
from .diagnostics import evaluate


ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / 'web'
MAX_OBSERVATIONS_BYTES = 32 * 1024


def initialize(database):
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database)) as connection, connection:
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


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, database, public_url, **kwargs):
        self.database = database
        self.public_url = public_url
        super().__init__(*args, directory=str(WEB), **kwargs)

    def list_directory(self, path):
        self.send_error(HTTPStatus.NOT_FOUND)
        return None

    def send_json(self, data, status=HTTPStatus.OK):
        self.send_body(json.dumps(data, ensure_ascii=False).encode(), 'application/json; charset=utf-8', status)

    def send_body(self, body, content_type, status=HTTPStatus.OK):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def error(self, status, message):
        self.send_json({'error': message}, status)

    def do_GET(self):
        target = urlsplit(self.path)
        if target.path == '/':
            query = f'?{target.query}' if target.query else ''
            self.send_response(HTTPStatus.FOUND)
            self.send_header('Location', f'/checkpoint.html{query}')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if not target.path.startswith('/api/'):
            super().do_GET()
            return

        parameters = parse_qs(target.query)
        serial = parameters.get('serial', [PASSPORT['serial']])[0]
        with closing(sqlite3.connect(self.database)) as connection:
            if target.path == '/api/health':
                connection.execute('SELECT serial FROM pumps LIMIT 1').fetchone()
                self.send_json({'status': 'ok', 'source': 'demo'})
            elif target.path == '/api/scenarios':
                self.scenarios(connection, serial)
            elif target.path == '/api/diagnose':
                scenario = parameters.get('scenario', ['healthy'])[0]
                self.diagnose_demo(connection, serial, scenario)
            elif target.path == '/api/qr':
                self.qr(connection, serial)
            else:
                self.error(HTTPStatus.NOT_FOUND, 'Маршрут не найден.')

    def do_POST(self):
        if urlsplit(self.path).path != '/api/diagnose':
            self.error(HTTPStatus.NOT_FOUND, 'Маршрут не найден.')
            return
        if self.headers.get_content_type() != 'application/json':
            self.error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, 'Ожидается application/json.')
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= MAX_OBSERVATIONS_BYTES:
                self.error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, 'Недопустимый размер запроса.')
                return
            data = json.loads(self.rfile.read(length))
        except (ValueError, RecursionError):
            self.error(HTTPStatus.BAD_REQUEST, 'Некорректный JSON.')
            return
        if not isinstance(data, dict) or not isinstance(data.get('serial'), str) or any(
            not isinstance(data.get(key), dict) for key in ('telemetry', 'vision')
        ):
            self.error(HTTPStatus.BAD_REQUEST, 'Укажите serial и объекты telemetry и vision.')
            return
        with closing(sqlite3.connect(self.database)) as connection:
            passport = self.passport(connection, data['serial'])
            if passport:
                self.send_json({'source': 'submitted', 'passport': passport,
                                'findings': evaluate(data['telemetry'], data['vision'], passport['thresholds'])})

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
                        'zones': json.loads(zones_json), 'findings': evaluate(telemetry, vision, passport['thresholds'])})

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
    args = parser.parse_args()
    public_url = args.public_url.rstrip('/')
    if public_url:
        url = urlsplit(public_url)
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.path or url.query or url.fragment:
            parser.error('--public-url должен быть HTTPS-адресом без пути, параметров и учётных данных')
    initialize(args.database)
    handler = partial(Handler, database=args.database, public_url=public_url)
    with ThreadingHTTPServer((args.bind, args.port), handler) as server:
        print(f'Cyber102 · http://{args.bind}:{server.server_port}/', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
