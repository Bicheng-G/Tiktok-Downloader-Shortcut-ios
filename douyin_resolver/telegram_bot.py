"""Optional private Telegram entry point for the existing resolver."""

import asyncio
import logging
import re
import time

import requests

from .core import ResolverError

logger = logging.getLogger(__name__)
REFRESH = re.compile(r"refresh:(\d{10,25})\Z")


class TelegramError(Exception):
    def __init__(self, message, *, retry_after=3):
        super().__init__(message)
        self.retry_after = retry_after


class TelegramAPI:
    def __init__(self, token):
        self.endpoint = f"https://api.telegram.org/bot{token}/"

    def call(self, method, payload):
        try:
            # A long poll waits up to 10 seconds on Telegram's side.
            response = requests.post(self.endpoint + method, json=payload, timeout=(5, 18))
            data = response.json()
            if response.status_code != 200 or not isinstance(data, dict) or not data.get("ok"):
                if response.status_code == 429 and isinstance(data, dict):
                    delay = (data.get("parameters") or {}).get("retry_after", 3)
                    if not isinstance(delay, (int, float)) or delay < 0:
                        delay = 3
                    raise TelegramError("Telegram rate limit (429)", retry_after=delay)
                if response.status_code == 401:
                    raise TelegramError("Telegram rejected the bot token (401)")
                if response.status_code == 409:
                    raise TelegramError("Telegram polling conflict (409): check webhook or another instance")
                raise TelegramError(f"Telegram {method} returned HTTP {response.status_code}")
            return data["result"]
        except (requests.RequestException, ValueError, KeyError):
            # Requests exceptions can contain the URL, including the bot token.
            raise TelegramError(f"Telegram {method} failed") from None


class TelegramBot:
    def __init__(self, *, api, resolver, allowed_user_ids, queue_size=16,
                 workers=2, user_interval=5):
        self.api = api
        self.resolver = resolver
        self.allowed_user_ids = frozenset(allowed_user_ids)
        self.queue = asyncio.Queue(maxsize=queue_size)
        self.workers = workers
        self.user_interval = user_interval
        self.last_user_at = {}
        self.offset = None
        self.tasks = []

    async def call(self, method, **payload):
        return await asyncio.to_thread(self.api.call, method, payload)

    def start(self):
        self.tasks = [asyncio.create_task(self.poll(), name="telegram-poll")]
        self.tasks.extend(asyncio.create_task(self.work(), name=f"telegram-worker-{n}")
                          for n in range(self.workers))

    async def stop(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()

    async def poll(self):
        while True:
            try:
                # A higher offset confirms the previous batch on Telegram. Keep
                # it unconfirmed until every queued job has delivered a result.
                await self.queue.join()
                options = {"limit": 20, "timeout": 10,
                           "allowed_updates": ["message", "callback_query"]}
                if self.offset is not None:
                    options["offset"] = self.offset
                updates = await self.call("getUpdates", **options)
                if not isinstance(updates, list):
                    raise TelegramError("Telegram getUpdates failed")
                for update in updates:
                    update_id = update.get("update_id") if isinstance(update, dict) else None
                    if not isinstance(update_id, int) or isinstance(update_id, bool):
                        continue
                    if self.offset is not None and update_id < self.offset:
                        continue
                    await self.accept_update(update)
                    self.offset = update_id + 1
            except asyncio.CancelledError:
                raise
            except TelegramError as exc:
                logger.warning("%s; retrying", exc)
                await asyncio.sleep(exc.retry_after)
            except Exception:
                # No share text, bot token, or signed CDN URL in logs.
                logger.warning("Telegram polling failed; retrying")
                await asyncio.sleep(3)

    async def accept_update(self, update):
        callback = update.get("callback_query")
        if not isinstance(callback, dict):
            callback = None
        message = callback.get("message") if isinstance(callback, dict) else update.get("message")
        if not isinstance(message, dict):
            return
        chat = message.get("chat") or {}
        sender = (callback or message).get("from") or {}
        if not isinstance(chat, dict) or not isinstance(sender, dict):
            return
        user_id, chat_id = sender.get("id"), chat.get("id")
        if (chat.get("type") != "private" or not isinstance(user_id, int)
                or isinstance(user_id, bool) or user_id not in self.allowed_user_ids
                or chat_id != user_id):
            return

        if callback:
            data = callback.get("data")
            match = REFRESH.fullmatch(data) if isinstance(data, str) else None
            if not match or not isinstance(message.get("message_id"), int):
                await self.call("answerCallbackQuery", callback_query_id=callback["id"],
                                text="此按钮已失效，请重新发送视频链接。")
                return
            job = (chat_id, f"https://www.douyin.com/video/{match.group(1)}",
                   message["message_id"], True)
        else:
            content = message.get("text", "")
            if not isinstance(content, str):
                content = ""
            if content.startswith(("/start", "/help")):
                await self.call("sendMessage", chat_id=chat_id,
                                text="发送抖音视频链接或完整分享文案，我会返回下载链接。")
                return
            if not content or len(content) > 8192:
                await self.call("sendMessage", chat_id=chat_id,
                                text="请发送抖音视频链接或完整分享文案（最多 8192 字）。")
                return
            job = (chat_id, content, None, False, message.get("message_id"))

        now = time.monotonic()
        if callback and now - self.last_user_at.get(user_id, float("-inf")) < self.user_interval:
            await self.call("answerCallbackQuery", callback_query_id=callback["id"],
                            text="请求太快，请稍后再试。", show_alert=True)
            return
        if callback and self.queue.full():
            await self.call("answerCallbackQuery", callback_query_id=callback["id"],
                            text="当前任务较多，请稍后再试。", show_alert=True)
            return
        if callback:
            await self.call("answerCallbackQuery", callback_query_id=callback["id"], text="正在更新链接…")
            self.queue.put_nowait(job)
            self.last_user_at[user_id] = now
        else:
            # Offline updates arrive together. Apply backpressure instead of
            # rejecting them because of their arrival rate or a full queue.
            await self.queue.put(job)

    async def work(self):
        while True:
            job = await self.queue.get()
            try:
                while True:
                    try:
                        await self.handle_job(*job)
                        break
                    except asyncio.CancelledError:
                        raise
                    except TelegramError as exc:
                        logger.warning("%s; retrying Telegram job", exc)
                        await asyncio.sleep(exc.retry_after)
                    except Exception:
                        logger.warning("Telegram job failed; retrying")
                        await asyncio.sleep(3)
            finally:
                self.queue.task_done()

    async def handle_job(self, chat_id, text, message_id, refresh, reply_to_message_id=None):
        if message_id is None:
            options = {}
            if reply_to_message_id is not None:
                options["reply_parameters"] = {
                    "message_id": reply_to_message_id, "allow_sending_without_reply": True,
                }
            sent = await self.call("sendMessage", chat_id=chat_id, text="正在解析…", **options)
            message_id = sent["message_id"]
        else:
            await self.call("editMessageText", chat_id=chat_id, message_id=message_id,
                            text="正在更新链接…")
        try:
            video = await asyncio.to_thread(self.resolver.resolve, text, refresh=refresh)
        except ResolverError as exc:
            options = {}
            if refresh:
                options["reply_markup"] = {"inline_keyboard": [[{
                    "text": "重新获取", "callback_data": f"refresh:{text.rsplit('/', 1)[-1]}"
                }]]}
            await self.call("editMessageText", chat_id=chat_id, message_id=message_id,
                            text=f"解析失败：{str(exc)[:300]}", **options)
            return
        except Exception:
            logger.error("Unexpected Telegram resolver failure")
            options = {}
            if refresh:
                options["reply_markup"] = {"inline_keyboard": [[{
                    "text": "重新获取", "callback_data": f"refresh:{text.rsplit('/', 1)[-1]}"
                }]]}
            await self.call("editMessageText", chat_id=chat_id, message_id=message_id,
                            text="解析异常，请稍后重试。", **options)
            return
        title = (video.title or "无标题").strip()[:150]
        author = (video.author or "未知").strip()[:80]
        body = (f"{title}\n作者：{author}\n\n"
                "点击“打开 MP4”后，在 Telegram 浏览器右上角 ⋯ 菜单选择“Save to Files”保存视频。"
                "地址可能过期；失效时点击“重新获取”。")
        markup = {"inline_keyboard": [
            [{"text": "打开 MP4", "url": video.candidates[0].url}],
            [{"text": "重新获取", "callback_data": f"refresh:{video.id}"}],
        ]}
        await self.call("editMessageText", chat_id=chat_id, message_id=message_id,
                        text=body, reply_markup=markup,
                        link_preview_options={"is_disabled": True})
