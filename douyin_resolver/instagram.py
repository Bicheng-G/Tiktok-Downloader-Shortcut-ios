"""Public Instagram video metadata, with exact shortcode matching."""
import json
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import requests

from .core import (Candidate, DEFAULT_UA, INSTAGRAM_HOSTS, ResolverError, Video,
                   checked_get, valid_url)

ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
# Instagram's logged-out Relay query; upstream may change this document ID.
DOC_ID = "27130156389949648"
QUERY_NAME = "PolarisLoggedOutDesktopWWWPostRootContentQuery"
MAX_METADATA = 8 * 1024 * 1024


def is_instagram(url):
    return urlsplit(url).hostname in INSTAGRAM_HOSTS


def media_id(ident):
    value = 0
    for char in ident:
        value = value * 64 + ALPHABET.index(char)
    return str(value)


def matches(item, ident):
    code = item.get("shortcode") or item.get("code")
    if code:
        return code == ident
    return str(item.get("pk") or item.get("id") or "").split("_")[0] == media_id(ident)


def find_media(data, ident):
    """Ignore recommendations and routing records that contain no media."""
    stack = [data]
    fields = {"video_versions", "video_url", "media_type", "is_video",
              "carousel_media", "edge_sidecar_to_children"}
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if matches(item, ident) and fields.intersection(item):
                return item
            stack.extend(v for v in item.values() if isinstance(v, (dict, list)))
        elif isinstance(item, list):
            stack.extend(item)
    return None


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.scripts, self.current = [], None

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.current = []

    def handle_data(self, data):
        if self.current is not None:
            self.current.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.current is not None:
            self.scripts.append("".join(self.current))
            self.current = None


def parse_page(html, ident):
    parser = Scripts()
    parser.feed(html)
    decoder = json.JSONDecoder()
    for raw in parser.scripts:
        try:
            data = json.loads(raw)
        except ValueError:
            # Older embed pages use these JS assignments instead of JSON scripts.
            match = re.search(r"(?:window\._sharedData\s*=\s*|"
                              r"window\.__additionalDataLoaded\([^,]+,\s*)(\{)", raw)
            if not match:
                continue
            try:
                data, _ = decoder.raw_decode(raw[match.start(1):])
            except ValueError:
                continue
        item = find_media(data, ident)
        if item:
            return item
    return None


def _read(response, deadline):
    chunks, size = [], 0
    for chunk in response.iter_content(65536):
        size += len(chunk)
        if size > MAX_METADATA or time.monotonic() > deadline:
            raise ResolverError("HTTP_NO_MATCH", "Instagram 视频详情过大或读取超时。")
        chunks.append(chunk)
    return b"".join(chunks).decode("utf-8", errors="replace")


def fetch(ident, url, *, session=None, timeout=8):
    own = session is None
    session = session or requests.Session()
    headers = {"User-Agent": DEFAULT_UA, "Referer": "https://www.instagram.com/"}
    login_required = False
    try:
        for page_url in (url, f"https://www.instagram.com/p/{ident}/embed/"):
            try:
                deadline = time.monotonic() + timeout
                with checked_get(session, page_url, headers=headers, timeout=timeout) as response:
                    if response.status_code != 200:
                        login_required |= response.status_code in (401, 403, 429)
                        continue
                    login_required |= urlsplit(response.url).path.startswith("/accounts/login")
                    html = _read(response, deadline)
                item = parse_page(html, ident)
                if item:
                    return item, DEFAULT_UA
                # Current public pages load their media via a Relay POST.
                token = re.search(r'\["LSD",\[\],\{"token":"([^"\s]+)"', html)
                if not token or page_url != url:
                    continue
                deadline = time.monotonic() + timeout
                with session.post(
                    "https://www.instagram.com/api/graphql", headers={
                        **headers, "Referer": url, "X-IG-App-ID": "936619743392459",
                        "X-FB-LSD": token.group(1), "X-FB-Friendly-Name": QUERY_NAME,
                        "X-Requested-With": "XMLHttpRequest",
                    }, data={"lsd": token.group(1), "doc_id": DOC_ID,
                             "fb_api_caller_class": "RelayModern",
                             "fb_api_req_friendly_name": QUERY_NAME,
                             "server_timestamps": "true",
                             "variables": json.dumps({"media_id": media_id(ident)})},
                    timeout=(min(5, timeout), timeout), stream=True, allow_redirects=False,
                ) as response:
                    login_required |= response.status_code in (301, 302, 401, 403, 429)
                    if response.status_code != 200:
                        continue
                    raw = _read(response, deadline).removeprefix("for (;;);")
                    item = find_media(json.loads(raw), ident)
                    if item:
                        return item, DEFAULT_UA
            except (requests.RequestException, ValueError):
                continue
    finally:
        if own:
            session.close()
    if login_required:
        raise ResolverError("LOGIN_REQUIRED", "Instagram 要求登录或限制匿名访问；请稍后重试或使用已登录的独立浏览器配置。")
    raise ResolverError("HTTP_NO_MATCH", "Instagram HTTP 未返回目标视频；可启用 auto 模式的浏览器兜底。")


def video_from(item, ident, ua, quality="compatible"):
    if not matches(item, ident):
        raise ResolverError("ID_MISMATCH", "返回的 Instagram 视频与输入链接不匹配。")
    if (item.get("carousel_media") or item.get("edge_sidecar_to_children")
            or item.get("media_type") in (1, 8) or item.get("is_video") is False):
        raise ResolverError("UNSUPPORTED_MEDIA", "当前仅支持 Instagram 单条视频，不支持图片或轮播帖子。")
    versions = list(item.get("video_versions") or [])
    if item.get("video_url"):
        versions.append({"url": item["video_url"], **(item.get("dimensions") or {})})
    candidates, seen, expiries = [], set(), []
    for version in versions:
        if not isinstance(version, dict):
            continue
        url = version.get("url")
        if not isinstance(url, str) or not valid_url(url, media=True):
            continue
        host = urlsplit(url).hostname
        if not any(host == suffix or host.endswith("." + suffix)
                   for suffix in ("cdninstagram.com", "fbcdn.net")):
            continue
        # Only complete MP4 versions; do not use a DASH manifest or a video element's blob.
        if not urlsplit(url).path.lower().endswith(".mp4") or url in seen:
            continue
        seen.add(url)
        candidates.append(Candidate(url, int(version.get("width") or item.get("original_width") or 0),
                                    int(version.get("height") or item.get("original_height") or 0),
                                    codec=item.get("video_codec") or "unknown",
                                    source="instagram"))
        expiry = parse_qs(urlsplit(url).query).get("oe", [""])[0]
        try:
            expiries.append(int(expiry, 16))
        except ValueError:
            pass
    candidates.sort(key=lambda c: (c.codec in ("h264", "avc1") if quality == "compatible" else True,
                                   c.width * c.height), reverse=True)
    if not candidates:
        raise ResolverError("NO_PROGRESSIVE_MP4", "Instagram 详情没有完整 MP4；视频可能受限或仅提供分离音视频流。")
    caption = item.get("caption") or {}
    edges = (item.get("edge_media_to_caption") or {}).get("edges") or []
    title = (caption.get("text", "") if isinstance(caption, dict) else str(caption))
    if not title and edges:
        title = (edges[0].get("node") or {}).get("text", "")
    author = item.get("user") or item.get("owner") or {}
    return Video(ident, f"https://www.instagram.com/reel/{ident}/", title,
                 author.get("username") or author.get("full_name", ""),
                 float(item.get("video_duration") or 0), candidates,
                 {"User-Agent": ua, "Referer": "https://www.instagram.com/"},
                 datetime.now(timezone.utc).isoformat(), min(expiries) if expiries else None)


def capture_page(page, ident, url, timeout, browser_error):
    finished = []

    def on_finished(request):
        path = urlsplit(request.url).path
        if is_instagram(request.url) and ("graphql" in path or "/api/v1/media/" in path):
            finished.append(request)

    page.on("requestfinished", on_finished)
    deadline = time.monotonic() + timeout
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
        while time.monotonic() < deadline:
            while finished and time.monotonic() < deadline:
                try:
                    response = finished.pop(0).response()
                    item = find_media(response.json(), ident) if response else None
                except (browser_error, ValueError):
                    continue
                if item:
                    return item, page.evaluate("navigator.userAgent")
            item = parse_page(page.content(), ident)
            if item:
                return item, page.evaluate("navigator.userAgent")
            page.wait_for_timeout(250)
        body = page.locator("body").inner_text(timeout=2000).lower()
        if "/accounts/login" in page.url or any(word in body for word in (
                "log in", "login", "sign up", "登录", "登入")):
            raise ResolverError("LOGIN_REQUIRED", "Instagram 要求登录；请使用已登录的独立浏览器配置或稍后重试。")
        if any(word in body for word in ("captcha", "challenge", "验证", "確認")):
            raise ResolverError("VERIFICATION_REQUIRED", "Instagram 要求人工验证，请在独立浏览器配置中完成验证。")
        raise ResolverError("NO_VIDEO_DATA", "未取得目标 Instagram 视频详情；请检查视频是否公开、已删除或受地区限制。")
    finally:
        page.remove_listener("requestfinished", on_finished)
