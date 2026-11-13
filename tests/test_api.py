import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from http.client import HTTPConnection
from pathlib import Path

from server.app import BoundedHTTPServer, Handler, initialize


class ApiTest(unittest.TestCase):
    serial = "P-102"
    public_url = "https://cyber102.example.test"

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        database = Path(directory.name) / "test.sqlite3"
        initialize(database)
        handler = partial(Handler, database=database, public_url=self.public_url)
        self.server = BoundedHTTPServer(("127.0.0.1", 0), handler)
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


if __name__ == "__main__":
    unittest.main()
