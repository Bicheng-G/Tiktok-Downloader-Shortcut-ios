# Instagram 视频解析

现有 `/resolve` API、CLI、私人 Telegram Bot 和重新生成的 iOS Shortcut 均支持 Instagram 公开 Reel 与单视频帖子。不需要安装新的解析依赖。

支持 `instagram.com` 和 `www.instagram.com` 下的 `/reel/<shortcode>/`、`/reels/<shortcode>/`、`/p/<shortcode>/`、`/tv/<shortcode>/`。输入可以是完整分享文案；`stkn`、`igsh` 等追踪参数在规范化时移除。图片、轮播、Stories、直播和私密作品不在支持范围内。

```sh
douyin resolve 'https://www.instagram.com/reel/DcaIKqURjoi/?stkn=MXVmZDF2aWdvZng0dQ==' --engine auto --channel chromium
douyin download 'https://www.instagram.com/reel/DcaIKqURjoi/' --engine browser --channel chromium
```

`http` 模式读取公开页面内嵌 JSON、Instagram 的公开 GraphQL 响应及 embed 页面。`auto` 在 HTTP 无法取得或验证目标视频时复用独立浏览器；`browser` 直接使用浏览器。网页和已完成的网络响应都必须含匹配的 shortcode 或媒体 ID；不会使用推荐视频、任意视频元素的 blob 地址或 DASH 分离轨道代替结果。

解析器只接受 Instagram CDN 的完整 MP4 URL，并保留签名原文。API 返回原有 `Video` 字段与 Instagram Referer，下载与文件头验证复用现有逻辑。缓存和正在进行的请求按平台及 ID 区分，避免与抖音 ID 冲突。若上游未提供视频时长，`duration_seconds` 为 `0`，表示未知。

Instagram 可能要求登录、验证码或限制匿名访问。CLI 可以手动登录自己的独立配置目录：

```sh
douyin login --platform instagram --profile .instagram-profile --channel chrome
douyin resolve 'https://www.instagram.com/reel/DcaIKqURjoi/' --engine browser --profile .instagram-profile --channel chrome
```

`.instagram-profile/` 已加入 Git 忽略列表。API 服务默认仍使用独立匿名浏览器，不读取个人 Chrome 配置。公开页面、Relay 文档 ID 和 CDN 都可能随上游变化；HTTP 不保证命中，部署建议使用 `auto`。

Telegram 接收 Instagram 链接并返回 MP4 链接；Instagram 刷新按钮使用 `refresh:ig:<shortcode>`，原有抖音按钮仍可使用。Shortcut 的匹配规则已更新；已安装的旧版需重新构建、在 Mac 上签名并导入。

## 2026-10-02 验证

用户提供的 `DcaIKqURjoi` 在本机独立 Chrome、匿名上下文通过 `/resolve` 的 `auto` 路径解析成功：HTTP 未命中后使用浏览器，首次约 27.42 秒，重复请求命中缓存。CDN Range 检查成功，完整下载 **19,054,519 字节**；ffprobe 检查为 **720×1280 H.264 + AAC，约 118.12 秒**。这是单条链接、单机网络的实测，不是所有 Instagram 链接都可用的保证。

离线测试覆盖链接与分享参数、目标 ID 匹配、嵌套及旧版页面数据、HTTP/浏览器兜底、CDN 域名与重定向、MP4 验证、跨平台缓存、Telegram 刷新及 Shortcut 构建。未验证实体 iPhone 导入与保存。
