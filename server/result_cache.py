"""Bounded, versioned inference results. Media evidence is finalized per request."""
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
KEY = re.compile(r'[a-f0-9]{64}\.json\Z')
PIPELINE_FILES = ('server/vision.py', 'server/video_review.py', 'server/knowledge.py',
                  'vision_runtime/worker.py', 'vision_runtime/qwen_worker.py',
                  'vision_runtime/qwen38_worker.py', 'vision_runtime/video_defects.py',
                  'vision_runtime/liquid.py')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')


def inference_key(kind, payload, model):
    pipeline = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in PIPELINE_FILES}
    identity = {'version': 1, 'kind': kind, 'model': model.model_id, 'revision': model.revision,
                'pipeline': pipeline, 'details': os.environ.get('CYBER102_VIDEO_DEFECT_DETAILS', '')}
    if kind == 'photo':
        identity.update(image=hashlib.sha256(payload['image']).hexdigest(), backend=payload['backend'],
                        profile=payload['profile'], context=payload.get('equipmentContext'))
    elif kind == 'video':
        identity.update(frames=payload['frames'], durationSeconds=payload['durationSeconds'],
                        context=payload.get('equipmentContext'))
    else:
        raise ValueError('Unknown inference kind')
    return hashlib.sha256(canonical(identity)).hexdigest()


class ResultCache:
    def __init__(self, root, *, ttl=7*86400, max_entries=512, max_bytes=64*1024*1024,
                 max_entry_bytes=256*1024, clock=time.time):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.ttl, self.max_entries, self.max_bytes = ttl, max_entries, max_bytes
        self.max_entry_bytes, self.clock = max_entry_bytes, clock
        self.lock = threading.RLock()
        with self.lock:
            self._prune()

    def _path(self, key):
        if not isinstance(key, str) or not KEY.fullmatch(key + '.json'):
            raise ValueError('Invalid cache key')
        return self.root / (key + '.json')

    def _load(self, path, expected_key):
        if path.stat().st_size > self.max_entry_bytes:
            return None
        record = json.loads(path.read_bytes())
        created = record.get('created')
        if (record.get('version') != 1 or record.get('key') != expected_key
                or type(created) not in (int, float) or not math.isfinite(created)
                or not 0 <= self.clock() - created < self.ttl):
            return None
        result = record.get('result')
        if (not isinstance(result, dict) or result.get('status') not in ('clear', 'defect')
                or any(name in result for name in ('analysisId', 'imageUrl', 'representativeFrame', 'media'))
                or hashlib.sha256(canonical(result)).hexdigest() != record.get('sha256')):
            return None
        return result

    def get(self, key):
        path = self._path(key)
        with self.lock:
            try:
                result = self._load(path, key)
                if result is not None:
                    path.touch()
                    return result
                path.unlink(missing_ok=True)
            except (OSError, ValueError, TypeError, RecursionError, AttributeError):
                pass
        return None

    def _prune(self):
        files = []
        for path in self.root.glob('*.json'):
            if not KEY.fullmatch(path.name):
                continue
            try:
                if self._load(path, path.stem) is None:
                    path.unlink(missing_ok=True)
                    continue
                stat = path.stat()
                files.append((stat.st_mtime, stat.st_size, path))
            except (OSError, ValueError, TypeError, RecursionError, AttributeError):
                path.unlink(missing_ok=True)
        files.sort()
        total = sum(size for _, size, _ in files)
        while files and (len(files) > self.max_entries or total > self.max_bytes):
            _, size, path = files.pop(0)
            path.unlink(missing_ok=True)
            total -= size

    def put(self, key, result):
        if (not isinstance(result, dict) or result.get('status') not in ('clear', 'defect')
                or any(name in result for name in ('analysisId', 'imageUrl', 'representativeFrame', 'media'))):
            return
        path = self._path(key)
        data = canonical({'version': 1, 'key': key, 'created': self.clock(), 'result': result,
                          'sha256': hashlib.sha256(canonical(result)).hexdigest()})
        if len(data) > self.max_entry_bytes:
            return
        temporary = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
        with self.lock:
            try:
                temporary.write_bytes(data)
                temporary.replace(path)
                self._prune()
            except OSError:
                logging.warning('Inference result cache write failed')
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
