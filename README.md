# Douyin Video Downloader for iOS Shortcuts

[简体中文](README.zh-CN.md)

Save a public Douyin video to your iPhone's Photos library: copy a share link or use the iOS share sheet, run the Shortcut, and let your self-hosted API resolve the video. The iPhone downloads the MP4 directly from the CDN; the server does not relay the video file.

This project is for **Douyin (the Chinese app)**. It accepts a video page URL, a `v.douyin.com` short link, or the full text of a Douyin share message. International TikTok, photo posts, livestreams, and private or removed videos are not supported. A `douyin.com/jingxuan` page needs a `modal_id` identifying a specific video.

```text
https://www.douyin.com/jingxuan?modal_id=7688235974236654911
https://v.douyin.com/p-rXmps4nWc/
9.92 复制打开抖音，看看【晓辉博士的作品】… https://v.douyin.com/p-rXmps4nWc/ …
```

## How it works

1. The Shortcut extracts a Douyin link from its share-sheet input or the clipboard.
2. It sends the link and your API token to your own `POST /resolve` endpoint.
3. The resolver checks a short-lived cache, tries HTTP first, and uses a reusable Chromium browser if needed.
4. The API returns a temporary MP4 URL and the CDN request headers. The iPhone downloads the video and saves it to Photos.

The default `auto` engine starts the browser only when the HTTP route cannot find and verify the requested video. `http` runs without Playwright but supports fewer videos; `browser` always uses the browser. The resolver checks the requested video ID rather than accepting an unrelated recommendation. It does not need your personal Chrome cookies. See the [resolver notes](docs/resolver.md) for test results and limitations (Chinese).

## 1. Deploy the API

You need a server that can keep a Docker container running, plus an HTTPS address reachable from your iPhone. This repository does not provide a public API.

```sh
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(32))"
# Put the generated value in .env as DOUYIN_API_TOKEN.
docker compose up -d --build
curl http://127.0.0.1:8000/health
```

Compose binds port 8000 to the server's loopback interface. Put an HTTPS reverse proxy in front of it, then use `https://your-domain.example/resolve` in the Shortcut. `127.0.0.1` on the iPhone refers to the iPhone, not your server. The token must be at least 24 ASCII characters with no whitespace; the placeholder in `.env.example` is rejected.

The default Docker image includes Chromium. For a smaller HTTP-only image, build the `http` target and set `DOUYIN_ENGINE=http`. Run one Uvicorn worker so the cache and browser are shared. Environment variables, Python setup, the API response, and an HTTPS proxy example are in the [deployment guide](docs/deployment.md) (Chinese).

## 2. Sign and install the Shortcut

The repository contains an **unsigned** [Shortcut](src/TikTok%20Downloader.shortcut), its readable [plist](src/TikTok%20Downloader.plist), and the [build script](scripts/build_shortcut.py). The old signed version is preserved in `src/legacy/`; any old iCloud share link still points to that version.

On a Mac, run this from the repository root:

```sh
sh scripts/sign_shortcut.sh
```

The script uses Apple's `shortcuts sign` command to create `dist/TikTok Downloader.shortcut`. AirDrop that file to your iPhone and import it. Enter your full HTTPS `/resolve` URL and your token when prompted. If import questions do not appear, edit the first two Text actions in the Shortcut. Allow access to the API, the video CDN, the clipboard, and Photos when iOS asks. The Mac is only needed to sign the Shortcut; daily downloads run on the iPhone.

The [Shortcut guide](docs/shortcut.md) (Chinese) covers rebuilding, manual setup, and distribution. The generated workflow and API have offline tests, but signing on macOS and saving a video on a physical iPhone have not been verified in this repository.

## Optional: private Telegram bot

You can also send a link or full share message to a private Telegram bot backed by the same resolver. It replies with an **Open MP4** button for the temporary CDN URL and a refresh button. On a phone, open the MP4 and use the Telegram browser's `⋯` menu → **Save to Files**. The bot does not store or relay the video.

Set both `TELEGRAM_BOT_TOKEN` and `TELEGRAM_ALLOWED_USER_IDS` in `.env`, then restart the service. The bot uses long polling, needs outbound access to `api.telegram.org`, and responds only to allowed users in private chats. See the [Telegram bot guide](docs/telegram-bot.md) (Chinese). Saving through Telegram on a phone still needs device testing.

## Develop locally

Python 3.10+ is required; Python 3.12 is recommended.

```sh
python -m venv .venv
# macOS/Linux: source .venv/bin/activate
# PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e '.[browser,test]'
python -m playwright install chromium
python -m unittest discover -s tests -v
python scripts/build_shortcut.py
```

The CLI can resolve or download without the iOS Shortcut:

```sh
douyin resolve 'https://v.douyin.com/p-rXmps4nWc/' --engine http
douyin download 'https://www.douyin.com/jingxuan?modal_id=7688235974236654911' --channel chromium
```

CDN URLs expire. A successful header check does not guarantee a complete download, maximum quality, or a watermark-free file. Availability can vary by network and by changes to Douyin or its CDN. Do not put your personal API token in a publicly shared Shortcut.

MIT licensed. See [LICENSE](LICENSE).
