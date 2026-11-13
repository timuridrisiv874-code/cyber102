import base64
import json
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from vision_runtime import worker


OBSERVATION = {
    "component": "pump",
    "condition": "corrosion",
    "box": [200, 300, 800, 900],
    "observation": "На корпусе заметны следы коррозии.",
}


def encoded_image(image_format="JPEG", size=(32, 16)):
    from PIL import Image

    image = BytesIO()
    Image.new("L", size).save(image, image_format)
    return base64.b64encode(image.getvalue()).decode("ascii")


class RuntimeValidationTest(unittest.TestCase):
    def test_accepts_exact_json_and_optional_single_fence(self):
        document = json.dumps(OBSERVATION, ensure_ascii=False)
        for output in (document, f"\n```json\n{document}\n```\n", f"```\n{document}\n```"):
            with self.subTest(output=output):
                self.assertEqual(worker.parse_observation(output), OBSERVATION)
        abstention = {"component": "none", "condition": "uncertain", "box": [0, 0, 0, 0], "observation": ""}
        self.assertEqual(worker.parse_observation(json.dumps(abstention)), abstention)

    def test_rejects_prose_invalid_fields_and_trailing_content(self):
        document = json.dumps(OBSERVATION)
        for output in (
            "{", "null", "[]", f"Result: {document}", f"{document} trailing text",
            f"```json\n{document}\n```\nSecond paragraph",
            json.dumps(OBSERVATION | {"analysis": "unexpected field"}),
            json.dumps({key: value for key, value in OBSERVATION.items() if key != "observation"}),
            json.dumps(OBSERVATION | {"box": [0, 0, 1001, 800]}),
            json.dumps(OBSERVATION | {"condition": "operational"}),
        ):
            with self.subTest(output=output), self.assertRaises(ValueError):
                worker.parse_observation(output)

    def test_image_rejects_urls_extra_fields_and_invalid_base64(self):
        for payload in (
            {"image": "https://example.invalid/frame.jpg"},
            {"image": "data:image/jpeg;base64,YWJj"},
            {"image": "file:///tmp/frame.jpg"},
            {"image": "YWJj", "prompt": "override"},
            {"image": "YW Jj"}, {"image": 1}, [], {},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                worker.read_image(payload)

    def test_image_checks_encoded_decoded_format_and_pixel_limits(self):
        with patch.object(worker, "MAX_BODY", 8), self.assertRaisesRegex(ValueError, "too large"):
            worker.read_image({"image": "A" * 12})
        with patch.object(worker, "MAX_IMAGE_BYTES", 2), self.assertRaisesRegex(ValueError, "Invalid image size"):
            worker.read_image({"image": "YWJj"})
        for image in (encoded_image("GIF"), encoded_image("PNG", size=(3000, 3000))):
            with self.assertRaisesRegex(ValueError, "8 megapixels"):
                worker.read_image({"image": image})
        with worker.read_image({"image": encoded_image()}) as image:
            self.assertEqual((image.mode, image.size), ("RGB", (32, 16)))

    def test_network_guard_allows_local_addresses_and_rejects_external_dns_and_connect(self):
        for host in ("127.0.0.1", "::1"):
            with self.subTest(host=host):
                worker.deny_external_connections("socket.connect", (None, (host, 11436)))
        for host in ("localhost", "127.0.0.1", "::1", None):
            with self.subTest(host=host):
                worker.deny_external_connections("socket.getaddrinfo", (host,))
        for event, arguments in (
            ("socket.connect", (None, ("192.0.2.1", 443))),
            ("socket.connect", (None, ("2001:db8::1", 443, 0, 0))),
            ("socket.connect", (None, "/tmp/worker.sock")),
            ("socket.getaddrinfo", ("example.invalid",)),
        ):
            with self.subTest(event=event, arguments=arguments), self.assertRaises(OSError):
                worker.deny_external_connections(event, arguments)

    def test_startup_installs_offline_guard_before_engine_load(self):
        offline_keys = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY", "DO_NOT_TRACK")

        with (patch.dict(os.environ, {key: "0" for key in offline_keys}),
              patch.object(worker.sys, "argv", ["worker", "--port", "12000", "--model-directory", "model"]),
              patch.object(worker.sys, "addaudithook") as audit,
              patch.object(worker, "Engine") as engine,
              patch.object(worker, "Server") as server,
              patch.object(worker, "print", create=True)):
            def load(directory):
                self.assertEqual(directory, Path("model"))
                audit.assert_called_once_with(worker.deny_external_connections)
                self.assertEqual([os.environ[key] for key in offline_keys], ["1"] * len(offline_keys))
                return engine.return_value

            engine.return_value.device = "fake GPU"
            engine.side_effect = load
            worker.main()
        server.assert_called_once_with(("127.0.0.1", 12000), engine.return_value)
        server.return_value.__enter__.return_value.serve_forever.assert_called_once()


class FakeEngine:
    device = "fake GPU"

    def __init__(self):
        self.lock = threading.Lock()
        self.started = threading.Event()
        self.finish = threading.Event()
        self.finish.set()
        self.images = []

    def infer(self, image):
        self.images.append((image.mode, image.size))
        self.started.set()
        if not self.finish.wait(3):
            raise RuntimeError("Test did not release inference")
        return OBSERVATION


class RuntimeHTTPTest(unittest.TestCase):
    def setUp(self):
        self.engine = FakeEngine()
        self.server = worker.Server(("127.0.0.1", 0), self.engine)
        self.addCleanup(self.server.server_close)
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        self.addCleanup(thread.join, 3)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, payload=None, headers=None, expected=200):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=4)
        request_headers = {"Content-Type": "application/json", **(headers or {})}
        try:
            connection.request(method, path, json.dumps(payload).encode() if payload is not None else None, request_headers)
            response = connection.getresponse()
            data = json.loads(response.read())
            self.assertEqual(response.status, expected, data)
            self.assertEqual(response.getheader("Cache-Control"), "no-store")
            return data
        finally:
            connection.close()

    def test_analyze_and_health_with_no_model_loaded(self):
        health = self.request("GET", "/health")
        self.assertEqual((health["model"], health["revision"], health["device"]),
                         (worker.MODEL, worker.REVISION, self.engine.device))
        self.assertIs(health["offline"], True)
        self.assertIs(health["busy"], False)
        payload = {"image": encoded_image()}
        data = self.request("POST", "/analyze", payload)
        self.assertEqual(data["observation"], OBSERVATION)
        self.assertEqual(data["model"], worker.MODEL)
        self.assertGreaterEqual(data["durationSeconds"], 0)
        self.assertEqual(self.engine.images, [("RGB", (32, 16))])

    def test_bad_uploads_and_browser_origin_do_not_reach_engine(self):
        payload = {"image": encoded_image()}
        for headers, status in (
            ({"Origin": "http://127.0.0.1"}, 400),
            ({"Content-Type": "text/plain"}, 400),
            ({"Content-Length": str(worker.MAX_BODY + 1)}, 413),
            ({"Transfer-Encoding": "chunked", "Content-Length": "2"}, 400),
        ):
            with self.subTest(headers=headers):
                self.assertIn("error", self.request("POST", "/analyze", payload, headers, expected=status))
        self.assertIn("error", self.request("POST", "/analyze", {"image": "https://example.invalid/pump.jpg"}, expected=400))
        self.assertEqual(self.engine.images, [])

    def test_busy_rejects_second_request_then_accepts_next_frame(self):
        payload = {"image": encoded_image()}
        self.engine.finish.clear()
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(self.request, "POST", "/analyze", payload)
            try:
                self.assertTrue(self.engine.started.wait(2))
                self.assertIs(self.request("GET", "/health")["busy"], True)
                self.assertIn("busy", self.request("POST", "/analyze", payload, expected=503)["error"])
                self.assertEqual(len(self.engine.images), 1)
            finally:
                self.engine.finish.set()
            self.assertEqual(first.result(timeout=4)["model"], worker.MODEL)
        self.assertTrue(self.engine.lock.acquire(timeout=2))
        self.engine.lock.release()
        self.assertIs(self.request("GET", "/health")["busy"], False)
        self.assertEqual(self.request("POST", "/analyze", payload)["observation"], OBSERVATION)
        self.assertEqual(len(self.engine.images), 2)


if __name__ == "__main__":
    unittest.main()
