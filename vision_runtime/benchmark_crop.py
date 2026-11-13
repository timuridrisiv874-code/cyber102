"""Frozen full-image -> original-crop experiment through the public local queue.

Preparation is offline; running requires the SHA of the saved protocol. Run on
the web server (or an explicitly established loopback SSH tunnel). This never
loads a model, changes a service, or writes an inspection/journal record. The
normal /analyze API may retain temporary analysis images/results.
"""
import argparse
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path, PurePosixPath
import re
import signal
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from server.vision_models import model_spec

PANEL = [7, 8, 9, 12, 16, 22, 23, 34, 35, 39, 41, 48]  # one-based catalog indices
SETTINGS = {'maxEdge':1280, 'jpegQuality':85, 'upscale':False, 'paddingPerSide':0.1,
            'maxCropAreaExclusive':0.85, 'minCropEdge':64, 'profile':'standard',
            'jobTimeoutSeconds':780, 'pollIntervalSeconds':2}
SHA = re.compile(r'^[0-9a-f]{64}$')
JOB_ID = re.compile(r'^[0-9a-f]{32}$')
MAX_JSON = 512 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode('utf-8')


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_bytes(json_bytes(value))
    temporary.replace(path)


def source_hashes():
    return {name:digest((ROOT / name).read_bytes()) for name in (
        'vision_runtime/worker.py', 'vision_runtime/qwen_worker.py',
        'server/vision.py', 'server/vision_models.py')}


def loopback_endpoint(value):
    parsed = urlsplit(value)
    if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1')
            or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise ValueError('Use an HTTP loopback origin without credentials, path or query')
    if parsed.port is None:
        raise ValueError('Specify the local web API port explicitly')
    return value.rstrip('/')


def source_file(root, relative):
    part = PurePosixPath(relative)
    if part.is_absolute() or '..' in part.parts or '\\' in relative or not part.parts:
        raise ValueError('Original source path must be relative to source-root')
    resolved = root.joinpath(*part.parts).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError('Original source path escapes source-root')
    return resolved


def open_original(path, expected_sha):
    from PIL import Image, ImageOps
    if path.stat().st_size > 30 * 1024 * 1024:
        raise ValueError('Original exceeds the 30 MiB experiment bound')
    raw = path.read_bytes()
    if digest(raw) != expected_sha:
        raise ValueError('Original SHA mismatch: ' + path.name)
    with Image.open(BytesIO(raw)) as image:
        if image.width * image.height > 60_000_000:
            raise ValueError('Original exceeds the 60 MP experiment bound')
        return ImageOps.exif_transpose(image).convert('RGB')


def normalized_jpeg(image):
    from PIL import Image
    preview = image.copy()
    preview.thumbnail((SETTINGS['maxEdge'], SETTINGS['maxEdge']), Image.Resampling.LANCZOS)
    output = BytesIO()
    preview.save(output, format='JPEG', quality=SETTINGS['jpegQuality'])
    raw = output.getvalue()
    if len(raw) > 3 * 1024 * 1024:
        raise ValueError('Normalized JPEG exceeds the public API limit')
    metadata = {'width':preview.width, 'height':preview.height, 'bytes':len(raw), 'sha256':digest(raw)}
    preview.close()
    return raw, metadata


def normalized_box(value):
    if not isinstance(value, dict) or set(value) != {'x', 'y', 'width', 'height'}:
        return None
    if any(type(value[key]) not in (int, float) or not math.isfinite(value[key]) for key in value):
        return None
    x, y, width, height = (value[key] for key in ('x', 'y', 'width', 'height'))
    if not (0 <= x < 1 and 0 <= y < 1 and width > 0 and height > 0 and x + width <= 1 + 1e-9 and y + height <= 1 + 1e-9):
        return None
    return x, y, min(1, x + width), min(1, y + height)


def crop_plan(result, size):
    """Integer crop in EXIF-oriented original pixels, not in the compressed A."""
    if result.get('status') not in ('clear', 'defect') or result.get('component') == 'none' or result.get('condition') == 'uncertain':
        return {'eligible':False, 'reason':'no_confident_component_box'}
    box = normalized_box(result.get('box'))
    if box is None:
        return {'eligible':False, 'reason':'invalid_box'}
    width, height = size
    left, top, right, bottom = box
    pad_x, pad_y = (right-left) * SETTINGS['paddingPerSide'], (bottom-top) * SETTINGS['paddingPerSide']
    pixels = [max(0, math.floor((left-pad_x)*width)), max(0, math.floor((top-pad_y)*height)),
              min(width, math.ceil((right+pad_x)*width)), min(height, math.ceil((bottom+pad_y)*height))]
    crop_width, crop_height = pixels[2]-pixels[0], pixels[3]-pixels[1]
    fraction = crop_width * crop_height / (width * height)
    reason = ('crop_too_small' if min(crop_width, crop_height) < SETTINGS['minCropEdge'] else
              'crop_covers_almost_full_image' if fraction >= SETTINGS['maxCropAreaExclusive'] else None)
    return {'eligible':reason is None, 'reason':reason, 'pixels':pixels,
            'width':crop_width, 'height':crop_height, 'areaFraction':fraction}


def map_crop_box(box, crop, original_size):
    normalized = normalized_box(box)
    if normalized is None:
        return None
    left, top, right, bottom = normalized
    x0, y0, x1, y1 = crop['pixels']
    width, height = original_size
    return {'x':(x0 + left*(x1-x0))/width, 'y':(y0 + top*(y1-y0))/height,
            'width':(right-left)*(x1-x0)/width, 'height':(bottom-top)*(y1-y0)/height}


def pixel_metrics(original_size, full, crop, cropped):
    width, height = original_size
    sx, sy = full['width']/width, full['height']/height
    bx, by = cropped['width']/crop['width'], cropped['height']/crop['height']
    return {'sourceResolutionRetentionGainX':bx/sx, 'sourceResolutionRetentionGainY':by/sy,
            'sourceResolutionRetentionAreaGain':bx*by/(sx*sy),
            'retainedContextAreaFraction':crop['areaFraction'],
            'cropInputPixels':cropped['width']*cropped['height'],
            'belowNativeMinimumPixels':cropped['width']*cropped['height'] < 65_536,
            'interpretation':'Ratios describe retained source samples, not new detail or accuracy. Native minimum-size upscaling is interpolation.'}


def prepare(inventory, source_root, output, endpoint):
    import PIL
    raw = inventory.read_bytes()
    entries = json.loads(raw)['images']
    if [item.get('index') for item in entries] != PANEL:
        raise ValueError('Inventory must contain exactly the frozen one-based 12-image panel in order')
    endpoint = loopback_endpoint(endpoint)
    spec = model_spec('qwen3_vl_4b')
    output.mkdir(parents=True, exist_ok=False)
    (output / 'inputs').mkdir()
    images = []
    for item in entries:
        identifier = item.get('id', '')
        if not SHA.fullmatch(identifier):
            raise ValueError('Original ID must be a SHA-256')
        relative = item['catalog_source_path']
        with open_original(source_file(source_root, relative), identifier) as image:
            if list(image.size) != item['oriented_original_dimensions']:
                raise ValueError('EXIF-oriented dimensions differ from the inventory')
            jpeg, meta = normalized_jpeg(image)
            name = f"inputs/{item['index']:02d}-A.jpg"
            (output / name).write_bytes(jpeg)
            images.append({'index':item['index'], 'imageId':identifier, 'sourcePath':relative,
                           'originalSize':list(image.size), 'fullInput':dict(meta, path=name)})
    protocol = {'schemaVersion':1, 'createdAt':datetime.now(timezone.utc).isoformat(),
        'panelOneBased':PANEL, 'inventorySha256':digest(raw), 'endpoint':endpoint,
        'model':spec.model_id, 'revision':spec.revision, 'settings':SETTINGS,
        'pillowVersion':PIL.__version__, 'scriptSha256':digest(Path(__file__).read_bytes()),
        'sourceHashes':source_hashes(), 'images':images,
        'order':'For each fixed image: A full -> eligible B crop. One pending queued request, no POST retry.',
        'compression':'Both A and B are JPEG85 uploads; the same public API decodes and re-encodes each as JPEG85 again.',
        'limitations':['Small purposive panel, no expert accuracy estimate or production decision.',
            'Crop selected by A may be wrong or omit the defect; no B when A abstains.',
            'Crop reduces context. It does not add source detail; native minimum-size upscaling is interpolation.',
            'No worker THW shape in the public API; record actual native grid-cell count and input tokens only.',
            'Timings include queue and API overhead; no isolation, no warmup or latency stability claim.'],
        'writes':'Temporary analysis API records only; never /api/inspections or /api/history.'}
    write_json(output / 'protocol.json', protocol)
    return digest((output / 'protocol.json').read_bytes())


def multipart(jpeg, profile='standard', boundary=None):
    if profile != 'standard' or not 0 < len(jpeg) <= 3 * 1024 * 1024:
        raise ValueError('Expected bounded JPEG and fixed standard profile')
    boundary = boundary or ('crop-' + uuid.uuid4().hex)
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="frame.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'.encode(), jpeg,
        f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="profile"\r\n\r\nstandard\r\n--{boundary}\r\nContent-Disposition: form-data; name="captureSource"\r\n\r\nuploaded_photo\r\n--{boundary}--\r\n'.encode()]
    return b''.join(parts), {'Content-Type':'multipart/form-data; boundary=' + boundary}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class LocalHTTP:
    def __init__(self, endpoint):
        self.endpoint = loopback_endpoint(endpoint)
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def __call__(self, method, path, body=None, headers=None, timeout=20):
        if not (path == '/analyze' or path == '/api/capabilities' or path.startswith('/api/analysis-jobs/')):
            raise ValueError('Experiment route is not allowed')
        request = Request(self.endpoint + path, body, {'Origin':self.endpoint, **(headers or {})}, method=method)
        try:
            response = self.opener.open(request, timeout=timeout)
        except HTTPError as error:
            response = error
        with response:
            raw = response.read(MAX_JSON + 1)
            if len(raw) > MAX_JSON:
                raise ValueError('Public API response exceeds experiment bound')
            return response.code, json.loads(raw)


def verify_capabilities(data, protocol):
    vision = data.get('vision', {}) if isinstance(data, dict) else {}
    if (vision.get('enabled') is not True or vision.get('queue', {}).get('enabled') is not True
            or vision.get('model') != protocol['model'] or vision.get('revision') != protocol['revision']
            or 'standard' not in vision.get('profiles', [])):
        raise ValueError('Production capabilities differ from the frozen Qwen queue protocol')
    return vision


def verify_result(result, protocol):
    if (not isinstance(result, dict) or result.get('source') != 'local_vision'
            or result.get('model') != protocol['model'] or result.get('profile') != 'standard'
            or result.get('status') not in ('clear', 'defect', 'unusable')
            or ('revision' in result and result['revision'] != protocol['revision'])):
        raise ValueError('Public result differs from the frozen model/profile contract')


class QueueFailure(RuntimeError):
    def __init__(self, message, record):
        super().__init__(message)
        self.record = record


class QueueClient:
    def __init__(self, transport, protocol, clock=time.monotonic, sleep=time.sleep):
        self.transport, self.protocol, self.clock, self.sleep = transport, protocol, clock, sleep
        self.client = str(uuid.uuid4())
        self.active = False

    def guard(self):
        status, data = self.transport('GET', '/api/capabilities')
        if status != 200:
            raise ValueError('Capabilities are unavailable; no analysis submitted')
        return verify_capabilities(data, self.protocol)

    def analyze(self, jpeg):
        if self.active:
            raise RuntimeError('Only one pending experiment job is allowed')
        self.guard()
        self.active = True
        started, request_id = self.clock(), str(uuid.uuid4())
        deadline = started + SETTINGS['jobTimeoutSeconds']
        record = {'success':False, 'events':[], 'requestId':request_id}
        submitted = terminal = False
        def remaining():
            value = deadline - self.clock()
            if value <= 0:
                raise TimeoutError('Queued analysis exceeded the frozen deadline')
            return min(20, value)
        def job_from(data, expected=None):
            job = data.get('job') if isinstance(data, dict) else None
            if (not isinstance(job, dict) or not JOB_ID.fullmatch(job.get('id', ''))
                    or (expected and job['id'] != expected) or job.get('type') != 'photo'
                    or job.get('status') not in ('queued','running','cancelling','succeeded','failed','cancelled','expired')):
                raise ValueError('Invalid or mismatched queue job')
            return job
        try:
            body, headers = multipart(jpeg)
            headers.update({'Prefer':'respond-async', 'X-Analysis-Client':self.client, 'X-Analysis-Request':request_id})
            submitted = True
            status, data = self.transport('POST', '/analyze', body, headers, remaining())
            record['submissionStatus'] = status
            if status != 202:
                terminal = True
                record['response'] = data
                raise ValueError('Public queue did not accept the request')
            job = job_from(data)
            identifier = job['id']
            while True:
                remaining()
                state = {'status':job['status'], 'position':job.get('position')}
                if not record['events'] or any(record['events'][-1][key] != value for key, value in state.items()):
                    record['events'].append(dict(state, elapsedSeconds=round(self.clock()-started, 3)))
                if job['status'] in ('succeeded','failed','cancelled','expired'):
                    terminal = True
                    record['job'] = {key:value for key, value in job.items() if key != 'result'}
                    record['result'] = job.get('result')
                    if job['status'] != 'succeeded':
                        raise ValueError('Queued analysis finished as ' + job['status'])
                    verify_result(record['result'], self.protocol)
                    record['capabilitiesAfter'] = self.guard()
                    record['success'] = True
                    return record
                self.sleep(min(SETTINGS['pollIntervalSeconds'], remaining()))
                status, data = self.transport('GET', '/api/analysis-jobs/' + identifier,
                    headers={'X-Analysis-Client':self.client}, timeout=remaining())
                if status != 200:
                    record['response'] = data
                    raise ValueError('Queue polling failed with HTTP ' + str(status))
                job = job_from(data, identifier)
        except BaseException as error:
            record['errorType'], record['error'] = type(error).__name__, str(error)[:1200]
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                error.experiment_record = record
                raise
            raise QueueFailure(str(error), record) from error
        finally:
            if submitted and not terminal:
                try:
                    status, data = self.transport('POST', '/api/analysis-jobs/by-request/' + request_id + '/cancel',
                        body=b'', headers={'X-Analysis-Client':self.client}, timeout=10)
                    record['cancellation'] = {'httpStatus':status, 'status':data.get('job', {}).get('status')}
                except Exception as error:
                    record['cancellation'] = {'errorType':type(error).__name__}
            record['elapsedSeconds'] = round(self.clock() - started, 3)
            self.active = False


def input_grid(result):
    cells = result.get('metrics', {}).get('patches')
    return {'nativeGridCells':cells, 'inputTokens':result.get('metrics', {}).get('inputTokens'),
            'imageGridTHW':None, 'reason':'The public API exposes the cell count, not the worker THW tensor.'}


def run(output, source_root, protocol_sha):
    import PIL
    raw = (output / 'protocol.json').read_bytes()
    if digest(raw) != protocol_sha:
        raise ValueError('Protocol SHA differs from the reviewed preparation')
    protocol = json.loads(raw)
    spec = model_spec('qwen3_vl_4b')
    if (protocol['settings'] != SETTINGS or protocol['panelOneBased'] != PANEL
            or [row['index'] for row in protocol['images']] != PANEL
            or protocol['model'] != spec.model_id or protocol['revision'] != spec.revision):
        raise ValueError('Protocol does not match this fixed experiment')
    if (protocol.get('scriptSha256') != digest(Path(__file__).read_bytes())
            or protocol.get('sourceHashes') != source_hashes() or protocol.get('pillowVersion') != PIL.__version__):
        raise ValueError('Experiment code, model prompt sources or Pillow changed after preparation')
    for row in protocol['images']:
        if row['fullInput']['path'] != f"inputs/{row['index']:02d}-A.jpg":
            raise ValueError('Unexpected prepared image path')
        with open_original(source_file(source_root, row['sourcePath']), row['imageId']) as image:
            if list(image.size) != row['originalSize']:
                raise ValueError('Original dimensions changed')
        if digest(source_file(output, row['fullInput']['path']).read_bytes()) != row['fullInput']['sha256']:
            raise ValueError('Prepared A input changed')
    with (output / '.run-started').open('x') as marker:
        marker.write(datetime.now(timezone.utc).isoformat())
    client = QueueClient(LocalHTTP(protocol['endpoint']), protocol)
    report = {'protocolSha256':protocol_sha, 'protocol':protocol, 'records':[], 'complete':False,
              'conclusion':'No quality or accuracy conclusion before paired visual review.'}
    write_json(output / 'results.json', report)
    failed = False
    try:
        report['capabilitiesBefore'] = client.guard()
        for image in protocol['images']:
            row = {'index':image['index'], 'imageId':image['imageId']}
            report['records'].append(row)
            phase = 'A'
            try:
                row['A'] = client.analyze((output / image['fullInput']['path']).read_bytes())
                row['A']['nativeInput'] = input_grid(row['A']['result'])
                write_json(output / 'results.json', report)
                phase = 'cropPreparation'
                plan = crop_plan(row['A']['result'], image['originalSize'])
                row['crop'] = plan
                if plan['eligible']:
                    with open_original(source_file(source_root, image['sourcePath']), image['imageId']) as original:
                        with original.crop(plan['pixels']) as cropped:
                            jpeg, meta = normalized_jpeg(cropped)
                    path = f"inputs/{image['index']:02d}-B.jpg"
                    (output / path).write_bytes(jpeg)
                    row['cropInput'] = dict(meta, path=path)
                    row['pixelMetrics'] = pixel_metrics(image['originalSize'], image['fullInput'], plan, meta)
                    phase = 'B'
                    row['B'] = client.analyze(jpeg)
                    row['B']['nativeInput'] = input_grid(row['B']['result'])
                    row['mappedBBox'] = map_crop_box(row['B']['result'].get('box'), plan, image['originalSize'])
                    row['twoPassElapsedSeconds'] = row['A']['elapsedSeconds'] + row['B']['elapsedSeconds']
                else:
                    row['B'] = {'skipped':True, 'reason':plan['reason']}
            except BaseException as error:
                row[phase] = getattr(error, 'record', getattr(error, 'experiment_record',
                    {'success':False, 'errorType':type(error).__name__, 'error':str(error)[:1200]}))
                failed = True
                raise
            finally:
                write_json(output / 'results.json', report)
            print(json.dumps({'index':image['index'], 'A':'ok', 'B':'ok' if plan['eligible'] else plan['reason']}), flush=True)
        report['complete'] = True
    except (Exception, KeyboardInterrupt) as error:
        failed = True
        report['runError'] = {'type':type(error).__name__, 'message':str(error)[:1200]}
    finally:
        report['finishedAt'] = datetime.now(timezone.utc).isoformat()
        write_json(output / 'results.json', report)
    return 2 if failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    preparation = commands.add_parser('prepare', help='Offline preparation only; no API calls')
    preparation.add_argument('--inventory', type=Path, required=True)
    preparation.add_argument('--source-root', type=Path, required=True)
    preparation.add_argument('--output', type=Path, required=True)
    preparation.add_argument('--endpoint', default='http://127.0.0.1:8102')
    execution = commands.add_parser('run', help='Run only after review of the saved protocol and SHA')
    execution.add_argument('--source-root', type=Path, required=True)
    execution.add_argument('--output', type=Path, required=True)
    execution.add_argument('--protocol-sha256', required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        value = prepare(args.inventory, args.source_root, args.output, args.endpoint)
        print(json.dumps({'prepared':True, 'protocolSha256':value, 'images':len(PANEL), 'apiRequests':0}))
        return 0
    def interrupt(_signum, _frame):
        raise KeyboardInterrupt('Interrupted; cancel the one pending queue request')
    signal.signal(signal.SIGTERM, interrupt)
    return run(args.output, args.source_root, args.protocol_sha256)


if __name__ == '__main__':
    raise SystemExit(main())
