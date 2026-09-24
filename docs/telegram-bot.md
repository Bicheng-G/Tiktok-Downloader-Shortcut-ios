# 私人 Telegram Bot

Bot 是现有解析服务的可选入口。向它私聊发送抖音视频链接或完整分享文案，等待解析后点击「下载 MP4」。地址可能过期；「重新获取」会重新解析同一个视频。Bot 不保存或转发完整视频。

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

- 只处理允许名单中的私人聊天；群组和其他用户的消息会被忽略。每位用户两次有效请求至少间隔 5 秒；等待队列最多 16 条，同时处理最多 2 条。解析器自身仍有并发上限。
- Bot 回复的是解析出的**临时 CDN 地址**。按钮直接打开 CDN，服务器不承担视频下载流量，也不会给 CDN 转发 API Token。
- 现有 Shortcut 下载时会附带 `User-Agent` 和 `Referer`。开发时用一个新解析的样例地址，在不带这些专用请求头的请求下读取到 HTTP 206 和 MP4 文件头。这只验证了开发机器的网络；仍须在目标手机上验证「下载 MP4」按钮。若手机无法下载，需要另行评估受限的视频转发服务；普通 HTTP 跳转不能补上请求头。
- Telegram Bot API 按 URL 发送视频有 [20 MB 文件限制](https://core.telegram.org/bots/api#sending-files)。两个主要用户样例分别约 63 MB 和 99 MB，因此这里返回下载按钮。
- 队列保存在进程内。服务重启时，正在处理或排队的消息可能丢失；请重新发送链接。Telegram 更新在入队后确认，极端重启时也可能收到重复回复。
- 若 Bot 反复无法接收消息，检查 Token、网络连接，以及是否仍设置 webhook。服务日志不会记录 Token、分享文案或签名 CDN URL。

## 验收

用允许的账号分别发送手机短链接、网页版单视频链接和完整分享文案，检查下载按钮是否指向目标视频。在手机 Telegram 中点击按钮，验证可以直接保存完整 MP4；等链接过期后点「重新获取」并再次下载。再用未允许账号和群聊确认 Bot 不响应。

当前没有配置 Bot Token 或允许的用户 ID，尚未完成真实账号与手机端联调。代码的授权、队列、刷新、失败提示和接口交互已用离线测试覆盖。
