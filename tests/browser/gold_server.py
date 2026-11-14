"""Ephemeral real API for gold-smoke.cjs; no model calls or production writes."""
import json
from contextlib import closing
from functools import partial
from io import BytesIO
from pathlib import Path
import signal
import sys
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from PIL import Image
from server import app, customer, inspections


class FixtureHandler(app.Handler):
    def log_message(self, *_):
        pass

    def translate_path(self, path):
        target = urlsplit(path).path
        assets = ROOT / 'android/app/src/main/assets/inspection'
        if target == '/android.html':
            return str(assets / 'index.html')
        if target.startswith('/__mobile_inspection/'):
            resolved = (assets / target.removeprefix('/__mobile_inspection/')).resolve()
            return str(resolved if resolved.is_relative_to(assets.resolve()) else assets / '__not_found__')
        return super().translate_path(path)


def main():
    # Allow TemporaryDirectory and the HTTP socket to close on test completion.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    with tempfile.TemporaryDirectory(prefix='cyber102-gold-') as temporary:
        database = Path(temporary) / 'gold.sqlite3'
        app.initialize(database, customer_root=customer.ROOT)
        jpeg = BytesIO()
        Image.new('RGB', (32, 24), '#627c6d').save(jpeg, format='JPEG')
        result = {
            'status': 'defect', 'source': 'local_vision', 'model': 'gold-smoke-fixture',
            'component': 'Корпус насоса', 'finding': 'Возможная коррозия',
            'equipment': {'id': 'P-102', 'name': 'Тестовый насос'},
            'serviceLife': 'Тестовый источник', 'nextService': 'После подтверждения',
            'recommendation': 'Подтвердить наблюдения и сопоставить с телеметрией.',
            'observation': 'Тестовое свидетельство для проверки функционала осмотра',
            'box': {'x': .1, 'y': .2, 'width': .4, 'height': .5},
            'media': {'captureSource': 'uploaded_video', 'videoTimeSeconds': 1.25, 'declaredBy': 'client'},
        }
        with closing(app.connect(database)) as connection, connection:
            result |= inspections.store_analysis(connection, jpeg.getvalue(), result)
        exercise_uploads = '--inspection-work' in sys.argv
        fixture = {'analysis': result}
        if exercise_uploads:
            # Exercise the normal upload, queue, evidence persistence and journal routes.
            # Replace only model inference in this short-lived process.
            finding = json.loads((ROOT / 'web/mock-analysis.json').read_text())
            app.vision.analyze = lambda image, *, backend, profile: {
                **finding, 'source': 'local_vision', 'model': 'fixture', 'profile': profile,
                'observation': 'Следы коррозии на наружной поверхности фланца.',
            }
            closeup = Path(temporary) / 'flange-detail.jpg'
            with Image.open(ROOT / 'web/demo-pump.jpg') as photo:
                photo.crop((722, 220, 1032, 742)).save(closeup, 'JPEG', quality=92)
            fixture['closeup'] = str(closeup)
        exercise_video = '--video-history' in sys.argv
        if exercise_video:
            def fixture_video(endpoint, payload, timeout):
                spec = app.vision.model_spec()
                return {'model': spec.model_id, 'revision': spec.revision,
                        'durationSeconds': 0.2, 'summary': 'Проверить фланец на двух кадрах.',
                        'observations': [
                            {'frameIndex': i, 'timestampSeconds': frame['timestampSeconds'],
                             'component': 'pipe', 'condition': 'corrosion' if i in (2, 5) else 'no_visible_defect',
                             'observation': 'Результат контрольного кадра', 'box': [200, 250, 600, 750]}
                            for i, frame in enumerate(payload['frames'])]}
            app.vision.request_json = fixture_video
        handler = partial(FixtureHandler, database=database, public_url='',
                          vision_enabled=exercise_uploads or exercise_video,
                          vision_backend='internvl3' if exercise_video else 'ollama', customer_root=customer.ROOT)
        with app.BoundedHTTPServer(('127.0.0.1', 0), handler) as server:
            print(json.dumps({'url': f'http://127.0.0.1:{server.server_port}', **fixture}), flush=True)
            server.serve_forever(poll_interval=.05)


if __name__ == '__main__':
    main()
