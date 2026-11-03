import json
import unittest
from unittest.mock import Mock, patch

import requests

from douyin_resolver import instagram
from douyin_resolver.browser import capture_page
from douyin_resolver.core import ResolverError, extract_url, normalize, valid_url, verify, video_id
from douyin_resolver.service import Resolver
from test_core import ID as DOUYIN_ID, PAGE as DOUYIN_PAGE, Response, Session, metadata

ID = "DcaIKqURjoi"
PAGE = f"https://www.instagram.com/reel/{ID}/"
USER_URL = PAGE + "?stkn=MXVmZDF2aWdvZng0dQ=="
MP4_URL = "https://scontent.cdninstagram.com/video.mp4?sig=keep%2Fexact&oe=7FFFFFFF"


def media(ident=ID):
    return {"code": ident, "pk": instagram.media_id(ident), "media_type": 2,
            "caption": {"text": "Example Reel"}, "user": {"username": "example"},
            "video_duration": 12.5, "original_width": 720, "original_height": 1280,
            "video_versions": [{"url": MP4_URL, "type": 101}]}


def html(item):
    return '<script type="application/json" data-sjs>' + json.dumps({"data": item}) + '</script>'


class InstagramURLTests(unittest.TestCase):
    def test_user_link_and_share_message(self):
        self.assertEqual(normalize(USER_URL), (ID, PAGE))
        self.assertEqual(extract_url(f"看这个视频 {USER_URL}，"), USER_URL)

    def test_supported_paths(self):
        for kind in ("reel", "reels", "p", "tv"):
            url = f"https://instagram.com/{kind}/{ID}/?igsh=tracking"
            self.assertEqual(video_id(url), ID)
            self.assertEqual(normalize(url)[1], f"https://www.instagram.com/{'reel' if kind == 'reels' else kind}/{ID}/")

    def test_non_video_paths_and_spoofed_urls(self):
        for url in ("https://instagram.com/", "https://instagram.com/example/",
                    "https://instagram.com/stories/example/123/", PAGE + "extra/",
                    "https://instagram.com/reel/audio/123/",
                    "https://instagram.com/reel/../", "https://instagram.com/reel/" + "A" * 29,
                    f"https://instagram.com.evil.test/reel/{ID}/",
                    f"https://user:secret@instagram.com/reel/{ID}/",
                    f"https://instagram.com:8765/reel/{ID}/"):
            with self.subTest(url=url):
                self.assertIsNone(video_id(url))
                with self.assertRaises(ResolverError):
                    normalize(url)

    def test_cdn_validation(self):
        self.assertTrue(valid_url(MP4_URL, media=True))
        self.assertTrue(valid_url("https://video.xx.fbcdn.net/a.mp4", media=True))
        for url in ("https://cdninstagram.com.evil.test/a.mp4", "http://127.0.0.1/a.mp4",
                    "https://video.fbcdn.net:8765/a.mp4"):
            self.assertFalse(valid_url(url, media=True))


class InstagramMetadataTests(unittest.TestCase):
    def test_target_matching_ignores_recommendations_and_routing_data(self):
        target, wrong = media(), media("OtherCode")
        self.assertEqual(instagram.find_media({"routing": {"shortcode": ID},
                                             "items": [target, wrong]}, ID), target)
        self.assertIsNone(instagram.find_media({"items": [wrong]}, ID))
        wrong["pk"] = instagram.media_id(ID)
        self.assertIsNone(instagram.find_media(wrong, ID))

    def test_numeric_id_matches_modern_nested_response(self):
        item = media()
        del item["code"]
        item["id"] = item.pop("pk") + "_123456"
        self.assertEqual(instagram.find_media({"if_not_gated_logged_out": item}, ID), item)

    def test_page_json_and_legacy_embed_assignments(self):
        for raw in (html(media()), '<script>window._sharedData = ' + json.dumps(media()) + ';</script>',
                    '<script>window.__additionalDataLoaded("/p/' + ID + '/",' + json.dumps(media()) + ');</script>'):
            self.assertEqual(instagram.parse_page(raw, ID), media())
        self.assertIsNone(instagram.parse_page('<script>{broken}</script>' + html(media("OtherCode")), ID))

    def test_metadata_signature_dimensions_and_expiry(self):
        result = instagram.video_from(media(), ID, "UA")
        self.assertEqual(result.candidates[0].url, MP4_URL)
        self.assertEqual((result.candidates[0].width, result.candidates[0].height), (720, 1280))
        self.assertEqual((result.title, result.author, result.duration_seconds), ("Example Reel", "example", 12.5))
        self.assertEqual(result.headers["Referer"], "https://www.instagram.com/")
        self.assertEqual(result.expires_at, 2147483647)
        self.assertEqual(result.to_dict()["filename"], ID + ".mp4")
        self.assertTrue(verify(result, Session(Response())).verified)

    def test_legacy_video_url_and_caption(self):
        item = {"shortcode": ID, "is_video": True, "video_url": MP4_URL,
                "dimensions": {"width": 1080, "height": 1920},
                "edge_media_to_caption": {"edges": [{"node": {"text": "caption"}}]},
                "owner": {"username": "owner"}}
        result = instagram.video_from(item, ID, "UA")
        self.assertEqual((result.title, result.author), ("caption", "owner"))
        self.assertEqual(result.candidates[0].height, 1920)

    def test_quality_sort_dedup_and_unsafe_or_manifest_exclusion(self):
        item = media()
        best = "https://video.fbcdn.net/best.mp4"
        item["video_versions"] += [
            {"url": MP4_URL}, {"url": best, "width": 1080, "height": 1920},
            {"url": "https://video.fbcdn.net/a.mpd"}, {"url": "blob:https://instagram.com/abc"},
            {"url": "http://127.0.0.1/a.mp4"}, {"url": "https://v.zjcdn.com/a.mp4"}]
        result = instagram.video_from(item, ID, "UA", "best")
        self.assertEqual([c.url for c in result.candidates], [best, MP4_URL])

    def test_images_carousels_mismatch_and_missing_mp4(self):
        for changes, code in (({"media_type": 1}, "UNSUPPORTED_MEDIA"),
                              ({"carousel_media": [media()]}, "UNSUPPORTED_MEDIA"),
                              ({"video_versions": []}, "NO_PROGRESSIVE_MP4"),
                              ({"code": "OtherCode"}, "ID_MISMATCH")):
            item = {**media(), **changes}
            with self.assertRaises(ResolverError) as caught:
                instagram.video_from(item, ID, "UA")
            self.assertEqual(caught.exception.code, code)


class InstagramHTTPTests(unittest.TestCase):
    def test_page_then_embed_fallback(self):
        session = Session(Response(html(media("OtherCode")).encode(), url=PAGE),
                          Response(html(media()).encode(), url=PAGE + "embed/"))
        self.assertEqual(instagram.fetch(ID, PAGE, session=session)[0], media())
        self.assertEqual(len(session.calls), 2)
        self.assertFalse(session.calls[0][1]["allow_redirects"])

    def test_graphql_exact_target_and_no_redirect(self):
        token_page = '<script>["LSD",[],{"token":"test-token"}]</script>'
        session = Session(Response(token_page.encode(), url=PAGE))
        session.post = Mock(return_value=Response(json.dumps({"data": media()}).encode()))
        self.assertEqual(instagram.fetch(ID, PAGE, session=session)[0], media())
        kwargs = session.post.call_args.kwargs
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(json.loads(kwargs["data"]["variables"]), {"media_id": instagram.media_id(ID)})

    def test_login_error_and_unsafe_redirect(self):
        session = Session(Response(status=403), Response(status=429))
        with self.assertRaises(ResolverError) as caught:
            instagram.fetch(ID, PAGE, session=session)
        self.assertEqual(caught.exception.code, "LOGIN_REQUIRED")
        session = Session(Response(status=302, headers={"Location": "http://127.0.0.1/"}))
        with self.assertRaises(ResolverError) as caught:
            instagram.fetch(ID, PAGE, session=session)
        self.assertEqual(caught.exception.code, "UNSAFE_REDIRECT")
        self.assertEqual(len(session.calls), 1)

    def test_oversized_metadata_is_bounded(self):
        with patch.object(instagram, "MAX_METADATA", 16):
            with self.assertRaises(ResolverError):
                instagram.fetch(ID, PAGE, session=Session(Response(b"x" * 17)))

    def test_network_error_is_structured(self):
        session = Mock()
        session.get.side_effect = requests.ConnectionError("offline")
        with self.assertRaises(ResolverError) as caught:
            instagram.fetch(ID, PAGE, session=session)
        self.assertEqual(caught.exception.code, "HTTP_NO_MATCH")


class InstagramServiceTests(unittest.TestCase):
    def test_http_routing_and_shared_cache_for_tracking_links(self):
        with Resolver(engine="http", check=False) as service, patch(
                "douyin_resolver.instagram.fetch", return_value=(media(), "UA")) as fetch, patch(
                "douyin_resolver.service.fetch") as douyin:
            result = service.resolve(USER_URL)
            self.assertEqual((result.id, result.page_url, result.strategy), (ID, PAGE, "http"))
            self.assertTrue(service.resolve(PAGE).cache_hit)
            fetch.assert_called_once_with(ID, PAGE)
            douyin.assert_not_called()

    def test_browser_fallback_and_verification(self):
        with Resolver() as service, patch("douyin_resolver.instagram.fetch", side_effect=ResolverError(
                "LOGIN_REQUIRED", "login")), patch.object(service.browser, "capture", return_value=(media(), "UA")), patch(
                "douyin_resolver.service.verify", side_effect=lambda v: v) as verify_mock:
            result = service.resolve(USER_URL)
            self.assertEqual(result.strategy, "browser")
            verify_mock.assert_called_once()

    def test_unsupported_media_does_not_launch_browser(self):
        with Resolver() as service, patch("douyin_resolver.instagram.fetch", return_value=(
                {**media(), "media_type": 1}, "UA")), patch.object(service.browser, "capture") as capture:
            with self.assertRaises(ResolverError):
                service.resolve(PAGE)
            capture.assert_not_called()

    def test_platform_cache_collision_is_prevented(self):
        numeric_page = f"https://instagram.com/reel/{DOUYIN_ID}/"
        with Resolver(engine="http", check=False) as service, patch(
                "douyin_resolver.instagram.fetch", return_value=(media(DOUYIN_ID), "UA")), patch(
                "douyin_resolver.service.fetch", return_value=(metadata(), "UA")):
            a = service.resolve(DOUYIN_PAGE)
            b = service.resolve(numeric_page)
            self.assertNotEqual(a.headers["Referer"], b.headers["Referer"])
            self.assertFalse(b.cache_hit)
            self.assertEqual(len(service.cache), 2)


class InstagramCaptureTests(unittest.TestCase):
    def page(self):
        page = Mock(url=PAGE)
        page.evaluate.return_value = "UA"
        page.content.return_value = "<html></html>"
        page.locator.return_value.inner_text.return_value = "loading"
        clock = [0.0]
        page.wait_for_timeout.side_effect = lambda ms: clock.__setitem__(0, clock[0] + ms / 1000)
        return page, clock

    def test_completed_graphql_response_and_cleanup(self):
        page, clock = self.page()
        listeners = {}
        page.on.side_effect = lambda event, callback: listeners.update({event: callback})
        request = Mock(url="https://www.instagram.com/graphql/query")
        request.response.return_value.json.return_value = {"data": media()}
        page.goto.side_effect = lambda *a, **kw: listeners["requestfinished"](request)
        with patch("douyin_resolver.instagram.time.monotonic", side_effect=lambda: clock[0]):
            self.assertEqual(capture_page(page, ID, PAGE, 1, RuntimeError), (media(), "UA"))
        page.remove_listener.assert_called_once()

    def test_json_page_and_login_error(self):
        page, clock = self.page()
        page.content.return_value = html(media())
        with patch("douyin_resolver.instagram.time.monotonic", side_effect=lambda: clock[0]):
            self.assertEqual(capture_page(page, ID, PAGE, 1, RuntimeError), (media(), "UA"))
        page, clock = self.page()
        page.url = "https://www.instagram.com/accounts/login/"
        with patch("douyin_resolver.instagram.time.monotonic", side_effect=lambda: clock[0]):
            with self.assertRaises(ResolverError) as caught:
                capture_page(page, ID, PAGE, 1, RuntimeError)
        self.assertEqual(caught.exception.code, "LOGIN_REQUIRED")
