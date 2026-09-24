import unittest
from unittest.mock import Mock

from fastapi.testclient import TestClient

from douyin_resolver.core import ResolverError
from douyin_resolver.server import Settings, create_app
from test_core import A, video

TOKEN = "test-token-at-least-24-characters"


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.resolver = Mock()
        self.resolver.resolve.return_value = video(A)
        self.client = TestClient(create_app(Settings(token=TOKEN), self.resolver))
        self.client.__enter__()
        self.auth = {"Authorization": "Bearer " + TOKEN}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.resolver.close.assert_called_once()

    def post(self, **kwargs):
        return self.client.post("/resolve", headers=self.auth, **kwargs)

    def test_public_liveness_does_not_resolve(self):
        self.assertEqual(self.client.get("/health").json()["status"], "ok")
        self.resolver.resolve.assert_not_called()

    def test_auth_before_resolution(self):
        result = self.client.post("/resolve", json={"url": "anything"})
        self.assertEqual(result.status_code, 401)
        self.resolver.resolve.assert_not_called()

    def test_resolve_contract(self):
        response = self.post(json={"url": "share text", "refresh": True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["download_url"], A)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.resolver.resolve.assert_called_once_with("share text", refresh=True)

    def test_validation(self):
        for payload in ([], {}, {"url": 12}, {"url": ""}, {"url": "x", "refresh": "true"}):
            with self.subTest(payload=payload):
                self.assertEqual(self.post(json=payload).status_code, 400)
        self.assertEqual(self.post(content="bad").status_code, 415)
        self.auth["Content-Type"] = "application/json"
        self.assertEqual(self.post(content="bad").status_code, 400)
        self.assertEqual(self.post(content="x" * 16385).status_code, 413)
        self.resolver.resolve.assert_not_called()

    def test_bounded_streaming_body(self):
        self.auth["Content-Type"] = "application/json"
        response = self.post(content=iter([b"a" * 9000, b"b" * 9000]))
        self.assertEqual(response.status_code, 413)

    def test_error_mapping(self):
        for code, expected in (("INVALID_URL", 400), ("HTTP_NO_MATCH", 502), ("RESOLVER_BUSY", 429)):
            self.resolver.resolve.side_effect = ResolverError(code, "test")
            result = self.post(json={"url": "link"})
            self.assertEqual(result.status_code, expected)
            self.assertEqual(result.json(), {"ok": False, "error": code, "message": "test"})

    def test_unexpected_error_does_not_leak_details(self):
        self.resolver.resolve.side_effect = RuntimeError("SECRET URL")
        with self.assertLogs("douyin_resolver.server", level="ERROR"):
            response = self.post(json={"url": "link"})
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("SECRET", response.text)

    def test_no_old_disk_or_query_download_endpoints(self):
        for url in ("/files/../../README.md", "/download?url=x", "/docs"):
            self.assertEqual(self.client.get(url).status_code, 404)

    def test_startup_requires_token_and_valid_settings(self):
        for settings in (Settings(), Settings(token="short"), Settings(token=TOKEN, engine="x"),
                         Settings(token=TOKEN, timeout=0), Settings(token=TOKEN, cache_ttl=999)):
            with self.assertRaises(ValueError):
                create_app(settings)
