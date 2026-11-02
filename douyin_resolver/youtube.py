"""YouTube metadata via yt-dlp; expose only complete, directly readable MP4s."""
import json
import math
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

from .core import Candidate, DEFAULT_UA, ResolverError, Video, YOUTUBE_HOSTS, valid_url, video_id


def is_youtube(url):
    return (urlsplit(url).hostname or "").lower() in YOUTUBE_HOSTS


def video_from(info, ident, quality="compatible"):
    if not isinstance(info, dict) or info.get("id") != ident or info.get("_type", "video") != "video":
        raise ResolverError("ID_MISMATCH", "返回的 YouTube 视频与输入链接不匹配。")
    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming", "post_live"):
        raise ResolverError("UNSUPPORTED_MEDIA", "暂不支持 YouTube 直播或尚未处理完成的回放。")
    candidates, seen, user_agents = [], set(), {}
    default_ua = (info.get("http_headers") or {}).get("User-Agent") or DEFAULT_UA
    for fmt in info.get("formats") or []:
        url = fmt.get("url")
        if not isinstance(url, str) or not valid_url(url, media=True):
            continue
        parsed = urlsplit(url)
        host = parsed.hostname
        if (parsed.scheme != "https" or not (host == "googlevideo.com" or host.endswith(".googlevideo.com"))
                or parsed.path != "/videoplayback" or fmt.get("ext") != "mp4"
                or fmt.get("protocol") != "https" or fmt.get("has_drm")
                or not fmt.get("vcodec") or fmt["vcodec"] == "none"
                or not fmt.get("acodec") or fmt["acodec"] == "none"
                or fmt.get("fragments") or url in seen):
            continue
        seen.add(url)
        user_agents[url] = (fmt.get("http_headers") or {}).get("User-Agent") or default_ua
        candidates.append(Candidate(url, int(fmt.get("width") or 0), int(fmt.get("height") or 0),
                                    int((fmt.get("tbr") or 0) * 1000), int(fmt.get("filesize") or 0),
                                    fmt["vcodec"], "youtube"))
    candidates.sort(key=lambda c: (c.codec.startswith(("avc1", "h264")) if quality == "compatible" else True,
                                   c.width * c.height, c.bitrate), reverse=True)
    if not candidates:
        raise ResolverError("NO_PROGRESSIVE_MP4", "YouTube 未提供含声音的完整 MP4 直链；当前不支持分离音视频流合并。")
    # Video has one shared header set, so fallbacks must use the same client UA.
    ua = user_agents[candidates[0].url]
    candidates = [candidate for candidate in candidates if user_agents[candidate.url] == ua]
    expiries = []
    for candidate in candidates:
        value = parse_qs(urlsplit(candidate.url).query).get("expire", [""])[0]
        if re.fullmatch(r"\d{1,12}", value):
            expiries.append(int(value))
    # Return only the two headers supported by the Shortcut. Never expose cookies.
    return Video(ident, f"https://www.youtube.com/watch?v={ident}", info.get("title") or "",
                 info.get("uploader") or info.get("channel") or "", float(info.get("duration") or 0),
                 candidates, {"User-Agent": ua,
                              "Referer": "https://www.youtube.com/"},
                 datetime.now(timezone.utc).isoformat(), min(expiries) if expiries else None,
                 strategy="yt-dlp")


def fetch(ident, url, *, quality="compatible", timeout=35):
    # Canonical input + a single extractor prevents playlist/unrelated-site extraction.
    if not is_youtube(url) or video_id(url) != ident:
        raise ResolverError("INVALID_URL", "请提供 YouTube 单条视频链接。")
    runtime = "deno" if shutil.which("deno") else "node" if shutil.which("node") else None
    if runtime is None:
        raise ResolverError("YOUTUBE_RUNTIME_REQUIRED", "YouTube 解析需要在服务器安装 Deno 或 Node.js 22+。")
    command = [sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-plugin-dirs",
               "--no-playlist", "--skip-download", "--dump-single-json", "--quiet", "--no-warnings",
               "--no-cache-dir", "--socket-timeout", str(min(timeout, 10)), "--retries", "0",
               "--extractor-retries", "0", "--js-runtimes", runtime, "--use-extractors", "Youtube",
               "--ignore-no-formats-error", "--", f"https://www.youtube.com/watch?v={ident}"]
    deadline = time.monotonic() + timeout
    try:
        result = subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace",
                                timeout=timeout, check=False,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise ResolverError("NETWORK_TIMEOUT", "YouTube 解析超时，请稍后重试。") from None
    except OSError:
        raise ResolverError("YOUTUBE_UNAVAILABLE", "无法启动 YouTube 解析器，请检查服务器安装。") from None
    if result.returncode:
        if "No module named yt_dlp" in result.stderr:
            raise ResolverError("YOUTUBE_DEPENDENCY_REQUIRED", "请在服务器安装或更新 yt-dlp[default]。")
        # Upstream errors may contain cookies, local paths or signed CDN URLs.
        raise ResolverError("YOUTUBE_UNAVAILABLE", "无法取得 YouTube 视频；请检查是否公开、地区限制或需要登录，并更新服务器的 yt-dlp。")
    try:
        info = json.loads(result.stdout)
    except ValueError:
        raise ResolverError("NO_VIDEO_DATA", "YouTube 解析器未返回有效视频详情。") from None
    video = video_from(info, ident, quality)
    urls = {candidate.url for candidate in video.candidates}
    available_at = 0
    for fmt in info.get("formats") or []:
        if fmt.get("url") not in urls:
            continue
        timestamp = fmt.get("available_at")
        if timestamp is None:
            continue
        if not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp):
            raise ResolverError("NO_VIDEO_DATA", "YouTube 返回的视频可用时间无效。")
        available_at = max(available_at, timestamp)
    # yt-dlp's downloader honors this timestamp, but --skip-download bypasses it.
    # Match its whole-second clock so a fresh URL is never probed too early.
    delay = available_at - int(time.time())
    if delay > 0:
        if delay >= deadline - time.monotonic():
            raise ResolverError("NETWORK_TIMEOUT", "YouTube 视频尚未就绪，等待时间超过解析时限，请稍后重试。")
        time.sleep(delay)
    return video
