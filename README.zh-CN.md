# 抖音视频下载 · iOS 快捷指令

[English](README.md)

复制抖音、Instagram 或 YouTube 视频链接，或从 iOS 分享菜单运行快捷指令，通过自建 API 解析视频，再由 iPhone 直接从 CDN 下载 MP4 并存入「照片」。服务器不转发视频文件。

本项目支持**中国版抖音**的公开视频，以及 **Instagram 公开 Reel 和单视频帖子**，接受视频网页 URL、`v.douyin.com` 短链接和完整分享文案。Instagram 支持 `/reel/`、`/reels/`、`/p/`、`/tv/` 链接，解析前移除 `stkn` 等分享参数。不支持国际版 TikTok、图片或轮播帖子、Instagram Stories、直播、私密或已下架作品。`douyin.com/jingxuan` 页面需包含指定单条视频的 `modal_id`。

```text
https://www.douyin.com/jingxuan?modal_id=7688235974236654911
https://v.douyin.com/p-rXmps4nWc/
https://www.instagram.com/reel/DcaIKqURjoi/?stkn=MXVmZDF2aWdvZng0dQ==
9.92 复制打开抖音，看看【晓辉博士的作品】… https://v.douyin.com/p-rXmps4nWc/ …
```

也接受 YouTube 和 YouTube Music 链接，包括省略 `https://` 的链接：

```text
music.youtube.com/watch?v=3QZOazpGIQc
youtube.com/watch?v=3QZOazpGIQc
https://youtu.be/3QZOazpGIQc
```

YouTube 后端需安装 Deno 2.3+ 或 Node.js 22+；两个 Docker target 均已包含 Deno。只返回含声音的完整 MP4，清晰度可能有限；直链可能绑定服务器 IP，因此其他网络上的 iPhone 不一定能下载。YouTube Music 解析为对应视频，不输出纯音频。配置和限制见 [YouTube 解析说明](docs/youtube.md)。已安装的快捷指令需重新构建、签名并导入，才能识别这些链接。

## 工作流程

1. 快捷指令从分享输入或剪贴板中提取抖音、Instagram 或 YouTube 链接。
2. 使用你的 API Token 将链接发送至自建服务的 `POST /resolve`。
3. 解析器先检查短期缓存、尝试 HTTP；必要时复用 Chromium 浏览器。
4. API 返回临时 MP4 地址及 CDN 请求头；iPhone 下载视频并保存到「照片」。

抖音和 Instagram 的默认 `auto` 模式仅在 HTTP 无法找到并验证目标视频时启动浏览器。`http` 模式无需 Playwright，但支持的视频较少；`browser` 模式使用浏览器。YouTube 在三种模式下均使用 yt-dlp。解析器会核对视频 ID，不会把推荐的其他视频当作结果，也无需读取个人 Chrome Cookie。实测和限制见[解析研究](docs/resolver.md)。

## 1. 部署 API

你需要可持续运行 Docker 容器的服务器，以及 iPhone 能访问的 HTTPS 地址。本仓库不提供公共 API。

```sh
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(32))"
# 将生成值填入 .env 的 DOUYIN_API_TOKEN。
docker compose up -d --build
curl http://127.0.0.1:8000/health
```

Compose 只将 8000 端口绑定在服务器本机。通过 HTTPS 反向代理接入，并在快捷指令中填写 `https://你的域名/resolve`。iPhone 上的 `127.0.0.1` 指向手机自身，并非服务器。Token 至少为 24 位无空白的 ASCII 字符；`.env.example` 中的占位值会被拒绝。

默认 Docker 镜像包含 Chromium。如需轻量的纯 HTTP 镜像，可构建 `http` target 并设置 `DOUYIN_ENGINE=http`。服务使用一个 Uvicorn worker，以便共用缓存和浏览器。环境变量、Python 部署、API 响应及 HTTPS 代理示例见[部署说明](docs/deployment.md)。

## 2. 签名并安装快捷指令

仓库提供**未签名**的[快捷指令](src/TikTok%20Downloader.shortcut)、可读的 [plist](src/TikTok%20Downloader.plist) 和[构建脚本](scripts/build_shortcut.py)。旧版签名文件保存在 `src/legacy/`；原有 iCloud 分享链接仍指向旧版。

在 Mac 上从仓库根目录运行：

```sh
sh scripts/sign_shortcut.sh
```

脚本使用 Apple 的 `shortcuts sign` 生成 `dist/TikTok Downloader.shortcut`。通过 AirDrop 发送到 iPhone 并导入，按提示输入完整的 HTTPS `/resolve` 地址和 Token。如果导入问题没有出现，编辑快捷指令开头的两个「文本」动作。首次使用时允许访问 API、视频 CDN、剪贴板和「照片」。Mac 仅用于签名；日常下载在 iPhone 上运行。

重新构建、手动搭建与分发方法见[快捷指令说明](docs/shortcut.md)。生成的工作流与 API 已通过离线测试，但本仓库尚未验证在 macOS 上签名和在实体 iPhone 上保存视频。

## 可选：私人 Telegram Bot

你也可以向共用解析器的私人 Telegram Bot 发送链接或完整分享文案。它会回复临时 CDN 地址的「打开 MP4」按钮及重新获取按钮。在手机上打开 MP4 后，从 Telegram 内置浏览器的 `⋯` 菜单选择 **Save to Files**。Bot 不保存或转发视频文件。

在 `.env` 中同时设置 `TELEGRAM_BOT_TOKEN` 和 `TELEGRAM_ALLOWED_USER_IDS`，然后重启服务。Bot 使用长轮询，服务器需能主动访问 `api.telegram.org`；它只响应允许名单中的私人聊天。配置见 [Telegram Bot 说明](docs/telegram-bot.md)。手机端通过 Telegram 保存视频仍需实机验证。

## Windows 一键启动与后台运行

本机已配置 `.venv` 和 `.env` 后，双击根目录的 **`Start Backend.cmd`** 即可后台启动，关闭启动窗口不影响服务。双击 **`Backend.cmd`** 打开管理菜单，可停止、重启、查看状态和日志，或开启／关闭登录 Windows 后自启。自启默认关闭。

实测仅 API 待机约 61 MB，启动 Chromium 并关闭空白页后约 241 MB（进程树工作集总和，测试未启用 Telegram）。真实解析峰值未测，个人使用可先预留 0.5–1 GB。电脑睡眠或关机时服务不可用。详见 [Windows 后台运行](docs/windows.md)。

## 本地开发

需要 Python 3.11+，建议使用 Python 3.12。YouTube 另需在 PATH 中提供 Deno 2.3+ 或 Node.js 22+。

```sh
python -m venv .venv
# macOS/Linux: source .venv/bin/activate
# PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e '.[browser,test]'
python -m playwright install chromium
python -m unittest discover -s tests -v
python scripts/build_shortcut.py
```

不使用 iOS 快捷指令也可通过 CLI 解析或下载：

```sh
douyin resolve 'https://v.douyin.com/p-rXmps4nWc/' --engine http
douyin resolve 'https://www.instagram.com/reel/DcaIKqURjoi/' --engine auto --channel chromium
douyin download 'https://www.douyin.com/jingxuan?modal_id=7688235974236654911' --channel chromium
```

Instagram 可能要求登录或限制匿名访问。解析器先尝试公开页面和 GraphQL 数据，`auto` 模式会使用独立浏览器兜底。CLI 可用 `douyin login --platform instagram --profile .instagram-profile` 保存登录状态，再用 `--engine browser --profile .instagram-profile` 解析；API 服务默认使用自己的匿名浏览器。详见 [Instagram 解析说明](docs/instagram.md)。

CDN 地址会过期。成功读取文件头不保证完整下载、最高画质或无水印。可用性可能随网络以及平台和 CDN 的变化而改变。请勿在公开分享的快捷指令中填写个人 API Token。

使用 MIT 许可证，详见 [LICENSE](LICENSE)。
