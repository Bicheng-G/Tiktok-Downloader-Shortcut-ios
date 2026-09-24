import json
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from douyin_resolver.core import ResolverError
from douyin_resolver.http_resolver import fetch
from douyin_resolver.service import Resolver
from test_core import A, ID, PAGE, Response, Session, metadata, video


class HTTPTests(unittest.TestCase):
    def test_skip_recommendations_then_match_backup(self):
        wrong = {"aweme_id": "1111111111111111111", "video": {}}
        session = Session(Response(json.dumps({"aweme_list": [wrong]}).encode()),
                          Response(json.dumps({"aweme_list": [metadata()]}).encode()))
        item, ua = fetch(ID, session=session)
        self.assertEqual(item["aweme_id"], ID)
        self.assertEqual(len(session.calls), 2)
        self.assertFalse(session.calls[0][1]["allow_redirects"])

    def test_success_status_alone_is_not_success(self):
        session = Session(Response(b'{}'), Response(b'<html>challenge</html>'))
        with self.assertRaises(ResolverError) as exc:
            fetch(ID, session=session)
        self.assertEqual(exc.exception.code, "HTTP_NO_MATCH")


class ServiceTests(unittest.TestCase):
    def test_http_mode_never_creates_browser(self):
        with patch("douyin_resolver.service.fetch", return_value=(metadata(), "UA")), patch(
                "douyin_resolver.browser.BrowserWorker") as browser:
            with Resolver(engine="http", check=False) as service:
                result = service.resolve(PAGE)
                self.assertEqual(result.strategy, "http")
            browser.assert_not_called()

    def test_auto_http_hit_does_not_capture(self):
        with patch("douyin_resolver.service.fetch", return_value=(metadata(), "UA")):
            with Resolver(check=False) as service, patch.object(service.browser, "capture") as capture:
                self.assertEqual(service.resolve(PAGE).strategy, "http")
                capture.assert_not_called()

    def test_auto_fallback_after_http_miss(self):
        with patch("douyin_resolver.service.fetch", side_effect=ResolverError("HTTP_NO_MATCH", "miss")):
            with Resolver(check=False) as service, patch.object(service.browser, "capture", return_value=(metadata(), "UA")) as capture:
                self.assertEqual(service.resolve(PAGE).strategy, "browser")
                capture.assert_called_once_with(ID, PAGE)

    def test_cache_copy_refresh_and_expiry(self):
        with Resolver(engine="http", check=False) as service, patch.object(service, "_uncached", return_value=video(A)) as run:
            first = service.resolve(PAGE)
            first.candidates.clear()
            cached = service.resolve(PAGE)
            self.assertTrue(cached.cache_hit)
            self.assertEqual(cached.candidates[0].url, A)
            service.resolve(PAGE, refresh=True)
            self.assertEqual(run.call_count, 2)
            service.cache[ID] = (time.time() - 1, video(A))
            service.resolve(PAGE)
            self.assertEqual(run.call_count, 3)

    def test_expiring_cdn_is_not_cached(self):
        item = video(A)
        item.expires_at = int(time.time()) + 20
        with Resolver(engine="http") as service, patch.object(service, "_uncached", return_value=item) as run:
            service.resolve(PAGE)
            service.resolve(PAGE)
            self.assertEqual(run.call_count, 2)

    def test_simultaneous_identical_requests_share_one_resolution(self):
        started, release = threading.Event(), threading.Event()

        def slow(*args):
            started.set()
            self.assertTrue(release.wait(2))
            return video(A)

        with Resolver(engine="http") as service, patch.object(service, "_uncached", side_effect=slow) as run:
            with ThreadPoolExecutor(max_workers=2) as pool:
                a = pool.submit(service.resolve, PAGE)
                self.assertTrue(started.wait(1))
                b = pool.submit(service.resolve, PAGE)
                release.set()
                self.assertEqual(a.result().id, b.result().id)
            self.assertEqual(run.call_count, 1)

    def test_busy_service_and_failed_resolution_are_not_cached(self):
        with Resolver(engine="http", concurrency=1) as service:
            service.slots.acquire()
            with self.assertRaises(ResolverError) as exc:
                service.resolve(PAGE)
            service.slots.release()
            self.assertEqual(exc.exception.code, "RESOLVER_BUSY")
            with patch.object(service, "_uncached", side_effect=ResolverError("TEST", "failure")):
                with self.assertRaises(ResolverError):
                    service.resolve(PAGE)
            self.assertFalse(service.cache)
            self.assertFalse(service.inflight)
