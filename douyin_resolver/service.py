"""Resolution strategy, bounded concurrency, in-flight sharing and short cache."""
import copy
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future

from .core import ResolverError, normalize, verify, video_from
from .http_resolver import fetch


class Resolver:
    def __init__(self, *, engine="auto", quality="compatible", timeout=35,
                 channel="chromium", headed=False, profile=None, cache_ttl=45,
                 concurrency=4, check=True):
        if engine not in ("auto", "http", "browser"):
            raise ValueError("engine must be auto, http or browser")
        self.engine, self.quality, self.check = engine, quality, check
        self.cache_ttl = max(0, cache_ttl)
        self.cache, self.inflight = OrderedDict(), {}
        self.lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(concurrency)
        self.browser = None
        if engine != "http":
            from .browser import BrowserWorker
            self.browser = BrowserWorker(timeout=timeout, channel=channel, headed=headed, profile=profile)

    def _uncached(self, ident, url):
        if self.engine != "browser":
            try:
                aweme, ua = fetch(ident)
                video = video_from(aweme, ident, ua, self.quality)
                video.strategy = "http"
                return verify(video) if self.check else video
            except ResolverError as exc:
                if self.engine == "http" or exc.code == "UNSUPPORTED_MEDIA":
                    raise
        aweme, ua = self.browser.capture(ident, url)
        video = video_from(aweme, ident, ua, self.quality)
        video.strategy = "browser"
        return verify(video) if self.check else video

    def resolve(self, text, *, refresh=False):
        if not self.slots.acquire(blocking=False):
            raise ResolverError("RESOLVER_BUSY", "解析服务繁忙，请稍后重试。")
        try:
            ident, url = normalize(text)
            with self.lock:
                cached = self.cache.get(ident)
                if not refresh and cached and cached[0] > time.time():
                    result = copy.deepcopy(cached[1])
                    result.cache_hit = True
                    self.cache.move_to_end(ident)
                    return result
                self.cache.pop(ident, None)
                pending = self.inflight.get(ident)
                owner = pending is None
                if owner:
                    pending = self.inflight[ident] = Future()
            if not owner:
                return copy.deepcopy(pending.result())
            try:
                result = self._uncached(ident, url)
                expiry = time.time() + self.cache_ttl
                if result.expires_at:
                    expiry = min(expiry, float(result.expires_at) - 60)
                with self.lock:
                    if expiry > time.time():
                        self.cache[ident] = (expiry, copy.deepcopy(result))
                        while len(self.cache) > 256:
                            self.cache.popitem(last=False)
                pending.set_result(copy.deepcopy(result))
                return result
            except BaseException as exc:
                pending.set_exception(exc)
                raise
            finally:
                with self.lock:
                    self.inflight.pop(ident, None)
        finally:
            self.slots.release()

    def close(self):
        if self.browser:
            self.browser.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
