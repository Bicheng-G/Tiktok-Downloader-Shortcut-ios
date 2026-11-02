import copy
import json
import re
import subprocess
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from douyin_resolver.core import ResolverError, checked_get, normalize, valid_url, video_id
from douyin_resolver.server import Settings, create_app
from douyin_resolver.service import Resolver
from douyin_resolver.youtube import fetch, video_from
from scripts.build_shortcut import URL_PATTERN
from test_core import MP4, Response, Session

ID = "3QZOazpGIQc"
PAGE = f"https://www.youtube.com/watch?v={ID}"
CDN = "https://rr1---sn-test.googlevideo.com/videoplayback?expire=4102444800&sig=keep%2Fexact"


def metadata():
    return {"id": ID, "title": "Example", "uploader": "Artist", "duration": 125,
            "formats": [{"url": CDN, "ext": "mp4", "protocol": "https", "vcodec": "avc1.42001E",
                         "acodec": "mp4a.40.2", "width": 640, "height": 360, "tbr": 300}],
            "http_headers": {"User-Agent": "test-UA", "Cookie": "private"}}


class YouTubeURLTests(unittest.TestCase):
    def test_variants_and_shortcut_accept_user_examples(self):
        for url in (f"music.youtube.com/watch?v={ID}", f"youtube.com/watch?v={ID}", PAGE,
                    f"https://m.youtube.com/watch?v={ID}&list=ignored", f"https://youtu.be/{ID}?si=tracking",
                    f"https://www.youtube.com/shorts/{ID}", f"https://www.youtube.com/embed/{ID}"):
            with self.subTest(url=url):
                self.assertEqual(normalize(f"分享 {url}。"), (ID, PAGE))
                self.assertEqual(re.findall(URL_PATTERN, f"分享 {url}。", re.I), [url])

    def test_reject_invalid_ids_playlists_and_spoofed_domains(self):
        for url in ("https://youtube.com/playlist?list=test", "https://youtube.com/watch?v=short",
                    PAGE + "&v=abcdefghijk", "https://youtu.be/abcdefghijk/extra",
                    "https://youtube.com.evil.test/watch?v=" + ID,
                    "https://evil.test/youtube.com/watch?v=" + ID,
                    "https://evil.test@youtube.com/watch?v=" + ID,
                    "ftp://youtube.com/watch?v=" + ID, "https://youtube.com:444/watch?v=" + ID):
            with self.subTest(url=url), self.assertRaises(ResolverError):
                normalize(url)
        self.assertIsNone(video_id("https://youtube.com/watch?v=bad"))


class YouTubeMetadataTests(unittest.TestCase):
    def test_complete_mp4_metadata_expiry_and_no_cookie_leak(self):
        video = video_from(metadata(), ID)
        self.assertEqual(video.to_dict()["download_url"], CDN)
        self.assertEqual(video.expires_at, 4102444800)
        self.assertEqual(video.headers, {"User-Agent": "test-UA", "Referer": "https://www.youtube.com/"})
        self.assertEqual(video.strategy, "yt-dlp")

    def test_only_complete_unencrypted_googlevideo_mp4_is_exposed(self):
        cases = ({"acodec": "none"}, {"vcodec": "none"}, {"acodec": None}, {"has_drm": True},
                 {"protocol": "m3u8_native"}, {"ext": "webm"}, {"fragments": [{"url": CDN}]},
                 {"url": "https://googlevideo.com.evil.test/videoplayback"},
                 {"url": "http://rr1.googlevideo.com/videoplayback"},
                 {"url": "https://127.0.0.1/videoplayback"},
                 {"url": "https://rr1.googlevideo.com/manifest"})
        for changes in cases:
            info = metadata()
            info["formats"][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ResolverError) as caught:
                video_from(info, ID)
            self.assertEqual(caught.exception.code, "NO_PROGRESSIVE_MP4")

    def test_exact_id_and_no_live_or_playlists(self):
        for changes in ({"id": "abcdefghijk"}, {"_type": "playlist"}, {"is_live": True},
                        {"live_status": "is_upcoming"}):
            with self.subTest(changes=changes), self.assertRaises(ResolverError):
                video_from({**metadata(), **changes}, ID)

    def test_quality_sort_and_deduplication(self):
        info = metadata()
        higher = {**info["formats"][0], "url": CDN + "&hd=1", "vcodec": "av01", "height": 1080, "width": 1920}
        info["formats"] += [higher, copy.deepcopy(higher)]
        self.assertEqual(len(video_from(info, ID).candidates), 2)
        self.assertEqual(video_from(info, ID).candidates[0].height, 360)
        self.assertEqual(video_from(info, ID, "best").candidates[0].height, 1080)

    def test_format_specific_user_agent_is_preserved_for_download_and_fallbacks(self):
        info = metadata()
        info["formats"][0]["http_headers"] = {"User-Agent": "format-UA", "Cookie": "private"}
        info["formats"].append({**info["formats"][0], "url": CDN + "&other=1", "http_headers": {
            "User-Agent": "other-UA"}, "width": 320, "height": 180})
        result = video_from(info, ID)
        self.assertEqual(result.headers["User-Agent"], "format-UA")
        self.assertNotIn("Cookie", result.headers)
        self.assertEqual([c.url for c in result.candidates], [CDN])

    def test_media_redirect_cannot_escape_allowlist(self):
        self.assertTrue(valid_url(CDN, media=True))
        session = Session(Response(status=302, headers={"Location": "https://127.0.0.1/private"}))
        with self.assertRaises(ResolverError):
            checked_get(session, CDN, media=True)
        self.assertEqual(len(session.calls), 1)


class YouTubeExtractorTests(unittest.TestCase):
    @patch("douyin_resolver.youtube.shutil.which", return_value="runtime")
    @patch("douyin_resolver.youtube.subprocess.run")
    def test_bounded_subprocess_uses_canonical_single_video(self, run, runtime):
        run.return_value = Mock(returncode=0, stdout=json.dumps(metadata()))
        self.assertEqual(fetch(ID, PAGE).id, ID)
        command = run.call_args.args[0]
        self.assertEqual(command[-1], PAGE)
        for flag in ("--ignore-config", "--no-plugin-dirs", "--no-playlist", "--skip-download"):
            self.assertIn(flag, command)
        self.assertEqual(run.call_args.kwargs["timeout"], 35)

    @patch("douyin_resolver.youtube.shutil.which", return_value="runtime")
    @patch("douyin_resolver.youtube.subprocess.run")
    def test_timeout_errors_and_upstream_details_are_not_exposed(self, run, runtime):
        for result, code in ((Mock(returncode=1, stderr="SECRET signed URL"), "YOUTUBE_UNAVAILABLE"),
                             (Mock(returncode=1, stderr="No module named yt_dlp"), "YOUTUBE_DEPENDENCY_REQUIRED"),
                             (Mock(returncode=0, stdout="invalid"), "NO_VIDEO_DATA")):
            run.return_value = result
            with self.assertRaises(ResolverError) as caught:
                fetch(ID, PAGE)
            self.assertEqual(caught.exception.code, code)
            self.assertNotIn("SECRET", str(caught.exception))
        run.side_effect = subprocess.TimeoutExpired("command", 35)
        with self.assertRaises(ResolverError) as caught:
            fetch(ID, PAGE)
        self.assertEqual(caught.exception.code, "NETWORK_TIMEOUT")

    def test_missing_runtime_is_actionable(self):
        with patch("douyin_resolver.youtube.shutil.which", return_value=None), self.assertRaises(ResolverError) as caught:
            fetch(ID, PAGE)
        self.assertEqual(caught.exception.code, "YOUTUBE_RUNTIME_REQUIRED")


class YouTubeServiceTests(unittest.TestCase):
    def test_both_domains_share_cache_without_douyin_or_browser_capture(self):
        for engine in ("http", "auto", "browser"):
            with self.subTest(engine=engine), Resolver(engine=engine, check=False) as resolver, patch(
                    "douyin_resolver.youtube.fetch", return_value=video_from(metadata(), ID)) as extractor, patch(
                    "douyin_resolver.service.fetch") as douyin, patch(
                    "douyin_resolver.browser.BrowserWorker.capture") as browser:
                resolver.resolve(f"music.youtube.com/watch?v={ID}")
                self.assertTrue(resolver.resolve(f"youtube.com/watch?v={ID}").cache_hit)
                resolver.resolve(PAGE, refresh=True)
                self.assertEqual(extractor.call_count, 2)
                douyin.assert_not_called()
                browser.assert_not_called()

    def test_api_returns_verified_mp4_using_real_service_routing(self):
        with Resolver(engine="http") as resolver, patch("douyin_resolver.youtube.fetch", return_value=video_from(
                metadata(), ID)), patch("douyin_resolver.core.requests.Session", return_value=Mock(
                    get=Session(Response(MP4, url=CDN)).get)), TestClient(create_app(
                        Settings(token="youtube-test-token-at-least-24"), resolver)) as client:
            response = client.post("/resolve", headers={"Authorization": "Bearer youtube-test-token-at-least-24"},
                                   json={"url": f"music.youtube.com/watch?v={ID}"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["download_url"], CDN)
            self.assertTrue(response.json()["verified"])


class YouTubeAvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.info = metadata()
        self.info["formats"][0]["available_at"] = 1005
        self.clock = Mock()
        self.clock.time.return_value = 1000.25
        self.clock.monotonic.return_value = 100

        def sleep(seconds):
            self.clock.time.return_value += seconds
            self.clock.monotonic.return_value += seconds

        self.clock.sleep.side_effect = sleep
        self.run = Mock(side_effect=lambda *a, **kw: Mock(returncode=0, stdout=json.dumps(self.info)))
        self.patches = [patch("douyin_resolver.youtube.time", self.clock, create=True),
                        patch("douyin_resolver.youtube.shutil.which", return_value="runtime"),
                        patch("douyin_resolver.youtube.subprocess.run", self.run)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_service_waits_for_format_availability_before_first_cdn_probe(self):
        def get(url, **kwargs):
            ready = self.clock.time() >= 1005
            return Response(MP4 if ready else b"Forbidden", status=206 if ready else 403,
                            headers={"Content-Range": "bytes 0-1023/9999"} if ready else {}, url=url)

        session = Mock(get=Mock(side_effect=get))
        with Resolver(engine="http") as resolver, patch(
                "douyin_resolver.core.requests.Session", return_value=session):
            result = resolver.resolve(PAGE)
        self.assertTrue(result.verified)
        session.get.assert_called_once()
        self.clock.sleep.assert_called_once_with(5)
        self.run.assert_called_once()

    def test_unverified_resolution_also_returns_only_ready_urls(self):
        self.assertEqual(fetch(ID, PAGE).id, ID)
        self.clock.sleep.assert_called_once_with(5)

    def test_already_available_or_missing_timestamp_does_not_wait(self):
        for timestamp in (999, None):
            with self.subTest(timestamp=timestamp):
                self.info["formats"][0]["available_at"] = timestamp
                fetch(ID, PAGE)
                self.clock.sleep.assert_not_called()

    def test_ignored_split_track_does_not_delay_complete_mp4(self):
        self.info["formats"][0]["available_at"] = 999
        self.info["formats"].append({**self.info["formats"][0], "url": CDN + "&audio=1",
                                     "vcodec": "none", "available_at": 2000})
        fetch(ID, PAGE)
        self.clock.sleep.assert_not_called()

    def test_wait_cannot_exceed_time_left_after_extraction(self):
        def slow_extract(*args, **kwargs):
            self.clock.monotonic.return_value = 133
            return Mock(returncode=0, stdout=json.dumps(self.info))

        self.run.side_effect = slow_extract
        with self.assertRaises(ResolverError) as caught:
            fetch(ID, PAGE, timeout=35)
        self.assertEqual(caught.exception.code, "NETWORK_TIMEOUT")
        self.clock.sleep.assert_not_called()

    def test_malformed_availability_cannot_trigger_an_unbounded_wait(self):
        for timestamp in ("later", float("inf"), float("nan")):
            self.info["formats"][0]["available_at"] = timestamp
            with self.subTest(timestamp=timestamp):
                with self.assertRaises(ResolverError) as caught:
                    fetch(ID, PAGE)
                self.assertEqual(caught.exception.code, "NO_VIDEO_DATA")
        self.clock.sleep.assert_not_called()
