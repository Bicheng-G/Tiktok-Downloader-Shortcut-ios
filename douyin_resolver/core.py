from __future__ import annotations

import os
import re
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit

import requests


class ResolverError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


PAGE_HOSTS = {"douyin.com", "www.douyin.com", "www-hj.douyin.com",
              "v.douyin.com", "iesdouyin.com", "www.iesdouyin.com"}
CDN_SUFFIXES = ("douyinvod.com", "zjcdn.com", "douyin.com", "iesdouyin.com",
                "douyinstatic.com", "bytecdn.cn", "bytecdn.com", "bytedance.com")
DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")


def valid_url(url: str, *, media: bool = False) -> bool:
    try:
        p = urlsplit(url)
        host = (p.hostname or "").lower()
        if p.scheme not in ("https", "http") or p.username or p.password or p.port not in (None, 80, 443):
            return False
        return any(host == s or host.endswith("." + s) for s in CDN_SUFFIXES) if media else host in PAGE_HOSTS
    except (ValueError, TypeError):
        return False


def extract_url(text: str) -> str:
    """Accept a link, or the full text copied from the Douyin share sheet."""
    for match in re.finditer(r'https?://[^\s<>"\u3000]+', text):
        url = match.group().rstrip(".,;!?，。；！？、）)]}>'\"：")
        if valid_url(url):
            return url
    raise ResolverError("INVALID_URL", "请输入抖音视频链接或含链接的分享文案。")


def video_id(url: str) -> str | None:
    if not valid_url(url):
        return None
    p = urlsplit(url)
    m = re.fullmatch(r"/(?:video|share/video)/(\d{10,25})/?", p.path)
    if m:
        return m.group(1)
    for name in ("modal_id", "aweme_id", "item_ids"):
        value = parse_qs(p.query).get(name, [""])[0]
        if re.fullmatch(r"\d{10,25}", value):
            return value
    return None


def checked_get(session, url, *, media=False, headers=None, timeout=25):
    """Validate every redirect, keep cookies domain-scoped and stream responses."""
    deadline = time.monotonic() + timeout
    for _ in range(8):
        if not valid_url(url, media=media):
            raise ResolverError("UNSAFE_REDIRECT", "链接跳转到了不支持的域名。")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ResolverError("NETWORK_TIMEOUT", "请求超时，请稍后重试。")
        response = session.get(url, headers=headers, timeout=(min(5, remaining), remaining),
                               stream=True, allow_redirects=False)
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise ResolverError("BAD_REDIRECT", "服务器重定向缺少目标地址。")
            url = urljoin(url, location)
            continue
        return response
    raise ResolverError("TOO_MANY_REDIRECTS", "链接重定向次数过多。")


def normalize(text: str, session=None) -> tuple[str, str]:
    url = extract_url(text)
    ident = video_id(url)
    if not ident and urlsplit(url).hostname == "v.douyin.com":
        own = session is None
        session = session or requests.Session()
        try:
            with checked_get(session, url, headers={"User-Agent": DEFAULT_UA}) as response:
                if response.status_code >= 400:
                    raise ResolverError("SHORT_LINK_FAILED", f"短链接返回 HTTP {response.status_code}。")
                ident = video_id(response.url)
        except requests.RequestException as exc:
            raise ResolverError("NETWORK_ERROR", "无法展开短链接，请检查网络或使用完整视频链接。") from exc
        finally:
            if own:
                session.close()
    if not ident:
        raise ResolverError("VIDEO_URL_REQUIRED", "请提供单条视频链接；精选首页、用户主页和合集不是单条视频。")
    return ident, f"https://www.douyin.com/video/{ident}"


def find_aweme(data, ident):
    """Only return the requested item, never a recommended/preloaded video."""
    stack = [data]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if str(item.get("aweme_id", item.get("awemeId", ""))) == ident and ("video" in item or "images" in item):
                return item
            stack.extend(v for v in item.values() if isinstance(v, (dict, list)))
        elif isinstance(item, list):
            stack.extend(item)
    return None


@dataclass
class Candidate:
    url: str
    width: int = 0
    height: int = 0
    bitrate: int = 0
    size: int = 0
    codec: str = "unknown"
    source: str = "play_addr"


@dataclass
class Video:
    id: str
    page_url: str
    title: str
    author: str
    duration_seconds: float
    candidates: list[Candidate]
    headers: dict
    resolved_at: str
    expires_at: int | None = None
    verified: bool = False
    strategy: str = "unknown"
    cache_hit: bool = False

    def to_dict(self):
        result = asdict(self)
        result["download_url"] = self.candidates[0].url
        result["filename"] = f"{self.id}.mp4"
        result["warning"] = "CDN 地址可能过期；失败时请重新解析。"
        return result


def candidates_from(aweme, quality="compatible") -> list[Candidate]:
    video = aweme.get("video") or {}
    if aweme.get("images"):
        raise ResolverError("UNSUPPORTED_MEDIA", "这是图文作品，当前解析器仅支持视频。")
    candidates = []

    def add(address, source, codec="unknown", bitrate=0):
        if not isinstance(address, dict):
            return
        for url in address.get("url_list", []):
            if not isinstance(url, str) or not valid_url(url, media=True):
                continue
            # DASH component URLs are valid MP4 containers, but lack the other track.
            if re.search(r"/media-(video|audio)-|/play/dash/|\.m3u8(?:\?|$)", url):
                continue
            candidates.append(Candidate(url, int(address.get("width") or 0),
                                        int(address.get("height") or 0), int(bitrate or 0),
                                        int(address.get("data_size") or 0), codec, source))

    for rate in video.get("bit_rate") or []:
        if rate.get("format", "mp4") != "mp4":
            continue
        add(rate.get("play_addr"), "bit_rate", "h265" if rate.get("is_h265") or rate.get("is_bytevc1") else "h264",
            rate.get("bit_rate"))
    add(video.get("play_addr_h264"), "play_addr_h264", "h264")
    if video.get("format", "mp4") == "mp4":
        add(video.get("play_addr"), "play_addr", "h265" if video.get("is_h265") else "h264")
        add(video.get("play_addr_265"), "play_addr_265", "h265")
    add(video.get("download_addr"), "download_addr")
    candidates.sort(key=lambda c: (c.source != "download_addr",
                                  c.codec == "h264" if quality == "compatible" else True,
                                  c.width * c.height, c.bitrate), reverse=True)
    result, seen = [], set()
    for c in candidates:
        if c.url not in seen:
            result.append(c)
            seen.add(c.url)
    if not result:
        raise ResolverError("NO_PROGRESSIVE_MP4", "详情中没有支持的完整 MP4；可能是受限视频或仅有分离音视频流。")
    return result


def video_from(aweme, ident, ua, quality="compatible"):
    if str(aweme.get("aweme_id", aweme.get("awemeId", ""))) != ident:
        raise ResolverError("ID_MISMATCH", "返回的视频与输入链接不匹配。")
    video = aweme.get("video") or {}
    return Video(ident, f"https://www.douyin.com/video/{ident}", aweme.get("desc", ""),
                 (aweme.get("author") or {}).get("nickname", ""),
                 float(video.get("duration") or 0) / 1000, candidates_from(aweme, quality),
                 {"User-Agent": ua, "Referer": "https://www.douyin.com/"},
                 datetime.now(timezone.utc).isoformat(), video.get("cdn_url_expired"))


def is_mp4(chunk: bytes) -> bool:
    return len(chunk) >= 12 and chunk[4:8] == b"ftyp"


def verify(video: Video, session=None) -> Video:
    own = session is None
    session = session or requests.Session()
    try:
        for candidate in video.candidates[:4]:
            try:
                with checked_get(session, candidate.url, media=True,
                                 headers={**video.headers, "Range": "bytes=0-1023", "Accept-Encoding": "identity"}, timeout=5) as r:
                    if r.status_code not in (200, 206):
                        continue
                    if r.status_code == 206 and not re.match(r"bytes 0-", r.headers.get("Content-Range", "")):
                        continue
                    first = next(r.iter_content(1024), b"")
                    if not is_mp4(first):
                        continue
                video.candidates.remove(candidate)
                video.candidates.insert(0, candidate)
                video.verified = True
                return video
            except (requests.RequestException, ResolverError):
                continue
    finally:
        if own:
            session.close()
    raise ResolverError("CDN_UNAVAILABLE", "已探测的 CDN 地址无法读取 MP4；可能已过期或受到地区限制，请重新解析。")


def capture(ident, page_url, *, timeout=35, channel="chrome", headed=False, profile=None):
    """One-shot browser utility; the backend instead reuses its BrowserWorker."""
    from .browser import BrowserWorker
    worker = BrowserWorker(timeout=timeout, channel=channel, headed=headed, profile=profile)
    try:
        return worker.capture(ident, page_url)
    finally:
        worker.close()


def resolve(text, *, engine="auto", quality="compatible", timeout=35, channel="chrome",
            headed=False, profile=None, check=True):
    from .service import Resolver
    with Resolver(engine=engine, quality=quality, timeout=timeout, channel=channel,
                  headed=headed, profile=profile, check=check, cache_ttl=0) as service:
        return service.resolve(text)


def download(video: Video, directory="downloads", *, max_bytes=2 * 1024**3, timeout=600, session=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    own = session is None
    session = session or requests.Session()
    deadline = time.monotonic() + timeout
    last_error = "没有可用地址"
    try:
        for candidate in video.candidates:
            fd, temporary = tempfile.mkstemp(prefix=f"{video.id}-", suffix=".part", dir=directory)
            target = Path(temporary).with_suffix(".mp4")
            try:
                with os.fdopen(fd, "wb") as output, checked_get(
                    session, candidate.url, media=True,
                    headers={**video.headers, "Accept-Encoding": "identity"}, timeout=30
                ) as r:
                    if r.status_code != 200:
                        raise ResolverError("DOWNLOAD_FAILED", f"CDN 返回 HTTP {r.status_code}，需要完整文件响应。")
                    expected = int(r.headers.get("Content-Length") or 0)
                    if expected > max_bytes:
                        raise ResolverError("FILE_TOO_LARGE", "视频超过下载大小限制。")
                    total = 0
                    for chunk in r.iter_content(64 * 1024):
                        if not chunk:
                            continue
                        if time.monotonic() > deadline:
                            raise ResolverError("DOWNLOAD_TIMEOUT", "下载超时。")
                        if total == 0 and not is_mp4(chunk):
                            raise ResolverError("NOT_VIDEO", "CDN 返回的内容不是 MP4。")
                        total += len(chunk)
                        if total > max_bytes:
                            raise ResolverError("FILE_TOO_LARGE", "视频超过下载大小限制。")
                        output.write(chunk)
                    if total < 12 or (expected and total != expected) or (candidate.size and total != candidate.size):
                        raise ResolverError("INCOMPLETE_DOWNLOAD", "视频大小不完整，未保存为 MP4。")
                os.replace(temporary, target)
                video.candidates.remove(candidate)
                video.candidates.insert(0, candidate)
                video.verified = True
                return target.resolve()
            except (requests.RequestException, ResolverError, ValueError) as exc:
                last_error = str(exc) if isinstance(exc, ResolverError) else "网络传输失败"
                if isinstance(exc, ResolverError) and exc.code in ("FILE_TOO_LARGE", "DOWNLOAD_TIMEOUT"):
                    raise
            finally:
                Path(temporary).unlink(missing_ok=True)
            if time.monotonic() > deadline:
                break
    finally:
        if own:
            session.close()
    raise ResolverError("DOWNLOAD_FAILED", f"所有下载地址失败：{last_error}")
