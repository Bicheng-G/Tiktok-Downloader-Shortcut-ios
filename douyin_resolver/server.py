"""Deployable ASGI API. Video bytes go directly from the CDN to iPhone."""
import asyncio
import hmac
import json
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .core import ResolverError
from .service import Resolver

logger = logging.getLogger(__name__)


@dataclass
class Settings:
    token: str = ""
    engine: str = "auto"
    channel: str = "chromium"
    timeout: int = 35
    cache_ttl: int = 45

    @classmethod
    def from_env(cls):
        return cls(token=os.getenv("DOUYIN_API_TOKEN", ""),
                   engine=os.getenv("DOUYIN_ENGINE", "auto"),
                   channel=os.getenv("DOUYIN_BROWSER_CHANNEL", "chromium"),
                   timeout=int(os.getenv("DOUYIN_BROWSER_TIMEOUT", "35")),
                   cache_ttl=int(os.getenv("DOUYIN_CACHE_TTL", "45")))


def reply(status_code, **body):
    return JSONResponse(body, status_code=status_code, headers={"Cache-Control": "no-store"})


def create_app(settings=None, resolver=None):
    settings = settings or Settings.from_env()
    if (len(settings.token) < 24 or not settings.token.isascii() or any(c.isspace() for c in settings.token)
            or settings.token.startswith(("REPLACE_", "YOUR_"))):
        raise ValueError("Set DOUYIN_API_TOKEN to at least 24 ASCII characters without whitespace")
    if settings.engine not in ("auto", "http", "browser"):
        raise ValueError("DOUYIN_ENGINE must be auto, http or browser")
    if not 1 <= settings.timeout <= 90 or not 0 <= settings.cache_ttl <= 300:
        raise ValueError("Browser timeout must be 1..90; cache TTL must be 0..300")

    @asynccontextmanager
    async def lifespan(app):
        app.state.resolver = resolver or Resolver(engine=settings.engine, channel=settings.channel,
                                                 timeout=settings.timeout, cache_ttl=settings.cache_ttl)
        try:
            yield
        finally:
            await asyncio.to_thread(app.state.resolver.close)

    app = FastAPI(title="Douyin Shortcut Resolver", version="0.2.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    async def health():
        # Liveness only: no traffic to Douyin and no browser startup.
        return reply(200, status="ok", engine=settings.engine)

    @app.post("/resolve")
    async def resolve(request: Request):
        supplied = request.headers.get("authorization", "").encode("utf8")
        expected = ("Bearer " + settings.token).encode("ascii")
        if not hmac.compare_digest(supplied, expected):
            return reply(401, ok=False, error="UNAUTHORIZED", message="请检查快捷指令中的 API Token。")
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            return reply(415, ok=False, error="JSON_REQUIRED", message="请发送 JSON 请求正文。")
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 16384:
                return reply(413, ok=False, error="BODY_TOO_LARGE", message="分享文本过长。")
            body.extend(chunk)
        try:
            data = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return reply(400, ok=False, error="INVALID_JSON", message="请求正文不是有效 JSON。")
        if not isinstance(data, dict) or not isinstance(data.get("url"), str) or not 1 <= len(data["url"]) <= 8192:
            return reply(400, ok=False, error="INVALID_URL", message="url 应为视频链接或完整分享文案。")
        if not isinstance(data.get("refresh", False), bool):
            return reply(400, ok=False, error="INVALID_REFRESH", message="refresh 应为布尔值。")
        try:
            result = await asyncio.to_thread(request.app.state.resolver.resolve, data["url"],
                                             refresh=data.get("refresh", False))
            return reply(200, ok=True, **result.to_dict())
        except ResolverError as exc:
            status = 400 if exc.code in ("INVALID_URL", "VIDEO_URL_REQUIRED", "UNSUPPORTED_MEDIA") else 502
            if exc.code == "RESOLVER_BUSY":
                status = 429
            return reply(status, ok=False, error=exc.code, message=str(exc))
        except Exception:
            # Avoid printing share text, credentials or signed CDN URLs in logs.
            logger.error("Unexpected resolver failure")
            return reply(500, ok=False, error="INTERNAL_ERROR", message="解析异常，请稍后重试。")

    return app


def serve(host="127.0.0.1", port=8765):
    import uvicorn
    uvicorn.run(create_app(), host=host, port=port, workers=1, access_log=False)
