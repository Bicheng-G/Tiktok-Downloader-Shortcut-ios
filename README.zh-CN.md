# 抖音视频下载 · iOS 快捷指令

[English](README.md)

复制抖音分享链接或从 iOS 分享菜单运行快捷指令，通过自建 API 解析视频，再由 iPhone 直接从 CDN 下载 MP4 并存入「照片」。服务器不转发视频文件。

本项目支持**中国版抖音**的公开视频，接受视频网页 URL、`v.douyin.com` 短链接和完整分享文案。不支持国际版 TikTok、图文、直播、私密或已下架作品。`douyin.com/jingxuan` 页面需包含指定单条视频的 `modal_id`。

```text
https://www.douyin.com/jingxuan?modal_id=7688235974236654911
https://v.douyin.com/p-rXmps4nWc/
9.92 复制打开抖音，看看【晓辉博士的作品】… https://v.douyin.com/p-rXmps4nWc/ …
```

## 工作流程

1. 快捷指令从分享输入或剪贴板中提取抖音链接。
2. 使用你的 API Token 将链接发送至自建服务的 `POST /resolve`。
3. 解析器先检查短期缓存、尝试 HTTP；必要时复用 Chromium 浏览器。
4. API 返回临时 MP4 地址及 CDN 请求头；iPhone 下载视频并保存到「照片」。

默认 `auto` 模式仅在 HTTP 无法找到并验证目标视频时启动浏览器。`http` 模式无需 Playwright，但支持的视频较少；`browser` 模式始终使用浏览器。解析器会核对视频 ID，不会把推荐的其他视频当作结果，也无需读取个人 Chrome Cookie。实测和限制见[解析研究](docs/resolver.md)。

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

## 本地开发

需要 Python 3.10+，建议使用 Python 3.12。

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
douyin download 'https://www.douyin.com/jingxuan?modal_id=7688235974236654911' --channel chromium
```

CDN 地址会过期。成功读取文件头不保证完整下载、最高画质或无水印。可用性可能随网络以及抖音和 CDN 的变化而改变。请勿在公开分享的快捷指令中填写个人 API Token。

使用 MIT 许可证，详见 [LICENSE](LICENSE)。
