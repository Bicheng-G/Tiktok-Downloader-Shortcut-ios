# YouTube 解析

API、CLI、Telegram Bot 和生成的 iOS 快捷指令均支持 YouTube 与 YouTube Music。Music 链接按同一视频处理，输出带声音的 MP4，不输出 MP3 或纯音频。

支持 `youtube.com`、`www.youtube.com`、`m.youtube.com`、`music.youtube.com` 的 `/watch?v=…`，以及 `youtu.be/…`、`/shorts/…`、`/embed/…`、`/live/…` 视频链接。视频 ID 必须为 11 位；直播或尚未处理完成的回放仍会被拒绝。不解析播放列表、频道主页或私人视频。`watch` 链接中的播放列表、时间和分享参数会移除，只解析指定视频。YouTube 链接可以省略协议。

```sh
python -m pip install -U -e '.[browser,test]'
# 另外安装 Deno 2.3+ 或 Node.js 22+，并加入服务器进程的 PATH。
douyin resolve 'music.youtube.com/watch?v=3QZOazpGIQc' --engine http
douyin resolve 'youtube.com/watch?v=3QZOazpGIQc' --engine http
douyin download 'https://youtu.be/3QZOazpGIQc' --engine http
```

项目需 Python 3.11+，依赖 `yt-dlp[default]`，包含匹配的 EJS 挑战脚本。优先使用 PATH 中的 Deno，否则启用 Node。Docker 的 `http` 和 `auto` 镜像均包含 Deno。运行要求见 [yt-dlp 官方 EJS 文档](https://github.com/yt-dlp/yt-dlp/wiki/EJS)。现有 Python 部署须重新安装依赖并重启进程；Docker 部署须重新构建。快捷指令须重新构建、在 Mac 上签名并重新导入。

三种 engine 均使用 yt-dlp 提取 YouTube 元数据，无需启动 Playwright。每次提取在独立子进程中执行，超时后终止；不加载用户的 yt-dlp 配置、插件、浏览器 Cookie，也不下载远程挑战脚本或视频文件。YouTube 与 Music 的相同视频共用短期缓存；刷新会重新提取。Telegram 的刷新按钮保留 YouTube 平台。

只接受 `googlevideo.com` 上含视频和音频轨道的 HTTPS 完整 MP4，过滤 DRM、分离轨道、HLS 和分片格式。`compatible` 优先 H.264，`best` 在完整 MP4 中优先分辨率；两者都不会合并分离轨道。无完整 MP4 时返回 `NO_PROGRESSIVE_MP4`，而不是提供无声视频。返回地址保留签名，并用 `expire` 参数限制缓存有效期。默认会探测 MP4 文件头。

YouTube 某些新提取的格式需等到 `available_at` 才可下载；提前探测会返回 403。解析器按最终保留的完整 MP4 格式计算可用时间，在提取超时预算内等待后再验证和返回地址。已就绪的地址不等待，也不因被过滤的音频或分离轨道而延迟。该行为与 [yt-dlp 下载器](https://github.com/yt-dlp/yt-dlp/blob/2026.10.26/yt_dlp/downloader/common.py) 一致；使用 `--skip-download` 只提取元数据时，调用方必须自行处理这个时间。

YouTube 直链可能绑定解析服务器的出口 IP；同 IP 和请求头要求见 [yt-dlp FAQ](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#i-extracted-a-video-url-with--g-but-it-does-not-play-on-another-machine--in-my-web-browser)。在服务器上探测或完整下载成功，仍不能保证另一网络的 iPhone 或 Telegram 浏览器可下载。此版本沿用手机直连 CDN 的架构，不代理视频；跨网络下载失败时，需在同一出口网络使用，或通过服务器上的 CLI 下载。没有经过实体 iPhone 验收，也不承诺最高画质。

`YOUTUBE_RUNTIME_REQUIRED` 表示缺少 JavaScript 运行时；`YOUTUBE_DEPENDENCY_REQUIRED` 表示缺少 yt-dlp；`NETWORK_TIMEOUT` 表示提取超时；`YOUTUBE_UNAVAILABLE` 表示上游无法提取，可能受登录、地区、限流或平台变化影响。检查视频可用性和服务器网络，并按项目依赖范围更新 yt-dlp。上游原始报错不会直接返回，以免暴露本地路径或签名地址。

2026-11-02 本地验证：两个示例链接完整下载 3,950,980 字节，ffprobe 确认为 360×360 H.264 视频和 AAC 音频，时长约 217 秒。初版存在间歇性 403：两轮独立提取都显示目标格式带有未来的 `available_at`；同一地址立即请求为 403，6 秒后在新 Session 中请求为 206，未更换地址、请求头或 Cookie。已修复忽略可用时间的问题并重启后端，114 项测试通过；两个入口各做 3 次强制刷新，6 次 API 请求均为 200、未命中缓存，每个返回地址在独立 Session 中均可读取 MP4。脱敏记录位于 `artifacts/youtube-availability-validation.json`。此记录不代表跨网络或实体 iPhone 验收。

本地 Docker daemon 未运行，镜像构建尚未验证；Python/Node 路径已完成上述真实请求和下载验证。
