from collections import Counter
import json
from pathlib import Path
import unittest

from data_tools.import_customer_documents import (
    DOCUMENTS, EXPECTED_SHA256, digest, equipment_catalog, ocr_rows, page_record,
)


DATA = Path(__file__).resolve().parents[1] / "data/customer"


class CustomerDocumentExtractionTests(unittest.TestCase):
    def test_native_text_has_priority_over_ocr(self):
        page = page_record("x", 1, "embedded", {("x", 1): {"lines": ["ocr"]}})
        self.assertEqual(page, {"page": 1, "method": "pdf_text", "quality": "readable", "text": "embedded"})

    def test_ocr_quality_and_missing_text_are_distinct(self):
        rows = ocr_rows([
            '{"filename":"x-01.png","lines":["Только короткий текст"],"confidence":1}',
            '{"filename":"x-02.png","lines":["  "]}',
        ])
        self.assertEqual(page_record("x", 1, "", rows)["quality"], "limited")
        self.assertEqual(page_record("x", 2, "", rows)["method"], "unreadable")

    def test_ocr_cache_rejects_duplicate_page_keys(self):
        with self.assertRaisesRegex(ValueError, "Duplicate OCR result"):
            ocr_rows(['{"filename":"x-01.png"}', '{"filename":"x-1.png"}'])


class CustomerDocumentPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = json.loads((DATA / "documents.json").read_text(encoding="utf-8"))
        cls.passports = json.loads((DATA / "equipment-passports.json").read_text(encoding="utf-8"))
        cls.documents = {item["id"]: item for item in cls.index["documents"]}

    def test_index_has_expected_documents_and_page_counts(self):
        self.assertEqual(set(self.documents), {row[0] for row in DOCUMENTS})
        self.assertEqual(self.index["summary"]["documents"], 22)
        self.assertEqual(self.index["summary"]["pages"], 218)
        methods = Counter()
        for identifier, item in self.documents.items():
            self.assertRegex(identifier, r"^[a-z0-9][a-z0-9-]{0,79}$")
            self.assertEqual(item["sha256"], EXPECTED_SHA256[identifier])
            self.assertEqual(item["relativePath"], f"documents/{item['sha256']}.pdf")
            self.assertEqual(item["url"], f"/api/customer/documents/{identifier}/pdf")
            self.assertEqual([page["page"] for page in item["pages"]], list(range(1, item["pageCount"] + 1)))
            for page in item["pages"]:
                methods[page["method"]] += 1
                self.assertEqual(bool(page["text"]), page["method"] != "unreadable")
        self.assertEqual(methods, {"ocr": 201, "pdf_text": 8, "unreadable": 9})
        self.assertEqual(self.index["summary"]["extractionMethods"], methods)

    def test_catalog_keeps_verified_source_scope(self):
        self.assertEqual(self.passports, equipment_catalog())
        self.assertEqual(self.passports["equipmentType"], "progressive_cavity")
        self.assertEqual(self.passports["applicableDiagnostics"], "not_validated")
        equipment = self.passports["equipment"]
        self.assertEqual(Counter(item["kind"] for item in equipment), {
            "pump": 4, "motor": 4, "gearbox": 4, "temperature_transmitter": 4,
        })
        self.assertEqual(len({item["serial"] for item in equipment}), 16)
        self.assertNotIn("P-102", {item["serial"] for item in equipment})
        for item in equipment:
            for reference in item["sources"]:
                self.assert_source(reference)
                self.assertIn(reference["field"], item["fields"])
            if item["kind"] == "pump":
                self.assertEqual(item["serial"], f"21031004-{item['groupNumber']}")
                self.assertEqual(item["fields"]["design_resource_before_writeoff"]["value"], 11000)
                self.assertNotIn("remaining_resource", item["fields"])
        for requirement in self.passports["requirements"]:
            self.assertTrue(requirement["appliesTo"])
            for reference in requirement["sources"]:
                self.assert_source(reference)

    def test_assigned_limits_match_each_motor_passport(self):
        pages_by_serial = {
            "230201068": ("drive-1-passports", 2), "230201069": ("drive-2-passports", 2),
            "230201067": ("drive-3-passports", 4), "230201070": ("drive-4-passports", 2),
        }
        for item in self.passports["equipment"]:
            for key, value, unit in (("assigned_resource", 60000, "ч"), ("assigned_lifetime", 20, "лет")):
                if item["kind"] != "motor":
                    self.assertNotIn(key, item["fields"])
                    continue
                with self.subTest(serial=item["serial"], field=key):
                    field = item["fields"][key]
                    self.assertEqual((field["value"], field["unit"]), (value, unit))
                    source = field["source"]
                    self.assertEqual((source["documentId"], source["page"]), pages_by_serial[item["serial"]])

    def test_originals_when_present_have_matching_bytes_and_hash(self):
        raw = DATA / "documents"
        if not raw.is_dir():
            self.skipTest("Organizer originals are distributed outside Git")
        for item in self.documents.values():
            path = DATA / item["relativePath"]
            self.assertTrue(path.is_file(), item["id"])
            self.assertEqual(path.stat().st_size, item["bytes"])
            self.assertEqual(digest(path), item["sha256"])

    def assert_source(self, reference):
        document = self.documents[reference["documentId"]]
        self.assertIn(reference["page"], range(1, document["pageCount"] + 1))
        self.assertTrue(reference["quote"])


if __name__ == "__main__":
    unittest.main()
