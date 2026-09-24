"""One lazy browser process per backend worker, owned by one dedicated thread."""
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .core import ResolverError, find_aweme, valid_url

logger = logging.getLogger(__name__)


class BrowserWorker:
    def __init__(self, *, timeout=35, channel="chromium", headed=False, profile=None):
        self.timeout, self.channel = timeout, channel
        self.headed, self.profile = headed, profile
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="douyin-browser")
        self.gate = threading.Lock()
        self.runtime = self.browser = self.context = None
        self.launches = 0

    def _start(self):
        if self.context is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ResolverError("BROWSER_NOT_INSTALLED", "安装 .[browser] 并执行 playwright install chromium，或使用 http 模式。") from exc
        self.runtime = sync_playwright().start()
        options = {"headless": not self.headed}
        if self.channel != "chromium":
            options["channel"] = self.channel
        try:
            if self.profile:
                self.context = self.runtime.chromium.launch_persistent_context(
                    str(Path(self.profile).resolve()), locale="zh-CN", **options)
            else:
                self.browser = self.runtime.chromium.launch(**options)
                self.context = self.browser.new_context(locale="zh-CN")
            self.launches += 1
        except Exception:
            self._stop()
            raise

    def capture(self, ident, url):
        # Don't leave multiple iPhones waiting in a long browser queue.
        if not self.gate.acquire(blocking=False):
            raise ResolverError("RESOLVER_BUSY", "浏览器正在解析另一条视频，请稍后重试。")
        try:
            return self.executor.submit(self._capture, ident, url).result()
        finally:
            self.gate.release()

    def _capture(self, ident, url):
        try:
            self._start()
            from playwright.sync_api import Error as BrowserError
            page = self.context.new_page()
            try:
                return capture_page(page, ident, url, self.timeout, BrowserError)
            finally:
                page.close()
        except ResolverError:
            raise
        except Exception as exc:
            # Disconnected/crashed browser is rebuilt on the next request.
            self._stop()
            raise ResolverError("BROWSER_ERROR", "浏览器加载失败；检查浏览器安装、网络或稍后重试。") from exc

    def _stop(self):
        for resource, method in ((self.context, "close"), (self.browser, "close"), (self.runtime, "stop")):
            if resource is not None:
                try:
                    getattr(resource, method)()
                except Exception:
                    logger.debug("Browser cleanup failed", exc_info=True)
        self.context = self.browser = self.runtime = None

    def close(self):
        self.executor.submit(self._stop).result()
        self.executor.shutdown(wait=True)


def capture_page(page, ident, url, timeout, browser_error):
    finished = []

    def on_finished(request):
        path = urlsplit(request.url).path
        if valid_url(request.url) and "/aweme/" in path and ("detail" in path or "feed" in path):
            finished.append(request)

    page.on("requestfinished", on_finished)
    deadline = time.monotonic() + timeout
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
        while time.monotonic() < deadline:
            while finished and time.monotonic() < deadline:
                try:
                    response = finished.pop(0).response()
                    aweme = find_aweme(response.json(), ident) if response else None
                except (browser_error, ValueError):
                    continue
                if aweme:
                    return aweme, page.evaluate("navigator.userAgent")
            for raw in page.locator("script#RENDER_DATA, script#__NEXT_DATA__").all_text_contents():
                try:
                    aweme = find_aweme(json.loads(unquote(raw)), ident)
                except (ValueError, TypeError):
                    continue
                if aweme:
                    return aweme, page.evaluate("navigator.userAgent")
            page.wait_for_timeout(250)
        body = page.locator("body").inner_text(timeout=2000)
        if any(word in body for word in ("验证后", "完成验证", "拖动滑块", "captcha")):
            raise ResolverError("VERIFICATION_REQUIRED", "平台要求人工验证，当前后端无法自动解析这条视频。")
        if any(word in body for word in ("视频已删除", "作品已删除", "视频不见了", "暂时无法播放")):
            raise ResolverError("VIDEO_UNAVAILABLE", "该视频已删除、受限或暂时不可播放。")
        raise ResolverError("NO_VIDEO_DATA", "未取得目标视频详情，请检查视频是否公开或稍后重试。")
    finally:
        page.remove_listener("requestfinished", on_finished)
