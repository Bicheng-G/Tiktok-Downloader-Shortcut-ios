# Windows 后台运行

先按 README 完成本机 Python 环境、浏览器依赖和 `.env` 配置。这套启动器直接使用项目 `.venv`，无需 Docker 或管理员权限。

## 使用

双击根目录的 `Start Backend.cmd`，启动成功后窗口自动关闭，服务在后台监听 `127.0.0.1:8000`。关闭终端不会停止后台进程。重复点击不会创建另一套服务。

双击 `Backend.cmd` 打开菜单：启动、停止、重启、状态、开启自启、关闭自启、日志。退出菜单会保留已启动的服务。停止会等待进行中的任务退出，并关闭服务自己的浏览器；不会结束个人浏览器。

也可在仓库根目录运行：

```powershell
.\Backend.cmd start
.\Backend.cmd stop
.\Backend.cmd restart
.\Backend.cmd status
.\Backend.cmd enable-autostart
.\Backend.cmd disable-autostart
.\Backend.cmd logs
```

修改 `.env` 后重启。启动器按字面值读取 `KEY=value`，支持空行、独立注释行和成对引号，不执行变量或命令替换。日志位于 `artifacts/backend.log`，每份最大约 2 MiB，保留当前文件及 3 份备份。`/health` 只证明 API 存活，不保证平台解析成功。

原 `scripts/start_windows_backend.ps1` 仍可用于前台启动，也纳入同一套重复实例检查和停止控制。

## 自启的范围

自启默认关闭。开启时只在当前用户的 Windows Startup 文件夹创建本项目专属快捷方式，通过 `.venv\Scripts\pythonw.exe` 启动，不显示终端，不保存密码。关闭自启会移除此快捷方式，已运行的服务需另行停止。

这是**开机后登录 Windows 时启动**。未登录时不会启动，注销会结束用户进程；锁屏可继续运行。Windows Startup 文件夹用于登录后启动，参见 [Microsoft 说明](https://support.microsoft.com/en-us/windows/experience/startup-boot/configure-startup-applications-in-windows)。若需要未登录就提供全天服务，应配置带启动触发器及适当用户身份的计划任务或 Windows 服务；本启动器不安装这类系统服务，也不提供崩溃后的自动重启。移动仓库或重建 `.venv` 后应检查并重新配置自启快捷方式。

电脑睡眠、休眠、关机或断网时，手机不能使用服务。启动器不改变电源设置。Telegram Bot 随 API 一起运行；iOS 快捷指令远程访问仍需要原有 HTTPS 地址和代理。这里不会启动或配置额外隧道／代理。

## 资源估算

2026-11-03 在本机用 Python 3.12 离线采样，关闭 Telegram，不请求视频平台。计入服务及其子进程，使用约 5 秒待机样本；工作集求和可能重复计算共享内存。

| 状态 | 工作集合计 | 私有提交内存合计 |
| --- | ---: | ---: |
| 仅 API，浏览器尚未启动 | 61.3 MiB | 42.2 MiB |
| Chromium 已启动，打开后关闭空白页 | 240.5 MiB | 178.1 MiB |

仅 API 样本 CPU 增量为 0；浏览器预热后的短样本约占单核 2.7%，不能当作长期平均。真实视频页面、多个 HTTP 请求及 yt-dlp／JavaScript 子进程可能使 CPU、内存短时增加，这些峰值尚未测量。个人低频使用可先预留 0.5–1 GB，作为预算，而非内存上限或容量保证。

`auto` 模式先尝试 HTTP，需要时才启动浏览器；浏览器启动后会保留，直到服务停止。当前没有空闲自动回收。设置 `DOUYIN_ENGINE=http` 并重启可减少浏览器资源，但会失去浏览器兜底，部分链接会失败；YouTube 仍使用 yt-dlp。

API 和 Bot 只解析链接，视频由手机直接下载，不占用电脑的视频存储和完整视频传输流量。解析仍会请求页面、接口及文件头；启用 Telegram 后还有持续长轮询和少量网络开销。适合个人电脑日常常驻，但为服务专门让整台电脑全天不睡眠的耗电，通常比 API 本身更值得考虑。

本机原始采样保存在 `artifacts/resource-sample.json`，采样脚本为 `artifacts/measure_backend_resources.py`（均为本地忽略文件）。
