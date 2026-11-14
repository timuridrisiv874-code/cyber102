"""Local DINOv2 image retrieval. CPU only; no network or uploaded-photo persistence."""
import argparse
from collections import OrderedDict
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
from pathlib import Path
import shutil
import threading
import time
from urllib.request import urlopen

from server import customer, vision, visual_search

ROOT = visual_search.ROOT
WEIGHTS = ROOT / 'models' / 'visual-search' / 'model.safetensors'


def verify_weights(path):
    if hashlib.sha256(Path(path).read_bytes()).hexdigest() != visual_search.WEIGHTS_SHA256:
        raise ValueError('Visual model checksum mismatch')


def prepare_weights(destination, source=None):
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.download')
    try:
        if source:
            shutil.copyfile(source, temporary)
        else:
            url = f'https://huggingface.co/{visual_search.MODEL}/resolve/{visual_search.REVISION}/model.safetensors'
            with urlopen(url, timeout=60) as response, temporary.open('wb') as output:
                shutil.copyfileobj(response, output)
        verify_weights(temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


class Engine:
    def __init__(self, weights=WEIGHTS, cache=ROOT / 'var' / 'visual-search',
                 customer_root=customer.ROOT, threads=2):
        import numpy as np
        import torch
        import timm
        from torchvision import transforms

        verify_weights(weights)
        torch.set_num_threads(threads)
        self.np, self.torch = np, torch
        self.model = timm.create_model('vit_small_patch14_dinov2.lvd142m', pretrained=True,
            pretrained_cfg_overlay={'file': str(weights)}, num_classes=0, img_size=224).cpu().eval()
        self.transform = transforms.Compose([transforms.ToTensor(),
            transforms.Normalize(mean=(.485, .456, .406), std=(.229, .224, .225))])
        self.entries, self.fingerprint = visual_search.catalogue(customer_root)
        self.queries = OrderedDict()
        cache = Path(cache)
        cache.mkdir(parents=True, exist_ok=True)
        index = cache / 'index.npz'
        self.vectors = None
        if index.exists():
            try:
                with np.load(index, allow_pickle=False) as saved:
                    vectors = saved['vectors']
                    if (str(saved['fingerprint']) == self.fingerprint and
                            vectors.shape == (len(self.entries), 384) and np.isfinite(vectors).all()):
                        self.vectors = vectors.copy()
            except (ValueError, KeyError, OSError):
                pass
        if self.vectors is None:
            from PIL import Image
            vectors = []
            for entry in self.entries:
                with Image.open(entry['_path']) as image:
                    vectors.append(self.encode(image))
            self.vectors = np.stack(vectors)
            temporary = index.with_suffix('.tmp')
            with temporary.open('wb') as output:
                np.savez_compressed(output, vectors=self.vectors, fingerprint=self.fingerprint)
            temporary.replace(index)

    def encode(self, image):
        from PIL import Image, ImageOps
        image = ImageOps.pad(ImageOps.exif_transpose(image).convert('RGB'), (224, 224),
                             method=Image.Resampling.BICUBIC, color=(238, 238, 238))
        with self.torch.inference_mode():
            vector = self.model(self.transform(image).unsqueeze(0)).float()[0]
            vector = self.torch.nn.functional.normalize(vector, dim=0)
        return vector.cpu().numpy()

    def health(self):
        return {'status': 'ok', 'model': visual_search.MODEL, 'revision': visual_search.REVISION,
                'pipeline': visual_search.PIPELINE, 'device': 'cpu', 'count': len(self.entries),
                'indexVersion': self.fingerprint}

    def search(self, image):
        from PIL import Image, ImageStat
        started = time.perf_counter()
        key = hashlib.sha256(image).hexdigest()
        if key in self.queries:
            self.queries.move_to_end(key)
            return {**self.queries[key], 'cached': True, 'durationMs': round((time.perf_counter() - started) * 1000)}
        with Image.open(BytesIO(image)) as photo:
            # Uniform frames carry no useful visual evidence; never turn them into an identity.
            informative = max(ImageStat.Stat(photo.convert('RGB')).stddev) >= 4
            vector = self.encode(photo) if informative else None
        matches = []
        if vector is not None:
            scores = self.vectors @ vector
            # Suppress the unrelated tail when one reference is distinctly closer.
            # This is a candidate-selection heuristic, never an identity confidence.
            floor = max(.45, float(scores.max()) - .10)
            for index in self.np.argsort(-scores)[:3]:
                if scores[index] < floor:
                    continue
                row = self.entries[index]
                matches.append({**{k: v for k, v in row.items() if k != '_path'},
                                'score': round(float(scores[index]), 4), 'identityConfirmed': False})
        result = {**self.health(), 'status': 'candidates' if matches else 'no_match', 'matches': matches,
                  'identityConfirmed': False, 'scoreMeaning': 'cosine_similarity_not_probability',
                  'durationMs': round((time.perf_counter() - started) * 1000), 'cached': False}
        self.queries[key] = result
        while len(self.queries) > 32:
            self.queries.popitem(last=False)
        return result


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, engine):
        self.engine = engine
        self.slot = threading.BoundedSemaphore(1)
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        self.reply(self.server.engine.health() if self.path == '/health' else {'error': 'not_found'},
                   200 if self.path == '/health' else 404)

    def do_POST(self):
        self.close_connection = True
        if self.path != '/search':
            self.reply({'error': 'not_found'}, 404)
            return
        if not self.server.slot.acquire(blocking=False):
            self.reply({'error': 'busy'}, 503)
            return
        try:
            self.connection.settimeout(10)
            lengths = self.headers.get_all('Content-Length', [])
            if (self.headers.get_all('Transfer-Encoding') or len(lengths) != 1 or
                    not lengths[0].isascii() or not lengths[0].isdecimal() or
                    self.headers.get_content_type() != 'image/jpeg'):
                raise ValueError('Invalid upload')
            size = int(lengths[0])
            if not 0 < size <= vision.MAX_IMAGE_BYTES:
                self.reply({'error': 'too_large'}, 413)
                return
            image = self.rfile.read(size)
            if len(image) != size:
                raise ValueError('Incomplete upload')
            self.reply(self.server.engine.search(vision.normalize_image(image)))
        except (ValueError, TimeoutError, OSError):
            self.reply({'error': 'invalid_image'}, 400)
        except Exception:
            import logging
            logging.exception('Visual search failed')
            self.reply({'error': 'search_unavailable'}, 503)
        finally:
            self.server.slot.release()


def main():
    parser = argparse.ArgumentParser(description='CPU-поиск похожего оборудования по фото')
    parser.add_argument('--port', type=int, default=11437)
    parser.add_argument('--threads', type=int, choices=range(1, 9), default=2)
    parser.add_argument('--weights', type=Path, default=WEIGHTS)
    parser.add_argument('--cache', type=Path, default=ROOT / 'var' / 'visual-search')
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--source-weights', type=Path)
    args = parser.parse_args()
    if args.prepare:
        prepare_weights(args.weights, args.source_weights)
        print('Visual model prepared and verified', flush=True)
        return
    engine = Engine(args.weights, args.cache, threads=args.threads)
    with Server(('127.0.0.1', args.port), engine) as server:
        print(json.dumps({**engine.health(), 'port': server.server_port}), flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
