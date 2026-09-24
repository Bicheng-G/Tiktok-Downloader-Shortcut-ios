# 部署解析后端

## 部署格式与模式

ASGI 工厂：`douyin_resolver.server:create_app`。使用 **一个 Uvicorn worker**；每个 worker 都有自己的缓存和浏览器，多开 worker 会多开浏览器。

| 模式 | 行为 | 用途 |
| --- | --- | --- |
| `auto`（默认） | HTTP 优先，失败后复用浏览器 | 当前建议的 Shortcut 后端，覆盖两个实测样例 |
| `http` | 仅 Requests，不导入 Playwright | 小型后端；部分公开视频会返回 `HTTP_NO_MATCH` |
| `browser` | 直接复用浏览器 | 排查 HTTP 路径或对比可用清晰度 |

浏览器由专用线程管理，避免 Playwright 跨线程操作。同一浏览器同时处理一条新视频；忙时返回 429，HTTP 路径仍可工作。整个解析器最多接受 4 个进行中的请求，同 ID 请求合并。缓存最多 256 条，默认 45 秒，并提前 60 秒避开平台声明的过期时间。进程结束即清空；没有数据库、对象存储或服务器视频目录。

## Docker

默认镜像包含 Chromium，启动服务或健康检查不会启动浏览器。`Dockerfile` 的 Playwright 包版本和官方镜像版本保持一致，参见 [Playwright Docker 文档](https://playwright.dev/python/docs/docker)。Compose 为容器配置 init、256 MB 共享内存和 1 GB 内存上限；这是起始配置，不是实测最低内存需求。

```sh
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(32))"
# 把结果填写到 .env 的 DOUYIN_API_TOKEN，不要保留占位值
docker compose up -d --build
```

已有容器托管平台可直接构建仓库根目录的 Dockerfile，设置 `DOUYIN_API_TOKEN`，把健康检查设为 `/health`。支持平台提供的 `PORT`，默认 8000。选择能保持进程运行、允许 Chromium、提供足够内存的平台。自动休眠会丢失缓存与浏览器会话，下一次浏览器请求会重新冷启动。这里不承诺某个免费套餐的容量或持续可用性。

仅 HTTP 的轻量部署：

```sh
docker build --target http -t douyin-resolver:http .
docker run --rm --env-file .env -e DOUYIN_ENGINE=http \
  -p 127.0.0.1:8000:8000 douyin-resolver:http
```

若使用 Compose 切换到 HTTP，**同时**把 `build.target` 改为 `http`，并将 `.env` 中 `DOUYIN_ENGINE` 改为 `http`。

## 直接运行 Python

```sh
python -m venv .venv
. .venv/bin/activate
pip install '.[browser]'
python -m playwright install --with-deps chromium
export DOUYIN_API_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
# 将此 Token 私下填入手机配置，并持久设置到部署平台环境变量。
uvicorn douyin_resolver.server:create_app --factory --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

PowerShell 对应启动方式：

```powershell
.venv\Scripts\Activate.ps1
$env:DOUYIN_API_TOKEN = python -c "import secrets; print(secrets.token_urlsafe(32))"
# 本机已有 Chrome 时可以设置此项，省去下载 Chromium
$env:DOUYIN_BROWSER_CHANNEL = 'chrome'
python -m douyin_resolver serve --host 127.0.0.1 --port 8000
```

直接运行 Python 不会自动加载 `.env`；请通过进程环境变量或服务管理器提供配置。纯 HTTP 只需 `pip install .` 并设置 `DOUYIN_ENGINE=http`。

| 环境变量 | 默认 | 说明 |
| --- | --- | --- |
| `DOUYIN_API_TOKEN` | 必填 | 至少 24 位 ASCII，无空白；推荐随机生成，示例占位值会被拒绝 |
| `DOUYIN_ENGINE` | `auto` | `auto` / `http` / `browser` |
| `DOUYIN_BROWSER_CHANNEL` | `chromium` | 可用 `chrome` 或 `msedge`，需已安装对应浏览器 |
| `DOUYIN_BROWSER_TIMEOUT` | `35` | 浏览器等待详情秒数，1–90；不包含短链、HTTP、CDN 请求耗时 |
| `DOUYIN_CACHE_TTL` | `45` | 0–300 秒，0 禁用缓存 |
| `PORT` | `8000` | Docker 启动端口；直接运行用 Uvicorn `--port` |

主机上的 HTTPS 代理示例（Caddy）：

```caddyfile
resolver.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

把域名指向部署主机并替换示例名称。如果代理也在容器内，应使用它可访问的服务地址。代理响应超时建议至少 120 秒；手机必须能访问 HTTPS 域名。本轮未创建云资源或部署公网服务。

## API 合约

`GET /health` 无需鉴权，返回进程存活状态；**不代表抖音接口可用**。

`POST /resolve`：

```http
Authorization: Bearer <你的 Token>
Content-Type: application/json
```

```json
{"url":"完整分享文案或视频 URL","refresh":false}
```

成功示意，省略较长的候选数组：

```json
{
  "ok": true,
  "id": "7680575887263617273",
  "download_url": "https://<CDN>/video/...?<原始签名>",
  "filename": "7680575887263617273.mp4",
  "headers": {"User-Agent": "...", "Referer": "https://www.douyin.com/"},
  "verified": true,
  "strategy": "http",
  "cache_hit": false,
  "expires_at": null
}
```

`download_url` 是 MP4 地址，不是永久链接。手机立即 GET 此地址，设置 `headers` 中的两个请求头，再保存结果。**不要向 CDN 转发 API Authorization**。API 不返回 Cookie，也不代理视频流量；生产环境中服务端和手机可能使用不同 IP，需要实际验证 CDN 是否允许跨网络下载。

`verified=true` 仅表示解析时能够读取 MP4 文件头；完整文件测试另见研究记录。HTTP 路径和浏览器路径可拿到不同清晰度，默认优先 H.264 兼容性，再比较分辨率。并不保证最高画质或无水印。

失败为 `{"ok":false,"error":"错误码","message":"说明"}`：400 输入不支持；401 Token 错误；413 正文超过 16 KiB；415 非 JSON；429 繁忙；502 上游未命中、网络、CDN、浏览器或验证问题；500 未预期异常。平台要求人工验证时返回明确错误，不自动处理验证码。

客户端遇到过期地址，可 POST 相同输入并设置 `refresh:true`。当前 Shortcut 使用默认缓存，不实现自动重试；等待 45 秒后再次运行也会重新解析。

输入只允许抖音页面域名，短链和媒体重定向逐跳检查域名。服务不提供任意 URL 代理，不在默认日志里记录分享文本、Token 或签名 URL。公网多用户场景应在代理层按账户或令牌加配额；当前单 Token 配置定位为个人或小范围使用。
