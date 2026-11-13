import json
import socket
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from http.client import HTTPConnection
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from server.app import BoundedHTTPServer, Handler, initialize
from server import vision


class ApiTest(unittest.TestCase):
    serial = "P-102"
    public_url = "https://cyber102.example.test"

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        database = Path(directory.name) / "test.sqlite3"
        initialize(database)
        self.handler = partial(Handler, database=database, public_url=self.public_url)
        self.server = BoundedHTTPServer(("127.0.0.1", 0), self.handler)
        self.addCleanup(self.server.server_close)
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        self.addCleanup(thread.join, 3)
        self.addCleanup(self.server.shutdown)
        self.local_origin = f"http://127.0.0.1:{self.server.server_port}"

    def request(self, method, path, body=None, headers=None):
        request_headers = dict(headers or {})
        if isinstance(body, dict):
            body = json.dumps(body).encode()
            request_headers.setdefault("Content-Type", "application/json")
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            connection.request(method, path, body, request_headers)
            response = connection.getresponse()
            return response.status, response.headers, response.read()
        finally:
            connection.close()

    def json_request(self, method, path, expected_status=200, **kwargs):
        status, headers, body = self.request(method, path, **kwargs)
        self.assertEqual(status, expected_status, body.decode(errors="replace"))
        self.assertEqual(headers.get_content_type(), "application/json")
        self.assertEqual(headers["Cache-Control"], "no-store")
        payload = json.loads(body)
        if status >= 400:
            self.assertTrue(payload["error"])
        return payload

    def test_health_and_diagnosis_by_scenario(self):
        health = self.json_request("GET", "/api/health")
        self.assertEqual(health["status"], "ok")
        scenarios = self.json_request("GET", "/api/scenarios?serial=P-102")
        self.assertEqual(scenarios["passport"]["serial"], self.serial)
        self.assertIn("coupling_guard", [scenario["id"] for scenario in scenarios["scenarios"]])
        diagnosis = self.json_request("GET", "/api/diagnose?serial=P-102&scenario=coupling_guard")
        self.assertEqual(diagnosis["source"], "demo")
        self.assertEqual(diagnosis["passport"]["serial"], self.serial)
        self.assertEqual([finding["id"] for finding in diagnosis["findings"]], ["coupling_guard"])
        self.assertIn("guard", diagnosis["zones"])

    def test_post_diagnosis_uses_submitted_signals_and_does_not_save_history(self):
        diagnosis = self.json_request("POST", "/api/diagnose", body={
            "serial": self.serial,
            "telemetry": {"discharge_pressure": 0.8},
            "vision": {"gauge_pressure": 0.96},
        })
        self.assertEqual(diagnosis["source"], "submitted")
        self.assertEqual([finding["id"] for finding in diagnosis["findings"]], ["gauge_mismatch"])
        self.assertEqual(self.json_request("GET", "/api/history?serial=P-102"), {"records": []})

    def test_invalid_diagnosis_payloads(self):
        for body, content_type, status in (
            (b"{", "application/json", 400),
            (b'{"serial":"P-102","telemetry":{"hours":NaN},"vision":{}}', "application/json", 400),
            (b'{"serial":"P-102","telemetry":{"hours":1e309},"vision":{}}', "application/json", 400),
            (br'{"serial":"\ud800","telemetry":{},"vision":{}}', "application/json", 400),
            ({"serial": self.serial, "telemetry": [], "vision": {}}, "application/json", 400),
            (b"{}", "text/plain", 415),
            (b" " * (32 * 1024 + 1), "application/json", 413),
        ):
            with self.subTest(status=status, content_type=content_type, length=len(body)):
                self.json_request("POST", "/api/diagnose", status, body=body,
                                  headers={"Content-Type": content_type})

    def test_history_create_retry_conflict_and_listing(self):
        body = {"serial": self.serial, "scenarioId": "seal_leak",
                "note": "Проверка уплотнения", "requestId": "inspection-1"}
        created = self.json_request("POST", "/api/history", 201, body=body)
        self.assertIs(created["created"], True)
        record = created["record"]
        self.assertEqual(record["serial"], self.serial)
        self.assertEqual(record["scenarioId"], body["scenarioId"])
        self.assertEqual(record["note"], body["note"])
        self.assertEqual(record["requestId"], body["requestId"])
        self.assertIs(record["vision"]["seal_leak"], True)
        self.assertIn("seal_leak", [finding["id"] for finding in record["findings"]])
        self.assertEqual(self.json_request("POST", "/api/history", body=body),
                         {"created": False, "record": record})
        self.json_request("POST", "/api/history", 409, body=body | {"note": "Другая заметка"})
        self.assertEqual(self.json_request("GET", "/api/history?serial=P-102"), {"records": [record]})

    def test_concurrent_history_retry_returns_single_record(self):
        body = {"serial": self.serial, "scenarioId": "healthy", "requestId": "same-request"}

        def send_request(_):
            status, _, response = self.request("POST", "/api/history", body=body)
            return status, json.loads(response)

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(send_request, range(2)))
        self.assertEqual(sorted(status for status, _ in responses), [200, 201])
        records = [payload["record"] for _, payload in responses]
        self.assertEqual(records[0], records[1])
        self.assertEqual(self.json_request("GET", "/api/history?serial=P-102"), {"records": records[:1]})

    def test_invalid_history_fields_and_unknown_pump_or_scenario(self):
        valid = {"serial": self.serial, "scenarioId": "healthy"}
        for fields in ({"scenarioId": []}, {"note": 8}, {"note": "я" * 1001},
                       {"note": "\ud800"}, {"requestId": ""}, {"requestId": ["id"]}):
            with self.subTest(fields=tuple(fields)):
                self.json_request("POST", "/api/history", 400, body=valid | fields)
        self.json_request("POST", "/api/history", 404, body=valid | {"scenarioId": "missing"})
        self.json_request("POST", "/api/history", 404, body=valid | {"serial": "unknown"})
        self.json_request("GET", "/api/history?serial=unknown", 404)
        self.json_request("GET", "/api/scenarios?serial=unknown", 404)
        self.json_request("POST", "/api/diagnose", 404, body={
            "serial": "unknown", "telemetry": {}, "vision": {},
        })
        self.assertEqual(self.json_request("GET", "/api/history?serial=P-102"), {"records": []})

    def test_origin_restriction_rejects_cross_origin_without_writing(self):
        valid = {"serial": self.serial, "scenarioId": "healthy"}
        for origin in ("null", "https://elsewhere.example.test", f"{self.public_url}.invalid"):
            with self.subTest(origin=origin):
                self.json_request("POST", "/api/history", 403, body=valid, headers={"Origin": origin})
        self.assertEqual(self.json_request("GET", "/api/history?serial=P-102"), {"records": []})
        for origin in (self.public_url, self.local_origin):
            with self.subTest(origin=origin):
                self.json_request("POST", "/api/history", 201, body=valid, headers={"Origin": origin})
        self.assertEqual(len(self.json_request("GET", "/api/history?serial=P-102")["records"]), 2)

    def test_frame_analysis_disabled_by_default(self):
        with patch.object(vision, "analyze") as analyze:
            self.json_request("POST", "/analyze", 503, body=b"frame")
            analyze.assert_not_called()

    def test_frame_analysis_rejects_invalid_origin_and_upload(self):
        self.handler.keywords["vision_enabled"] = True
        with patch.object(vision, "analyze") as analyze:
            for status, headers in (
                (403, {"Origin": "https://elsewhere.example.test"}),
                (403, {"Origin": ""}),
                (400, {"Content-Type": "multipart/form-data; boundary=missing"}),
                (400, {"Content-Type": "image/jpeg"}),
                (400, {"Transfer-Encoding": "chunked", "Content-Length": "5"}),
                (400, {"Content-Length": "+5"}),
                (413, {"Content-Length": str(vision.MAX_IMAGE_BYTES + 8193)}),
            ):
                with self.subTest(headers=headers):
                    self.json_request("POST", "/analyze", status, body=b"frame", headers=headers)
            analyze.assert_not_called()

    def test_short_upload_is_rejected_before_image_decoding(self):
        self.handler.keywords["vision_enabled"] = True
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            with patch.object(vision, "extract_image") as decode:
                connection.request("POST", "/analyze", b"frame", {"Content-Length": "30"})
                connection.sock.shutdown(socket.SHUT_WR)
                response = connection.getresponse()
                self.assertEqual(response.status, 400)
                self.assertIn("не полностью", json.loads(response.read())["error"])
                decode.assert_not_called()
        finally:
            connection.close()

    def test_both_backends_keep_the_frame_analysis_contract(self):
        self.handler.keywords["vision_enabled"] = True
        observation = {"component": "pump", "condition": "corrosion",
                       "box": [100, 100, 900, 800], "observation": "Следы коррозии на корпусе."}
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("requires the optional Pillow vision dependency")
        jpeg = BytesIO()
        Image.new("RGB", (32, 32), "red").save(jpeg, "JPEG")
        body = (b'--frame\r\nContent-Disposition: form-data; name="file"; filename="frame.jpg"\r\n'
                b'Content-Type: image/jpeg\r\n\r\n' + jpeg.getvalue() + b'\r\n--frame--\r\n')
        headers = {"Content-Type": "multipart/form-data; boundary=frame", "Origin": self.public_url}
        for backend, model, envelope in (
            ("ollama", vision.MODEL, {"message": {"content": json.dumps(observation)}}),
            ("internvl3", vision.INTERNVL_MODEL, {"model": vision.INTERNVL_MODEL, "observation": observation}),
        ):
            with self.subTest(backend=backend), patch.object(vision, "build_opener") as build:
                self.handler.keywords["vision_backend"] = backend
                build.return_value.open.return_value = BytesIO(json.dumps(envelope).encode())
                card = self.json_request("POST", "/analyze", body=body, headers=headers)
                self.assertGreaterEqual(card.pop("durationSeconds"), 0)
                self.assertEqual(card, vision.result_from_observation(observation, model=model))
                build.return_value.open.side_effect = TimeoutError("worker timed out")
                self.json_request("POST", "/analyze", 503, body=body, headers=headers)


if __name__ == "__main__":
    unittest.main()
