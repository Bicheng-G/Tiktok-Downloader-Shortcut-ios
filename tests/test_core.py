import tempfile
import unittest
from pathlib import Path

import requests

from douyin_resolver.core import (Candidate, ResolverError, Video, candidates_from,
                                  checked_get, download, extract_url, find_aweme,
                                  normalize, valid_url, verify, video_from, video_id)

ID = "7686432847778982833"
PAGE = f"https://www.douyin.com/video/{ID}"
A = "https://v3-dy-o.zjcdn.com/video/a/?signature=keep%2Fexact&x=1"
B = "https://v5-dy-ov-experiment.zjcdn.com/video/b/"
MP4 = b"\x00\x00\x00\x18ftypisom" + b"0" * 64
USER_WEB_URL = "https://www.douyin.com/jingxuan?modal_id=7688235974236654911"
USER_SHORT_URL = "https://v.douyin.com/p-rXmps4nWc/"
USER_SHARE_TEXT = (
    "9.92 复制打开抖音，看看【晓辉博士的作品】9分钟新书解读《Token经济》 "
    "我们写的《Tok... https://v.douyin.com/p-rXmps4nWc/ 07/17 :4pm q@e.oD ndN:/"
)


class Response:
    def __init__(self, body=MP4, status=200, headers=None, url=A, broken=False):
        self.status_code, self.url, self.body = status, url, body
        self.headers = headers or {}
        self.closed, self.broken = False, broken

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self.closed = True

    def iter_content(self, size):
        yield self.body
        if self.broken:
            raise requests.ConnectionError("interrupted")


class Session:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def video(*urls):
    return Video(ID, PAGE, "title", "author", 1, [Candidate(u) for u in urls], {}, "now")


def metadata():
    return {"aweme_id": ID, "video": {"bit_rate": [
        {"format": "mp4", "is_h265": 1, "bit_rate": 300,
         "play_addr": {"width": 2560, "height": 1440, "url_list": [B]}},
        {"format": "mp4", "is_h265": 0, "bit_rate": 200,
         "play_addr": {"width": 1920, "height": 1080, "url_list": [A]}},
        {"format": "dash", "bit_rate": 999,
         "play_addr": {"width": 4000, "height": 3000, "url_list": [A + "&dash=1"]}},
    ], "play_addr": {"url_list": [A]}, "download_addr": {"url_list": [B + "?watermark=1"]}}}


class URLTests(unittest.TestCase):
    def test_user_jingxuan_example(self):
        self.assertEqual(normalize(USER_WEB_URL),
                         ("7688235974236654911", "https://www.douyin.com/video/7688235974236654911"))

    def test_user_complete_mobile_share_text(self):
        self.assertEqual(extract_url(USER_SHARE_TEXT), USER_SHORT_URL)

    def test_user_mobile_share_redirect(self):
        # ID observed by expanding the user's real short link on 2026-09-24.
        mobile_id = "7680575887263617273"
        landing = f"https://www.iesdouyin.com/share/video/{mobile_id}/?region=SG"
        for text in (USER_SHORT_URL, USER_SHARE_TEXT, f"“{USER_SHARE_TEXT}”"):
            with self.subTest(text=text):
                session = Session(Response(status=302, headers={"Location": landing}), Response(url=landing))
                self.assertEqual(normalize(text, session),
                                 (mobile_id, f"https://www.douyin.com/video/{mobile_id}"))
                self.assertEqual(session.calls[0][0], USER_SHORT_URL)

    def test_share_text(self):
        self.assertEqual(extract_url(f"3.21 复制打开抖音 {PAGE}， 看视频"), PAGE)

    def test_known_formats(self):
        for url in (PAGE, f"https://www.douyin.com/jingxuan?modal_id={ID}",
                    f"https://www.iesdouyin.com/share/video/{ID}/?region=CN"):
            with self.subTest(url=url):
                self.assertEqual(normalize(url), (ID, PAGE))

    def test_homepage_rejected(self):
        with self.assertRaises(ResolverError) as error:
            normalize("https://www.douyin.com/jingxuan")
        self.assertEqual(error.exception.code, "VIDEO_URL_REQUIRED")

    def test_host_and_scheme_validation(self):
        for url in ("http://127.0.0.1/video/" + ID, "file:///etc/passwd",
                    "https://douyin.com.evil.test/video/" + ID,
                    "https://user:password@www.douyin.com/video/" + ID,
                    "https://www.douyin.com:8000/video/" + ID,
                    "https://www.douyin.com/video/" + ID + "/extra"):
            with self.subTest(url=url):
                self.assertIsNone(video_id(url))
        self.assertFalse(valid_url("https://v3.zjcdn.com.evil.test/a", media=True))

    def test_short_link_redirect(self):
        session = Session(Response(status=302, headers={"Location": PAGE}), Response(url=PAGE))
        self.assertEqual(normalize("https://v.douyin.com/abc/", session), (ID, PAGE))
        self.assertEqual(len(session.calls), 2)

    def test_redirect_does_not_fetch_foreign_host(self):
        response = Response(status=302, headers={"Location": "http://127.0.0.1/secret"})
        session = Session(response)
        with self.assertRaises(ResolverError):
            checked_get(session, A, media=True)
        self.assertEqual(len(session.calls), 1)
        self.assertTrue(response.closed)


class MetadataTests(unittest.TestCase):
    def test_preload_cannot_replace_target(self):
        unrelated = {"aweme_id": "1111111111111111111", "video": {}}
        target = metadata()
        self.assertEqual(find_aweme({"aweme_list": [unrelated, target]}, ID), target)
        self.assertIsNone(find_aweme({"aweme_detail": unrelated}, ID))

    def test_choose_compatible_and_preserve_signature(self):
        result = candidates_from(metadata())
        self.assertEqual(result[0].url, A)
        self.assertEqual(result[0].width, 1920)
        self.assertEqual(len(result), 3)
        self.assertFalse(any("dash=1" in c.url for c in result))

    def test_best_uses_resolution(self):
        self.assertEqual(candidates_from(metadata(), "best")[0].url, B)

    def test_exclude_split_tracks_even_without_format(self):
        item = {"video": {"play_addr": {"url_list": ["https://v3.zjcdn.com/media-video-avc1/?x=1"]}}}
        with self.assertRaises(ResolverError):
            candidates_from(item)

    def test_gallery_is_explicit_error(self):
        item = metadata()
        item["images"] = [{}]
        with self.assertRaises(ResolverError) as error:
            candidates_from(item)
        self.assertEqual(error.exception.code, "UNSUPPORTED_MEDIA")

    def test_mismatched_id_is_error(self):
        with self.assertRaises(ResolverError):
            video_from(metadata(), "1111111111111111111", "UA")


class TransferTests(unittest.TestCase):
    def test_probe_falls_back_from_html_and_promotes_url(self):
        item = video(A, B)
        verify(item, Session(Response(b"<html>error</html>"), Response()))
        self.assertEqual(item.candidates[0].url, B)
        self.assertTrue(item.verified)

    def test_probe_rejects_wrong_range(self):
        with self.assertRaises(ResolverError):
            verify(video(A), Session(Response(status=206, headers={"Content-Range": "bytes 4096-8191/10000"})))

    def test_complete_file_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = download(video(A), directory, session=Session(Response(headers={"Content-Length": str(len(MP4))})))
            self.assertEqual(path.read_bytes(), MP4)
            self.assertEqual(path.suffix, ".mp4")
            self.assertFalse(list(Path(directory).glob("*.part")))

    def test_truncated_response_is_not_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ResolverError):
                download(video(A), directory, session=Session(Response(headers={"Content-Length": "9999"})))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_metadata_size_is_checked(self):
        item = video(A)
        item.candidates[0].size = 9999
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ResolverError):
                download(item, directory, session=Session(Response()))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_partial_http_response_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ResolverError):
                download(video(A), directory, session=Session(Response(status=206)))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_stream_failure_falls_back_and_cleans_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            item = video(A, B)
            path = download(item, directory, session=Session(Response(broken=True), Response()))
            self.assertEqual(path.read_bytes(), MP4)
            self.assertEqual(len(list(Path(directory).iterdir())), 1)
            self.assertEqual(item.to_dict()["download_url"], B)

    def test_limit_and_html_errors_leave_no_files(self):
        for response, limit in ((Response(), 12), (Response(b"<html>not video</html>"), 9999)):
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(ResolverError):
                    download(video(A), directory, max_bytes=limit, session=Session(response))
                self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
