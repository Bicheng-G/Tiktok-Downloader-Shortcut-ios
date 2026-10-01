# 私人 Telegram Bot

Bot 是现有解析服务的可选入口。向它私聊发送抖音视频链接或完整分享文案，等待解析后点击「打开 MP4」。在 Telegram 内置浏览器右上角点「⋯」→「Save to Files」保存视频；视频播放器里的分享按钮是分享链接。地址可能过期；「重新获取」会重新解析同一个视频。Bot 不保存或转发完整视频。

## 启用

1. 在 Telegram 找 [@BotFather](https://t.me/BotFather)，用 `/newbot` 创建 Bot，取得 Token。
2. 向新 Bot 发送 `/start`。**在启动本服务前**，用 Telegram Bot API 的 `getUpdates` 查看这条消息的 `message.from.id`，这是你的数字用户 ID。Token 不要放进公开链接、截图或仓库。
3. 在服务的 `.env` 中设置 `TELEGRAM_BOT_TOKEN` 和 `TELEGRAM_ALLOWED_USER_IDS`。多个用户 ID 用英文逗号分隔。留空两项即可关闭 Bot。
4. 启动或重启服务：`docker compose up -d --build`。直接运行 Python 时也须通过环境变量设置两项；Python 启动器不会自动读取 `.env`。

```dotenv
TELEGRAM_BOT_TOKEN=<BotFather 提供的 Token>
TELEGRAM_ALLOWED_USER_IDS=123456789
```

Bot 使用长轮询，因此不需要为 Telegram 配置 webhook；服务器必须能主动访问 `api.telegram.org`。服务仍只运行 **一个 Uvicorn worker / 一个容器副本**，让 Bot 与 API 共用缓存和浏览器。若这个 Bot 以前设置过 webhook，先调用 Telegram Bot API 的 `deleteWebhook`；webhook 和 `getUpdates` 不能同时使用。

## 行为和边界

- 只处理允许名单中的私人聊天；群组和其他用户的消息会被忽略。普通链接连续发送时自动排队；等待队列最多 16 条，同时处理最多 2 条，队列满时等待空位继续入队。每位用户点击「重新获取」至少间隔 5 秒，队列满时按钮会提示稍后重试。解析器自身仍有并发上限。
- Bot 回复的是解析出的**临时 CDN 地址**。按钮直接打开 CDN，服务器不承担视频下载流量，也不会给 CDN 转发 API Token。
- 现有 Shortcut 下载时会附带 `User-Agent` 和 `Referer`。开发时用一个新解析的样例地址，在不带这些专用请求头的请求下读取到 HTTP 206 和 MP4 文件头。这只验证了开发机器的网络；仍须在目标手机上验证「打开 MP4」按钮和「Save to Files」保存流程。若手机无法下载，需要另行评估受限的视频转发服务；普通 HTTP 跳转不能补上请求头。
- Telegram Bot API 按 URL 发送视频有 [20 MB 文件限制](https://core.telegram.org/bots/api#sending-files)。两个主要用户样例分别约 63 MB 和 99 MB，因此这里返回 MP4 直链按钮，由手机浏览器保存文件。
- 服务恢复后会自动领取 Telegram 尚未确认的离线消息，逐批排队解析，每条结果回复对应的原消息。只有当前批次全部处理完（成功回复下载链接或解析失败提示），才通过下一次轮询确认收取；Telegram 请求失败会等待后重试，限流时遵守 `retry_after`。
- Telegram 对未领取更新的保留时间[最多为 24 小时](https://core.telegram.org/bots/api#getting-updates)，Bot API 不能任意读取聊天历史。过期或已被旧程序确认的消息需要重新转发；若要电脑关机超过 24 小时仍不漏消息，需要常在线的接收端和持久化队列。
- 本地队列仍保存在进程内。处理中重启会重新领取 Telegram 仍保留的未确认批次，其中已经回复的部分可能重复回复；网络响应丢失时重试也可能重复回复。不保证跨重启恰好处理一次。永久无法回复的任务会持续重试并阻塞后续批次，需检查服务日志与 Bot 权限。
- 若 Bot 反复无法接收消息，检查 Token、网络连接，以及是否仍设置 webhook。服务日志不会记录 Token、分享文案或签名 CDN URL。

## 验收

用允许的账号分别发送手机短链接、网页版单视频链接和完整分享文案，检查「打开 MP4」按钮是否指向目标视频。在手机 Telegram 中点击按钮，点浏览器右上角「⋯」→「Save to Files」，验证保存的是完整 MP4；等链接过期后点「重新获取」并再次保存。再用未允许账号和群聊确认 Bot 不响应。

仓库不包含 Bot Token 或允许的用户 ID，部署时须在本机 `.env` 配置。代码的授权、队列、刷新、失败提示和接口交互已用离线测试覆盖。

离线补处理验收：停止服务，在私人聊天中连续发送多条链接（可超过 16 条），在第一条消息发出后 24 小时内启动服务，确认所有链接都有对应结果，且不出现「请求太快」或「当前任务较多」拒绝普通消息。处理中重启时确认剩余任务会补处理，允许未确认批次出现重复回复。
