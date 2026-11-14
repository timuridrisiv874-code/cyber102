"""Bounded in-memory FIFO for one web process and one shared inference worker.

Jobs survive browser disconnects, not server restarts. Images are released on
completion/cancellation; only bounded result metadata remains for a short TTL.
"""
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import logging
import re
import threading
import time
import uuid

from . import vision

TOKEN = re.compile(r'(?:[0-9a-fA-F]{32,64}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})\Z')
TERMINAL = {'succeeded', 'failed', 'cancelled', 'expired'}
logger = logging.getLogger(__name__)


def log_failure(job, stage, error):
    # Error messages, tracebacks and request payloads may contain image bytes or credentials.
    cause = error.__cause__ or error.__context__
    logger.warning('Analysis %s (%s) failed during %s: %s; cause=%s', job.id, job.kind, stage,
                   type(error).__name__, type(cause).__name__ if cause else 'none')


class QueueFull(Exception):
    pass


class RequestConflict(Exception):
    pass


class JobNotFound(Exception):
    pass


def token(value):
    if not isinstance(value, str) or not TOKEN.fullmatch(value):
        raise ValueError('Нужны корректные идентификаторы сессии и запроса анализа.')
    return value.lower()


def owner(value):
    return hashlib.sha256(token(value).encode('ascii')).digest()


def iso(value):
    return None if value is None else datetime.fromtimestamp(value, timezone.utc).isoformat()


@dataclass
class Job:
    id: str
    owner: bytes
    request: str
    digest: str | None
    kind: str | None
    created: float
    created_wall: float
    payload: object = None
    run: object = None
    finalize: object = None
    size: int = 0
    status: str = 'queued'
    started_wall: float | None = None
    finished: float | None = None
    finished_wall: float | None = None
    result: object = None
    error: str | None = None
    code: str | None = None
    error_status: int | None = None


class AnalysisQueue:
    def __init__(self, *, max_waiting=8, max_bytes=64*1024*1024, max_jobs=64, max_per_client=2,
                 max_wait=600, result_ttl=300, max_tombstones_per_client=8, max_tombstones=16,
                 clock=time.monotonic, wall_clock=time.time):
        self.max_waiting, self.max_bytes, self.max_jobs = max_waiting, max_bytes, max_jobs
        self.max_per_client = max_per_client
        self.max_tombstones_per_client = max_tombstones_per_client
        self.max_tombstones = max_tombstones
        self.max_wait, self.result_ttl = max_wait, result_ttl
        self.clock, self.wall_clock = clock, wall_clock
        self._condition = threading.Condition()
        self._jobs, self._requests, self._pending = {}, {}, deque()
        self._running, self._legacy, self._stopping, self._bytes = None, False, False, 0
        self._thread = threading.Thread(target=self._work, name='analysis-fifo', daemon=True)
        self._thread.start()

    def capabilities(self, enabled=True):
        return {'enabled': bool(enabled), 'maxWaiting': self.max_waiting,
                'maxPerClient': self.max_per_client,
                'maxWaitSeconds': self.max_wait, 'resultTtlSeconds': self.result_ttl,
                'pollIntervalSeconds': 2}

    def _release_payload(self, job):
        self._bytes -= job.size
        job.payload = job.run = job.finalize = None
        job.size = 0

    def _finish(self, job, status):
        job.status, job.finished, job.finished_wall = status, self.clock(), self.wall_clock()
        self._release_payload(job)

    def _housekeep(self):
        now = self.clock()
        for job in list(self._jobs.values()):
            if job.status == 'queued' and now - job.created >= self.max_wait:
                job.error, job.code, job.error_status = 'Время ожидания истекло. Повторите анализ.', 'analysis_expired', 408
                self._finish(job, 'expired')
            if job.status in TERMINAL and now - job.finished >= self.result_ttl:
                self._jobs.pop(job.id)
                self._requests.pop((job.owner, job.request), None)
        self._pending = deque(identifier for identifier in self._pending
                              if identifier in self._jobs and self._jobs[identifier].status == 'queued')

    def _snapshot(self, job):
        position = list(self._pending).index(job.id) + 1 if job.status == 'queued' else (
            0 if job.status in ('running', 'cancelling') else None)
        value = {'id': job.id, 'type': job.kind, 'status': job.status, 'position': position,
                 'createdAt': iso(job.created_wall), 'startedAt': iso(job.started_wall),
                 'finishedAt': iso(job.finished_wall),
                 'expiresAt': iso(job.finished_wall + self.result_ttl) if job.finished_wall is not None else None,
                 'cancelRequested': job.status in ('cancelled', 'cancelling')}
        if job.status == 'succeeded':
            value['result'] = job.result
        if job.error is not None:
            value.update(error=job.error, code=job.code, errorStatus=job.error_status)
        return value

    def submit(self, client, request, digest, kind, payload, size, run, finalize=None):
        client, request = owner(client), token(request)
        if kind not in ('photo', 'video') or type(size) is not int or size <= 0:
            raise ValueError('Некорректное задание анализа.')
        with self._condition:
            self._housekeep()
            existing = self._requests.get((client, request))
            if existing:
                job = self._jobs[existing]
                if job.digest is not None and not hmac.compare_digest(job.digest, digest):
                    raise RequestConflict()
                # A pre-submission cancellation tombstone binds the first digest.
                if job.digest is None:
                    job.digest, job.kind = digest, kind
                return self._snapshot(job)
            if (self._stopping or len(self._pending) >= self.max_waiting
                    or self._bytes + size > self.max_bytes
                    or sum(job.owner == client and job.status not in TERMINAL
                           for job in self._jobs.values()) >= self.max_per_client
                    or sum(job.owner == client and job.status in ('cancelled', 'cancelling')
                           for job in self._jobs.values()) >= self.max_tombstones_per_client):
                raise QueueFull()
            if len(self._jobs) >= self.max_jobs:
                raise QueueFull()
            job = Job(uuid.uuid4().hex, client, request, digest, kind, self.clock(), self.wall_clock(),
                      payload=payload, run=run, finalize=finalize, size=size)
            self._jobs[job.id], self._requests[(client, request)] = job, job.id
            self._pending.append(job.id)
            self._bytes += size
            self._condition.notify_all()
            return self._snapshot(job)

    def _owned(self, identifier, client):
        job = self._jobs.get(identifier)
        if job is None or not hmac.compare_digest(job.owner, client):
            raise JobNotFound()
        return job

    def get(self, identifier, client):
        client = owner(client)
        with self._condition:
            self._housekeep()
            return self._snapshot(self._owned(identifier, client))

    def _cancel(self, job):
        if job.status == 'queued':
            self._finish(job, 'cancelled')
            self._pending.remove(job.id)
        elif job.status == 'running':
            job.status = 'cancelling'
        self._condition.notify_all()
        return self._snapshot(job)

    def cancel(self, identifier, client):
        client = owner(client)
        with self._condition:
            self._housekeep()
            return self._cancel(self._owned(identifier, client))

    def cancel_request(self, request, client):
        client, request = owner(client), token(request)
        with self._condition:
            self._housekeep()
            identifier = self._requests.get((client, request))
            if identifier:
                return self._cancel(self._jobs[identifier])
            if (self._stopping
                    or sum(job.status in ('cancelled', 'cancelling') for job in self._jobs.values()) >= self.max_tombstones
                    or sum(job.owner == client and job.status in ('cancelled', 'cancelling') for job in self._jobs.values())
                    >= self.max_tombstones_per_client):
                raise QueueFull()
            if len(self._jobs) >= self.max_jobs:
                raise QueueFull()
            job = Job(uuid.uuid4().hex, client, request, None, None, self.clock(), self.wall_clock())
            self._jobs[job.id], self._requests[(client, request)] = job, job.id
            self._finish(job, 'cancelled')
            return self._snapshot(job)

    @contextmanager
    def legacy(self):
        with self._condition:
            self._housekeep()
            if self._stopping or self._legacy or self._running is not None or self._pending:
                raise vision.VisionBusy('Модель занята очередью осмотров. Повторите через несколько секунд.')
            self._legacy = True
        try:
            yield
        finally:
            with self._condition:
                self._legacy = False
                self._condition.notify_all()

    def _work(self):
        while True:
            with self._condition:
                self._housekeep()
                while not self._stopping and (self._legacy or not self._pending):
                    self._condition.wait(timeout=1)
                    self._housekeep()
                if self._stopping:
                    return
                job = self._jobs[self._pending.popleft()]
                job.status, job.started_wall, self._running = 'running', self.wall_clock(), job.id
                payload, run, finalize = job.payload, job.run, job.finalize
            result, error = None, None
            try:
                result = run(payload)
            except Exception as caught:
                error = caught
            with self._condition:
                if job.status == 'cancelling' or self._stopping:
                    self._finish(job, 'cancelled')
                elif isinstance(error, vision.VisionBusy):
                    # An earlier timed-out HTTP call may still hold the real GPU
                    # lock. Keep this job first in line; do not drain the queue.
                    job.status, job.started_wall = 'queued', None
                    self._pending.appendleft(job.id)
                elif error is not None:
                    log_failure(job, 'inference', error)
                    if isinstance(error, vision.VisionUnavailable):
                        job.error, job.code = error.public_message(job.kind), error.code
                    else:
                        job.error, job.code = 'Не удалось выполнить анализ. Повторите запрос позже.', 'analysis_failed'
                    job.error_status = 503
                    self._finish(job, 'failed')
                else:
                    try:
                        # Commit and cancellation are serialized: once success
                        # is committed, later cancellation cannot revoke it.
                        job.result = finalize(payload, result) if finalize else result
                        self._finish(job, 'succeeded')
                    except Exception as error:
                        log_failure(job, 'persistence', error)
                        job.error, job.code, job.error_status = (
                            'Не удалось сохранить результат анализа. Повторите запрос.', 'analysis_failed', 503)
                        self._finish(job, 'failed')
                self._running = None
                payload = run = finalize = result = error = None
                self._condition.notify_all()
                if job.status == 'queued':
                    self._condition.wait(timeout=2)

    def close(self, timeout=160):
        with self._condition:
            self._stopping = True
            for identifier in list(self._pending):
                self._cancel(self._jobs[identifier])
            if self._running:
                self._cancel(self._jobs[self._running])
            self._condition.notify_all()
        self._thread.join(timeout=timeout)
