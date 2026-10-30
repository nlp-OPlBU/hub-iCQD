"""离线验证：python -m unittest discover -s . -p test_frontend.py -v"""

import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests
from PIL import Image
from streamlit.testing.v1 import AppTest

from frontend import MAX_SIZE, analyze_image, validate_image, validate_url


def sample_image():
    buffer = BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def response(payload=None, status=200):
    value = Mock()
    value.status_code = status
    value.ok = status < 400
    value.json.return_value = payload or {
        "result": {"classification": "低风险", "description": "楼道内有少量堆物。"}
    }
    value.__enter__ = Mock(return_value=value)
    value.__exit__ = Mock(return_value=False)
    return value


class FrontendTests(unittest.TestCase):
    def test_input_validation(self):
        self.assertEqual(validate_image(sample_image()), "image/png")
        for content in (b"", b"not an image", b"x" * (MAX_SIZE + 1)):
            with self.subTest(content_length=len(content)), self.assertRaises(ValueError):
                validate_image(content)
        for url in ("", "file:///photo.jpg", "https://u:p@example.com/a", "http://[bad", "http://host:bad"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_url(url)

    @patch("requests.post")
    def test_file_request_matches_backend(self, post):
        post.return_value = response()
        content = sample_image()
        result = analyze_image("http://127.0.0.1:8000/", content=content, filename="a.png")
        self.assertEqual(result["classification"], "低风险")
        post.assert_called_once_with(
            "http://127.0.0.1:8000/analyze_image",
            timeout=(10, 120),
            files={"file": ("a.png", content, "image/png")},
        )

    @patch("requests.post")
    def test_url_request_matches_backend(self, post):
        post.return_value = response()
        analyze_image("http://localhost:8000", image_url=" https://example.com/a.png ")
        post.assert_called_once_with(
            "http://localhost:8000/analyze_image",
            timeout=(10, 120),
            data={"image_url": "https://example.com/a.png"},
        )

    @patch("requests.post")
    def test_errors_are_readable(self, post):
        cases = [
            (requests.Timeout(), "超时"),
            (requests.ConnectionError(), "无法连接"),
            (response({"detail": "图片无效"}, 400), "图片无效"),
            (response({"unexpected": "data"}), "格式不正确"),
            (response({"result": {"classification": [], "description": "bad"}}), "格式不正确"),
        ]
        for value, message in cases:
            with self.subTest(message=message):
                post.side_effect = value if isinstance(value, Exception) else None
                post.return_value = value
                with self.assertRaisesRegex(ValueError, message):
                    analyze_image("http://localhost:8000", image_url="https://example.com/a.png")
        post.side_effect = None
        post.return_value = response()
        post.return_value.json.side_effect = ValueError("not json")
        with self.assertRaisesRegex(ValueError, "未返回 JSON"):
            analyze_image("http://localhost:8000", image_url="https://example.com/a.png")

    def app(self):
        app = AppTest.from_file(str(Path(__file__).with_name("frontend.py"))).run()
        self.assertEqual(len(app.exception), 0)
        return app

    @patch("requests.post")
    def test_url_page_result_and_clear_on_change(self, post):
        post.return_value = response()
        app = self.app()
        self.assertTrue(app.button[0].disabled)
        app.radio[0].set_value("图片 URL").run()
        app.text_input(key="image_url").input("file:///bad.jpg").run()
        self.assertTrue(app.button[0].disabled)
        self.assertEqual(len(app.error), 1)
        app.text_input(key="image_url").input("https://example.com/a.png").run()
        app.button[0].click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertIn("低风险", app.warning[0].value)
        app.text_input(key="image_url").input("https://example.com/b.png").run()
        self.assertNotIn("analysis_result", app.session_state)

    @patch("requests.post")
    def test_camera_and_upload_pages_submit_files(self, post):
        content = sample_image()
        photo = SimpleNamespace(size=len(content), name="photo.png", getvalue=lambda: content)
        for source, widget in (("手机/摄像头拍照", "camera_input"), ("上传图片", "file_uploader")):
            with self.subTest(source=source), patch(f"streamlit.{widget}", return_value=photo):
                post.reset_mock()
                post.return_value = response()
                app = self.app()
                app.radio[0].set_value(source).run()
                self.assertFalse(app.button[0].disabled)
                app.button[0].click().run()
                self.assertEqual(len(app.exception), 0)
                self.assertIn("files", post.call_args.kwargs)
                self.assertNotIn("data", post.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
