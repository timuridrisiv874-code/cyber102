import threading
import time
import unittest
import uuid
import weakref
from unittest.mock import Mock

from server.analysis_queue import AnalysisQueue, JobNotFound, QueueFull, RequestConflict
from server import vision


def identity():
    return uuid.uuid4().hex


class QueueTest(unittest.TestCase):
    def queue(self, **options):
        queue = AnalysisQueue(**options)
        self.addCleanup(queue.close)
        return queue

    def wait(self, queue, job, client, status):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            state = queue.get(job['id'], client)
            if state['status'] == status:
                return state
            time.sleep(.005)
        self.fail(f'Expected {status}, got {state}')

    def test_mixed_fifo_single_execution_and_legacy_cannot_overtake(self):
        queue = self.queue(max_waiting=2)
        gate, entered = threading.Event(), threading.Event()
        self.addCleanup(gate.set)
        order, active = [], []
        def run(value):
            active.append(value)
            self.assertEqual(len(active), 1)
            order.append(value)
            if value == 'video':
                entered.set(); gate.wait(3)
            active.pop()
            return {'value': value}
        client = identity()
        first = queue.submit(client, identity(), 'a', 'video', 'video', 1, run)
        self.assertTrue(entered.wait(1))
        second = queue.submit(client, identity(), 'b', 'photo', 'photo', 1, run)
        third_client = identity()
        third = queue.submit(third_client, identity(), 'c', 'video', 'last', 1, run)
        self.assertEqual([second['position'], third['position']], [1, 2])
        with self.assertRaises(QueueFull):
            queue.submit(identity(), identity(), 'd', 'photo', 'overflow', 1, run)
        with self.assertRaises(vision.VisionBusy), queue.legacy():
            pass
        gate.set()
        self.wait(queue, third, third_client, 'succeeded')
        self.assertEqual(order, ['video', 'photo', 'last'])
        self.assertEqual(queue.get(first['id'], client)['result'], {'value': 'video'})
        self.assertEqual(queue._bytes, 0)
        with queue.legacy():
            fourth = queue.submit(client, identity(), 'e', 'photo', 'after legacy', 1, run)
            self.assertEqual(queue.get(fourth['id'], client)['status'], 'queued')
        self.wait(queue, fourth, client, 'succeeded')

    def test_running_cancel_holds_slot_and_does_not_commit(self):
        queue = self.queue()
        gate, entered = threading.Event(), threading.Event()
        self.addCleanup(gate.set)
        finalize, next_run = Mock(), Mock(return_value={'ok': True})
        def slow(_):
            entered.set(); gate.wait(3); return {'ok': True}
        client = identity()
        first = queue.submit(client, identity(), 'a', 'photo', object(), 5, slow, finalize)
        self.assertTrue(entered.wait(1))
        second = queue.submit(client, identity(), 'b', 'video', object(), 5, next_run)
        self.assertEqual(queue.cancel(first['id'], client)['status'], 'cancelling')
        self.assertEqual(queue.get(second['id'], client)['status'], 'queued')
        next_run.assert_not_called()
        self.assertEqual(queue._bytes, 10)
        gate.set()
        self.wait(queue, first, client, 'cancelled')
        self.wait(queue, second, client, 'succeeded')
        finalize.assert_not_called()
        self.assertEqual(queue._bytes, 0)

    def test_queued_cancel_releases_payload_and_cross_client_jobs_are_hidden(self):
        class Payload:
            pass
        queue = self.queue()
        client = identity()
        with queue.legacy():
            payload = Payload(); reference = weakref.ref(payload)
            run = Mock()
            job = queue.submit(client, identity(), 'a', 'photo', payload, 5, run)
            del payload
            self.assertIsNotNone(reference())
            for method in (queue.get, queue.cancel):
                with self.assertRaises(JobNotFound):
                    method(job['id'], identity())
            self.assertEqual(queue.cancel(job['id'], client)['status'], 'cancelled')
            self.assertIsNone(reference())
            self.assertEqual(queue._bytes, 0)
        run.assert_not_called()

    def test_idempotency_fairness_and_bytes_do_not_count_retries(self):
        queue = self.queue(max_bytes=10)
        client, request = identity(), identity()
        with queue.legacy():
            first = queue.submit(client, request, 'a', 'photo', b'one', 5, Mock())
            retry = queue.submit(client, request, 'a', 'photo', b'one', 5, Mock())
            self.assertEqual(first['id'], retry['id'])
            self.assertEqual(queue._bytes, 5)
            with self.assertRaises(RequestConflict):
                queue.submit(client, request, 'different', 'photo', b'two', 5, Mock())
            queue.submit(client, identity(), 'b', 'video', b'two', 5, Mock())
            with self.assertRaises(QueueFull):
                queue.submit(client, identity(), 'c', 'photo', b'three', 1, Mock())
            with self.assertRaises(QueueFull):
                queue.submit(identity(), identity(), 'd', 'photo', b'three', 1, Mock())
            queue.cancel(first['id'], client)
            accepted = queue.submit(identity(), identity(), 'e', 'photo', b'three', 1, Mock(return_value={}))
            self.assertEqual(accepted['status'], 'queued')

    def test_pre_submission_cancel_tombstone_ttl_and_bounded_results(self):
        now = [0.0]
        queue = self.queue(max_jobs=2, result_ttl=10, clock=lambda: now[0])
        client, request = identity(), identity()
        tombstone = queue.cancel_request(request, client)
        run = Mock()
        same = queue.submit(client, request, 'a', 'photo', b'one', 1, run)
        self.assertEqual((same['id'], same['status']), (tombstone['id'], 'cancelled'))
        with self.assertRaises(RequestConflict):
            queue.submit(client, request, 'different', 'photo', b'one', 1, run)
        queue.cancel_request(identity(), identity())
        with self.assertRaises(QueueFull):
            queue.cancel_request(identity(), identity())
        now[0] = 11
        with self.assertRaises(JobNotFound):
            queue.get(tombstone['id'], client)
        queue.cancel_request(identity(), identity())
        run.assert_not_called()

    def test_wait_expiration_releases_image_and_keeps_readable_terminal_state(self):
        now = [0.0]
        queue = self.queue(max_wait=10, clock=lambda: now[0])
        client, run = identity(), Mock()
        with queue.legacy():
            job = queue.submit(client, identity(), 'a', 'video', b'image bytes', 11, run)
            now[0] = 11
            expired = queue.get(job['id'], client)
            self.assertEqual((expired['status'], expired['code']), ('expired', 'analysis_expired'))
            self.assertEqual(queue._bytes, 0)
        run.assert_not_called()

    def test_downstream_busy_retries_same_head_without_leaking_unknown_errors(self):
        queue = self.queue()
        client, entered = identity(), threading.Event()
        calls = []
        def run(_):
            calls.append('head')
            if len(calls) == 1:
                entered.set()
                raise vision.VisionBusy('still generating previous timed-out request')
            raise RuntimeError('secret internal traceback text')
        job = queue.submit(client, identity(), 'a', 'photo', b'a', 1, run)
        self.assertTrue(entered.wait(1))
        second = queue.submit(client, identity(), 'b', 'video', b'b', 1,
                              lambda _: calls.append('tail') or {})
        self.wait(queue, second, client, 'succeeded')
        self.assertEqual(calls, ['head', 'head', 'tail'])
        failed = queue.get(job['id'], client)
        self.assertEqual(failed['status'], 'failed')
        self.assertNotIn('secret', str(failed))

    def test_public_failure_message_and_private_exception_classification(self):
        queue = self.queue()
        client = identity()
        for error, code, message in [
            (vision.VisionUnavailable('credential=secret'), 'vision_unavailable', 'Сервис анализа временно недоступен. Повторите разбор этого видео позже.'),
            (vision.VisionInvalidResponse('secret bytes'), 'vision_invalid_response', 'Модель вернула несогласованные наблюдения. Повторите разбор видео.'),
            (RuntimeError('secret payload'), 'analysis_failed', 'Не удалось выполнить анализ. Повторите запрос позже.'),
        ]:
            with self.subTest(code=code):
                def fail(_):
                    raise error from ConnectionRefusedError('secret address')
                with self.assertLogs('server.analysis_queue', 'WARNING') as logs:
                    job = queue.submit(client, identity(), 'a', 'video', b'sensitive image', 15, fail)
                    failed = self.wait(queue, job, client, 'failed')
                self.assertEqual((failed['code'], failed['error'], failed['errorStatus']), (code, message, 503))
                self.assertIn(job['id'], logs.output[0])
                self.assertIn(type(error).__name__, logs.output[0])
                self.assertIn('ConnectionRefusedError', logs.output[0])
                self.assertNotIn('secret', str([failed, logs.output]))
                self.assertNotIn('sensitive image', str([failed, logs.output]))

    def test_close_cancels_pending_and_drops_running_result(self):
        queue = self.queue()
        gate, entered = threading.Event(), threading.Event()
        self.addCleanup(gate.set)
        commit = Mock()
        def run(_):
            entered.set(); gate.wait(3); return {}
        client = identity()
        first = queue.submit(client, identity(), 'a', 'photo', b'a', 1, run, commit)
        self.assertTrue(entered.wait(1))
        second = queue.submit(client, identity(), 'b', 'video', b'b', 1, Mock())
        queue.close(timeout=0)
        self.assertEqual(queue.get(second['id'], client)['status'], 'cancelled')
        self.assertEqual(queue.get(first['id'], client)['status'], 'cancelling')
        gate.set(); queue.close(timeout=2)
        self.assertFalse(queue._thread.is_alive())
        self.assertEqual(queue._bytes, 0)
        commit.assert_not_called()

    def test_hundred_fast_attempts_preserve_idempotency_and_obey_cache_bound(self):
        now = [0.0]
        queue = self.queue(clock=lambda: now[0])
        client, first, first_request, rejected = identity(), None, None, 0
        for index in range(100):
            request = identity()
            try:
                job = queue.submit(client, request, str(index), 'photo', b'x', 1, lambda _: {'ok': True})
            except QueueFull:
                rejected += 1
                continue
            if first is None:
                first, first_request = job, request
            self.wait(queue, job, client, 'succeeded')
        self.assertEqual((len(queue._jobs), rejected), (64, 36))
        self.assertEqual(queue._bytes, 0)
        retry = queue.submit(client, first_request, '0', 'photo', b'x', 1, Mock())
        self.assertEqual((retry['id'], retry['status']), (first['id'], 'succeeded'))
        now[0] = 301
        last = queue.submit(identity(), identity(), 'last', 'video', b'x', 1, lambda _: {})
        self.assertIn(last['status'], ('queued', 'running'))

    def test_cancel_storm_has_per_client_and_global_tombstone_limits(self):
        queue = self.queue()
        client = identity()
        for _ in range(8):
            request = identity()
            queue.cancel_request(request, client)
            # Binding a late POST must not remove the record from cancel quota.
            self.assertEqual(queue.submit(client, request, 'a', 'photo', b'x', 1, Mock())['status'], 'cancelled')
        for _ in range(100):
            with self.assertRaises(QueueFull):
                queue.cancel_request(identity(), client)
        for _ in range(8):
            queue.cancel_request(identity(), identity())
        with self.assertRaises(QueueFull):
            queue.cancel_request(identity(), identity())
        job_client = identity()
        job = queue.submit(job_client, identity(), 'legitimate', 'photo', b'x', 1, lambda _: {})
        self.wait(queue, job, job_client, 'succeeded')


if __name__ == '__main__':
    unittest.main()
