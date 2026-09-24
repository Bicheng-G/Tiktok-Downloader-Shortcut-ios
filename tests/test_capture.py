import threading
import unittest
from unittest.mock import Mock, patch

from douyin_resolver.browser import BrowserWorker, capture_page
from douyin_resolver.core import ResolverError

ID = "7680575887263617273"
URL = "https://www.douyin.com/video/" + ID


class CaptureTests(unittest.TestCase):
    def page(self, complete):
        page = Mock()
        page.evaluate.return_value = "test-UA"
        page.locator.return_value.all_text_contents.return_value = []
        page.locator.return_value.inner_text.return_value = "视频加载中"
        listeners, clock = {}, [0.0]
        page.on.side_effect = lambda event, callback: listeners.update({event: callback})
        page.wait_for_timeout.side_effect = lambda ms: clock.__setitem__(0, clock[0] + ms / 1000)
        data = {"aweme_id": ID, "video": {}}
        request = Mock(url="https://www.douyin.com/aweme/v1/web/aweme/detail/")
        request.response.return_value.json.return_value = {"aweme_detail": data}
        stalled = Mock()
        stalled.json.side_effect = AssertionError("Must not read unfinished body")

        def goto(*args, **kwargs):
            if "response" in listeners:
                listeners["response"](stalled)
            if complete:
                listeners["requestfinished"](request)

        page.goto.side_effect = goto
        return page, clock, data, stalled

    def test_only_completed_responses_are_read(self):
        page, clock, data, stalled = self.page(True)
        with patch("douyin_resolver.browser.time.monotonic", side_effect=lambda: clock[0]):
            self.assertEqual(capture_page(page, ID, URL, 1, RuntimeError), (data, "test-UA"))
        stalled.json.assert_not_called()
        page.remove_listener.assert_called_once()

    def test_unfinished_body_does_not_prevent_timeout(self):
        page, clock, data, stalled = self.page(False)
        with patch("douyin_resolver.browser.time.monotonic", side_effect=lambda: clock[0]):
            with self.assertRaises(ResolverError) as exc:
                capture_page(page, ID, URL, 1, RuntimeError)
        self.assertEqual(exc.exception.code, "NO_VIDEO_DATA")
        stalled.json.assert_not_called()

    def test_process_and_context_reused_on_same_worker_thread(self):
        runtime = Mock()
        threads = []
        runtime.chromium.launch.side_effect = lambda **kw: threads.append(threading.get_ident()) or Mock()
        with patch("playwright.sync_api.sync_playwright") as factory, patch(
                "douyin_resolver.browser.capture_page", return_value=({}, "UA")) as capture:
            factory.return_value.start.return_value = runtime
            worker = BrowserWorker()
            try:
                worker.capture(ID, URL)
                worker.capture(ID, URL)
                self.assertEqual(worker.launches, 1)
                runtime.chromium.launch.assert_called_once()
                self.assertEqual(capture.call_count, 2)
                self.assertNotEqual(threads[0], threading.get_ident())
            finally:
                worker.close()
            runtime.stop.assert_called_once()

    def test_busy_worker_rejects_without_queue(self):
        worker = BrowserWorker()
        try:
            worker.gate.acquire()
            with self.assertRaises(ResolverError) as exc:
                worker.capture(ID, URL)
            self.assertEqual(exc.exception.code, "RESOLVER_BUSY")
        finally:
            worker.gate.release()
            worker.close()
