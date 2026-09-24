"""Public mobile feed; never accept a recommendation in place of the target."""
import json
import time

import requests

from .core import ResolverError, find_aweme

ENDPOINTS = (
    "https://api5-normal-c-hl.amemv.com/aweme/v1/feed/",
    "https://aweme.snssdk.com/aweme/v1/feed/",
)
MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
)


def fetch(ident, *, session=None, timeout=4):
    own = session is None
    session = session or requests.Session()
    try:
        for endpoint in ENDPOINTS:
            try:
                deadline = time.monotonic() + timeout * 2
                with session.get(
                    endpoint, params={"aweme_id": ident, "aid": "1128"},
                    headers={"User-Agent": MOBILE_UA, "Accept": "application/json"},
                    timeout=(timeout, timeout), allow_redirects=False, stream=True,
                ) as response:
                    if response.status_code != 200:
                        continue
                    chunks, size = [], 0
                    for chunk in response.iter_content(65536):
                        size += len(chunk)
                        if size > 4 * 1024 * 1024 or time.monotonic() > deadline:
                            raise ValueError("Metadata too large")
                        chunks.append(chunk)
                    aweme = find_aweme(json.loads(b"".join(chunks)), ident)
                    if aweme:
                        return aweme, MOBILE_UA
            except (requests.RequestException, ValueError):
                continue
    finally:
        if own:
            session.close()
    raise ResolverError("HTTP_NO_MATCH", "HTTP 接口未返回目标视频；可启用 auto 模式的浏览器兜底。")
