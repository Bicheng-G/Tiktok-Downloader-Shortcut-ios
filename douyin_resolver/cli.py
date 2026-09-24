import argparse
import json
import sys
from pathlib import Path

from .core import ResolverError, download, resolve


def browser_options(parser):
    parser.add_argument("--engine", choices=("auto", "http", "browser"), default="auto")
    parser.add_argument("--channel", choices=("chrome", "msedge", "chromium"), default="chrome")
    parser.add_argument("--profile", help="独立浏览器配置目录；不会读取个人 Chrome 配置")
    parser.add_argument("--headed", action="store_true", help="显示浏览器窗口")
    parser.add_argument("--timeout", type=int, default=35, help="浏览器等待视频详情的秒数")
    parser.add_argument("--quality", choices=("compatible", "best"), default="compatible",
                        help="compatible 优先 H.264；best 优先高分辨率")


def options(args):
    return {k: getattr(args, k) for k in ("engine", "channel", "profile", "headed", "timeout", "quality")}


def login(args):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        kwargs = {"headless": False}
        if args.channel != "chromium":
            kwargs["channel"] = args.channel
        context = p.chromium.launch_persistent_context(str(Path(args.profile).resolve()), **kwargs)
        try:
            page = context.new_page()
            page.goto("https://www.douyin.com/", wait_until="domcontentloaded")
            input("请在浏览器手动登录或验证；完成后回到终端按 Enter 保存登录状态。\n")
        finally:
            context.close()


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="抖音视频链接 → CDN 解析 → 完整 MP4 文件")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("resolve", "download"):
        command = commands.add_parser(name)
        command.add_argument("url", help="视频 URL、短链接，或完整分享文案")
        browser_options(command)
        if name == "resolve":
            command.add_argument("--output", help="另存 JSON 文件")
        else:
            command.add_argument("--dir", default="downloads")
    command = commands.add_parser("serve", help="启动 API；通过 DOUYIN_* 环境变量配置")
    command.add_argument("--host", default="127.0.0.1")
    command.add_argument("--port", type=int, default=8765)
    command = commands.add_parser("login", help="手动登录独立浏览器配置")
    command.add_argument("--profile", default=".douyin-profile")
    command.add_argument("--channel", choices=("chrome", "msedge", "chromium"), default="chrome")
    args = parser.parse_args()
    if hasattr(args, "timeout") and not 1 <= args.timeout <= 300:
        parser.error("--timeout 必须介于 1 和 300 秒之间")
    try:
        if args.command == "login":
            login(args)
            return
        if args.command == "serve":
            from .server import serve
            serve(args.host, args.port)
            return
        video = resolve(args.url, **options(args))
        path = None
        if args.command == "download":
            path = download(video, args.dir)
        result = video.to_dict()
        if path:
            result["file"] = str(path)
        encoded = json.dumps(result, ensure_ascii=False, indent=2)
        if getattr(args, "output", None):
            Path(args.output).write_text(encoded + "\n", encoding="utf-8")
        print(encoded)
    except ResolverError as exc:
        print(json.dumps({"error": exc.code, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        raise SystemExit(130)
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": "LOCAL_ERROR", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
