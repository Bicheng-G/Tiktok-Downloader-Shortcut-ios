import asyncio
import time
import unittest
from unittest.mock import Mock, patch

import requests

from douyin_resolver.core import ResolverError
from douyin_resolver.telegram_bot import TelegramAPI, TelegramBot, TelegramError
from test_core import A, ID, video


class FakeAPI:
    def __init__(self):
        self.calls = []
        self.next_message_id = 51

    def call(self, method, payload):
        self.calls.append((method, payload))
        if method == "sendMessage":
            self.next_message_id += 1
            return {"message_id": self.next_message_id}
        return True


def message(text, user=123):
    return {"message": {"message_id": 7, "chat": {"id": user, "type": "private"},
                        "from": {"id": user}, "text": text}}


def callback(user=123, data=f"refresh:{ID}"):
    return {"callback_query": {"id": "callback-1", "from": {"id": user},
                               "data": data, "message": {"message_id": 52,
                                                        "chat": {"id": user, "type": "private"}}}}


class TelegramBotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.api = FakeAPI()
        self.resolver = Mock()
        self.resolver.resolve.return_value = video(A)
        self.bot = TelegramBot(api=self.api, resolver=self.resolver,
                               allowed_user_ids=(123,), user_interval=5)

    async def test_authorized_share_text_returns_direct_link_and_refresh(self):
        await self.bot.accept_update(message("分享文案 https://v.douyin.com/example/"))
        self.assertEqual(self.bot.queue.qsize(), 1)
        await self.bot.handle_job(*self.bot.queue.get_nowait())
        self.resolver.resolve.assert_called_once_with("分享文案 https://v.douyin.com/example/",
                                                      refresh=False)
        method, payload = self.api.calls[-1]
        self.assertEqual(method, "editMessageText")
        buttons = payload["reply_markup"]["inline_keyboard"]
        self.assertEqual(buttons[0][0]["url"], A)
        self.assertEqual(buttons[1][0]["callback_data"], f"refresh:{ID}")
        self.assertEqual(payload["message_id"], 52)
        self.assertEqual(self.api.calls[0][1]["reply_parameters"]["message_id"], 7)

    async def test_refresh_forces_new_resolution(self):
        await self.bot.accept_update(callback())
        await self.bot.handle_job(*self.bot.queue.get_nowait())
        self.resolver.resolve.assert_called_once_with(
            f"https://www.douyin.com/video/{ID}", refresh=True)
        self.assertEqual(self.api.calls[0][0], "answerCallbackQuery")
        self.assertEqual(self.api.calls[-1][1]["reply_markup"]["inline_keyboard"][0][0]["url"], A)

    async def test_private_allowlist_and_consecutive_messages(self):
        await self.bot.accept_update(message("first", user=999))
        group = message("first")
        group["message"]["chat"]["type"] = "group"
        await self.bot.accept_update(group)
        self.assertEqual(self.bot.queue.qsize(), 0)
        self.assertEqual(self.api.calls, [])
        await self.bot.accept_update(message("first"))
        await self.bot.accept_update(message("second"))
        self.assertEqual(self.bot.queue.qsize(), 2)
        self.assertEqual(self.api.calls, [])

    async def test_refresh_still_has_user_interval(self):
        await self.bot.accept_update(callback())
        await self.bot.accept_update(callback())
        self.assertEqual(self.bot.queue.qsize(), 1)
        self.assertIn("请求太快", self.api.calls[-1][1]["text"])

    async def test_message_waits_for_queue_space(self):
        bot = TelegramBot(api=self.api, resolver=self.resolver,
                          allowed_user_ids=(123,), queue_size=1)
        await bot.accept_update(message("first"))
        pending = asyncio.create_task(bot.accept_update(message("second")))
        try:
            await asyncio.sleep(0)
            self.assertFalse(pending.done())
            self.assertEqual(bot.queue.get_nowait()[1], "first")
            bot.queue.task_done()
            await asyncio.wait_for(pending, 1)
            self.assertEqual(bot.queue.get_nowait()[1], "second")
            bot.queue.task_done()
            self.assertEqual(self.api.calls, [])
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)

    async def test_busy_queue_and_bad_refresh(self):
        bot = TelegramBot(api=self.api, resolver=self.resolver,
                          allowed_user_ids=(123,), queue_size=1, user_interval=0)
        await bot.accept_update(message("first"))
        await bot.accept_update(callback())
        self.assertEqual(bot.queue.qsize(), 1)
        self.assertEqual(self.api.calls[-1][0], "answerCallbackQuery")
        self.assertIn("任务较多", self.api.calls[-1][1]["text"])
        await bot.accept_update(callback(data="refresh:invalid"))
        self.assertEqual(bot.queue.qsize(), 1)

    async def test_resolution_error_is_explained(self):
        self.resolver.resolve.side_effect = ResolverError("NO_PROGRESSIVE_MP4", "没有 MP4")
        await self.bot.handle_job(123, "link", None, False)
        self.assertEqual(self.api.calls[-1][1]["text"], "解析失败：没有 MP4")
        self.assertFalse(any("reply_markup" in call[1] for call in self.api.calls))

    async def test_failed_refresh_keeps_retry_button(self):
        self.resolver.resolve.side_effect = ResolverError("CDN_UNAVAILABLE", "地址失效")
        await self.bot.handle_job(123, f"https://www.douyin.com/video/{ID}", 52, True)
        self.assertEqual(self.api.calls[-1][1]["reply_markup"]["inline_keyboard"][0][0]
                         ["callback_data"], f"refresh:{ID}")

    async def test_repeated_update_id_is_queued_once(self):
        update = {"update_id": 9, **message("one")}

        class DuplicateAPI(FakeAPI):
            def call(self, method, payload):
                if method == "getUpdates":
                    time.sleep(0.01)
                    self.calls.append((method, payload))
                    return [update]
                return super().call(method, payload)

        api = DuplicateAPI()
        bot = TelegramBot(api=api, resolver=self.resolver,
                          allowed_user_ids=(123,), user_interval=0)
        task = asyncio.create_task(bot.poll())
        try:
            job = await asyncio.wait_for(bot.queue.get(), 1)
            self.assertEqual(job[1], "one")
            await asyncio.sleep(0.03)
            self.assertEqual(len(api.calls), 1)  # No confirmation before completion.
            bot.queue.task_done()
            for _ in range(100):
                if len(api.calls) >= 2:
                    break
                await asyncio.sleep(0.01)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(bot.queue.qsize(), 0)
        self.assertEqual(api.calls[0][1].get("offset"), None)
        self.assertEqual(api.calls[1][1]["offset"], 10)

    async def test_offline_backlog_larger_than_queue_and_poll_batch(self):
        updates = [{"update_id": n, **message(f"link-{n}")} for n in range(1, 46)]
        finished = []
        confirmed = asyncio.Event()

        async def call(method, **payload):
            self.assertEqual(method, "getUpdates")
            offset = payload.get("offset", 0)
            # Every confirmed link must already have a completed result.
            self.assertTrue(all(f"link-{n}" in finished for n in range(1, offset)))
            if offset == 46:
                confirmed.set()
                await asyncio.Future()
            return [update for update in updates if update["update_id"] >= offset][:payload["limit"]]

        async def handle_job(chat_id, text, *args):
            await asyncio.sleep(0)
            finished.append(text)

        self.bot.call = call
        self.bot.handle_job = handle_job
        self.bot.start()
        try:
            await asyncio.wait_for(confirmed.wait(), 2)
            self.assertEqual(len(finished), 45)
            self.assertEqual(len(set(finished)), 45)
        finally:
            await self.bot.stop()

    async def test_restart_receives_unfinished_batch_again(self):
        entered = asyncio.Event()
        offsets = []

        async def call(method, **payload):
            offsets.append(payload.get("offset"))
            return [{"update_id": 9, **message("offline-link")}]

        async def interrupted_job(*args):
            entered.set()
            await asyncio.Future()

        self.bot.call = call
        self.bot.handle_job = interrupted_job
        self.bot.start()
        try:
            await asyncio.wait_for(entered.wait(), 1)
        finally:
            await self.bot.stop()
        self.assertEqual(offsets, [None])

        restarted = TelegramBot(api=self.api, resolver=self.resolver, allowed_user_ids=(123,))
        restarted.call = call
        task = asyncio.create_task(restarted.poll())
        try:
            job = await asyncio.wait_for(restarted.queue.get(), 1)
            self.assertEqual(job[1], "offline-link")
            self.assertEqual(offsets, [None, None])
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_delivery_failure_is_retried_before_confirmation(self):
        retrying = asyncio.Event()
        release = asyncio.Event()
        attempts = 0

        async def handle_job(*args):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise TelegramError("temporary failure", retry_after=0)
            retrying.set()
            await release.wait()

        self.bot.handle_job = handle_job
        await self.bot.accept_update(message("link"))
        worker = asyncio.create_task(self.bot.work())
        drained = asyncio.create_task(self.bot.queue.join())
        try:
            await asyncio.wait_for(retrying.wait(), 1)
            self.assertFalse(drained.done())
            release.set()
            await asyncio.wait_for(drained, 1)
            self.assertEqual(attempts, 2)
        finally:
            worker.cancel()
            drained.cancel()
            await asyncio.gather(worker, drained, return_exceptions=True)

    async def test_help_and_invalid_input_do_not_resolve(self):
        await self.bot.accept_update(message("/start"))
        await self.bot.accept_update(message(""))
        self.assertEqual(self.bot.queue.qsize(), 0)
        self.resolver.resolve.assert_not_called()
        self.assertEqual([name for name, _ in self.api.calls], ["sendMessage", "sendMessage"])


class TelegramAPITests(unittest.TestCase):
    def test_rate_limit_preserves_retry_delay(self):
        response = Mock(status_code=429)
        response.json.return_value = {"ok": False, "parameters": {"retry_after": 12}}
        with patch("douyin_resolver.telegram_bot.requests.post", return_value=response):
            with self.assertRaises(TelegramError) as caught:
                TelegramAPI("123:secret").call("sendMessage", {})
        self.assertEqual(caught.exception.retry_after, 12)

    def test_transport_error_does_not_expose_token(self):
        api = TelegramAPI("123:secret")
        with patch("douyin_resolver.telegram_bot.requests.post",
                   side_effect=requests.ConnectionError("https://api.telegram.org/bot123:secret")):
            with self.assertRaises(TelegramError) as caught:
                api.call("getUpdates", {"timeout": 10})
        self.assertNotIn("secret", str(caught.exception))
