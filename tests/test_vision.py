import base64
import json
import unittest
from io import BytesIO
from unittest.mock import patch
from urllib.error import URLError

from server import vision


OBSERVATION = {
    "component": "pipe",
    "condition": "corrosion",
    "box": [100, 200, 600, 800],
    "observation": "На фланце заметен бурый налёт.",
}


def response(data, *, thinking=None):
    message = {"content": json.dumps(data) if data is not None else ""}
    if thinking is not None:
        message["thinking"] = thinking
    return BytesIO(json.dumps({"message": message}).encode())


def multipart(*fields):
    boundary = "cyber102-test"
    body = b""
    for name, image in fields:
        headers = (f"--{boundary}\r\n"
                   f'Content-Disposition: form-data; name="{name}"; filename="frame.jpg"\r\n'
                   "Content-Type: image/jpeg\r\n\r\n")
        body += headers.encode() + image + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return f"multipart/form-data; boundary={boundary}", body


class VisionTest(unittest.TestCase):
    def test_preliminary_diagnosis_and_frame_coordinates(self):
        result = vision.result_from_observation(OBSERVATION)
        self.assertEqual(result["status"], "defect")
        self.assertEqual(result["source"], "local_vision")
        self.assertEqual(result["model"], vision.MODEL)
        self.assertIs(result["preliminary"], True)
        self.assertEqual(result["box"], {"x": 0.1, "y": 0.2, "width": 0.5, "height": 0.6})
        self.assertEqual(result["observation"], OBSERVATION["observation"])
        self.assertIn("не подтверждён", result["equipment"]["id"])
        self.assertIn("паспорт", result["serviceLife"])

    def test_clean_surface_does_not_claim_equipment_is_safe(self):
        result = vision.result_from_observation(OBSERVATION | {"condition": "no_visible_defect"})
        self.assertEqual(result["status"], "clear")
        self.assertIn("не подтверждает исправность", result["recommendation"])

    def test_unrecognized_or_uncertain_view_abstains(self):
        for fields in ({"component": "none"}, {"condition": "uncertain"}):
            with self.subTest(fields=fields):
                result = vision.result_from_observation(OBSERVATION | fields)
                self.assertEqual(result["status"], "unusable")
                self.assertNotIn("box", result)
                self.assertNotIn("recommendation", result)

    def test_invalid_output_cannot_produce_a_card(self):
        for fields in (
            {"component": "desk"}, {"condition": "operational"},
            {"observation": ["unsupported"]}, {"observation": " \n "}, {"observation": "\ud800"}, {"box": []},
            {"box": [True, 200, 600, 800]}, {"box": [100, 200, float("nan"), 800]},
            {"box": [100, 200, 10 ** 500, 800]}, {"box": [100, 200.5, 600, 800]},
            {"box": [100, 200, 1001, 800]}, {"box": [100, 200, 100, 800]},
        ):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                vision.result_from_observation(OBSERVATION | fields)

    def test_local_request_sends_only_the_frame_and_does_not_stream(self):
        image = b"sanitized JPEG bytes"
        with patch.object(vision, "build_opener") as build:
            opener = build.return_value
            opener.open.return_value = response(OBSERVATION)
            result = vision.analyze(image)
            request, = opener.open.call_args.args
            self.assertEqual(request.full_url, "http://127.0.0.1:11435/api/chat")
            self.assertEqual(request.get_method(), "POST")
            self.assertEqual(request.get_header("Content-type"), "application/json")
            payload = json.loads(request.data)
            self.assertIs(payload["stream"], False)
            self.assertEqual(payload["model"], vision.MODEL)
            self.assertEqual(payload["messages"][0]["images"], [base64.b64encode(image).decode()])
            self.assertEqual(build.call_args.args[0].proxies, {})
            redirect_handler = build.call_args.args[1]
            with self.assertRaises(URLError):
                redirect_handler.redirect_request(request, None, 307, "Redirect", {}, "https://elsewhere.example.test/")
        self.assertGreaterEqual(result["durationSeconds"], 0)

    def test_failure_releases_inference_slot(self):
        with patch.object(vision, "build_opener") as build:
            opener = build.return_value
            opener.open.side_effect = [URLError("unavailable"), BytesIO(b'{"message":{}}'), response(OBSERVATION)]
            with self.assertRaisesRegex(vision.VisionUnavailable, "не ответила"):
                vision.analyze(b"one")
            with self.assertRaisesRegex(vision.VisionUnavailable, "неоднозначный"):
                vision.analyze(b"two")
            self.assertEqual(vision.analyze(b"three")["status"], "defect")

    def test_constrained_json_is_accepted_when_content_is_empty(self):
        with patch.object(vision, "build_opener") as build:
            build.return_value.open.return_value = response(None, thinking=json.dumps(OBSERVATION))
            result = vision.analyze(b"frame")
        self.assertEqual(result["status"], "defect")
        self.assertEqual(result["box"], {"x": 0.1, "y": 0.2, "width": 0.5, "height": 0.6})
        self.assertEqual(result["observation"], OBSERVATION["observation"])
        self.assertNotIn("thinking", result)

    def test_regular_content_takes_precedence_over_secondary_output(self):
        with patch.object(vision, "build_opener") as build:
            build.return_value.open.return_value = response(
                OBSERVATION, thinking=json.dumps(OBSERVATION | {"condition": "no_visible_defect"}),
            )
            result = vision.analyze(b"frame")
        self.assertEqual(result["status"], "defect")

    def test_fallback_rejects_prose_wrong_schema_and_invalid_coordinates(self):
        for secondary in (
            "Inspection notes " + json.dumps(OBSERVATION),
            json.dumps(OBSERVATION | {"notes": "unexpected"}),
            json.dumps({name: value for name, value in OBSERVATION.items() if name != "observation"}),
            json.dumps(OBSERVATION | {"box": [100, 200, 1001, 800]}),
        ):
            with self.subTest(secondary=secondary), patch.object(vision, "build_opener") as build:
                build.return_value.open.return_value = response(None, thinking=secondary)
                with self.assertRaisesRegex(vision.VisionUnavailable, "неоднозначный"):
                    vision.analyze(b"frame")

    def test_busy_inference_does_not_send_second_frame(self):
        with patch.object(vision, "build_opener") as build:
            opener = build.return_value

            def open_first(*args, **kwargs):
                with self.assertRaisesRegex(vision.VisionUnavailable, "занята"):
                    vision.analyze(b"second frame")
                return response(OBSERVATION)

            opener.open.side_effect = open_first
            self.assertEqual(vision.analyze(b"first frame")["status"], "defect")
            opener.open.assert_called_once()

    def test_upload_requires_exactly_one_file_with_a_bounded_size(self):
        with self.assertRaisesRegex(ValueError, "multipart"):
            vision.extract_image("image/jpeg", b"data")
        for fields in ((("other", b"data"),), (("file", b"one"), ("file", b"two"))):
            with self.subTest(fields=fields), self.assertRaisesRegex(ValueError, "ровно одно"):
                vision.extract_image(*multipart(*fields))
        with patch.object(vision, "MAX_IMAGE_BYTES", 2), self.assertRaisesRegex(ValueError, "размер изображения"):
            vision.extract_image(*multipart(("file", b"123")))

    def test_upload_normalizes_jpeg_without_retaining_exif(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("requires the optional Pillow vision dependency")
        image = Image.new("RGB", (1400, 700), (21, 42, 64))
        metadata = Image.Exif()
        metadata[274] = 6  # rotate the display by 90 degrees
        metadata[270] = "test metadata"
        original = BytesIO()
        image.save(original, "JPEG", exif=metadata)
        frame = vision.extract_image(*multipart(("file", original.getvalue())))
        with Image.open(BytesIO(frame)) as normalized:
            self.assertEqual(normalized.size, (640, 1280))
            self.assertEqual(normalized.format, "JPEG")
            self.assertFalse(normalized.getexif())
        with self.assertRaisesRegex(ValueError, "не удалось прочитать"):
            vision.extract_image(*multipart(("file", b"not an image")))


if __name__ == "__main__":
    unittest.main()
