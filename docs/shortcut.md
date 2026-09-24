# 更新、签名与分发 iPhone 快捷指令

`src/TikTok Downloader.shortcut` 已替换为新版**未签名 binary plist**。`src/TikTok Downloader.plist` 是同一流程的可读 XML；生成源是 `scripts/build_shortcut.py`。原已签名文件完整保留在 `src/legacy/TikTok Downloader-v1.shortcut`。

新版不再调用 `devtool.top`，也不再用固定中文短语和字符串切割解析接口返回值。它读取分享输入或剪贴板，正则提取第一个抖音 URL，POST 给自建 API，通过字典取 `download_url` 和请求头，下载后存入 Photos。后端本身也接受完整分享文案；快捷指令仅发送提取后的链接。

## 在 Mac 上签名一次

不需要在 Mac 上运行下载流程。程序生成的快捷指令文件要通过 Apple 签名，才能以文件方式分享给 iPhone 导入；iPhone 上手动创建自己的快捷指令则无需另行签名。Apple 的 [命令行文档](https://support.apple.com/en-sg/guide/shortcuts-mac/apd455c82f02/mac) 提供 `shortcuts sign --mode anyone`，签名时会将副本交给 Apple 验证。

在 Mac 上复制或 clone 本仓库，进入仓库目录：

```sh
sh scripts/sign_shortcut.sh
```

等价的完整命令：

```sh
mkdir -p dist
shortcuts sign --mode anyone \
  --input "src/TikTok Downloader.shortcut" \
  --output "dist/TikTok Downloader.shortcut"
```

把 `dist/TikTok Downloader.shortcut` AirDrop 到 iPhone，导入时填写：

1. 完整 API 地址，例如 `https://你的后端域名/resolve`。
2. 你自己的 Token，不加 `Bearer ` 前缀。

如果系统版本未显示导入问题，编辑最前面的两个「文本」动作填写相同配置。首次运行允许访问相关网络域名、读取剪贴板及添加照片。

当前已经验证 plist 可解码、动作输出引用、条件分支结构、两个输入示例、HTTP 头部和 Photos 连接关系。**尚未在真实 macOS 执行签名，也未在实体 iPhone 验证导入和保存**；在公开发布前，用本文末尾的两条样例完成手机验证。

## 从源码重新构建

修改 `scripts/build_shortcut.py` 后：

```sh
python3 scripts/build_shortcut.py
python3 -m unittest discover -s tests -v
sh scripts/sign_shortcut.sh
```

UUID 固定，生成结果可重复，Git 可直接审阅 XML 差异。签名产物进入被忽略的 `dist/`。签名不要求创建新的 Shortcut、运行下载任务或重新安装 Python。

## 手动搭建的等价流程

如遇到系统导入兼容问题，可在 iPhone 上建立这些动作：

1. 「获取剪贴板」；若要支持分享菜单，则优先使用「快捷指令输入」。
2. 「匹配文本」提取链接：`https?://[^\s<>"，。；！？、）)]+`，取第一个抖音链接。也可以直接把完整文本交给 API。
3. 「获取 URL 内容」：POST 你的 `/resolve`；请求头 `Authorization: Bearer <你的 Token>`；正文类型 JSON，字段 `url` 设为上一步链接或分享文本。
4. 从返回字典获取 `download_url`。若为空，显示 `message` 并停止。
5. 从返回字典获取 `headers`，再取 `User-Agent`、`Referer`。
6. 「获取 URL 内容」：GET `download_url`，只设置这两个 CDN 请求头。
7. 将下载结果「存储到相簿」，随后显示成功通知。

网络或 HTTP 错误可能由系统直接提示并停止，未必进入自定义提示分支。照片权限拒绝、存储空间不足或下载中断也会停止，不会显示最后的成功通知。

## 分发

- 源码：公开本仓库，保留 MIT 许可证。
- 文件：在 GitHub Release 附上 **dist 中已签名**的 `.shortcut`，链接到部署说明。
- iCloud：导入签名模板后，用 Shortcuts 自带的分享功能生成新的 iCloud 链接，再更新 README。旧 iCloud 链接不会随 Git 更新。
- 公开模板保留占位配置和导入问题。签名前、分享前清除个人 Token；收件人导入后填自己的后端与 Token。

手机验收：分别复制下面两条链接运行，确认相册出现正确作品，能够播放画面和声音；再检查 Token 错误和普通无链接文本能够停止。

- `https://www.douyin.com/jingxuan?modal_id=7688235974236654911`（影视飓风保镖视频）
- `https://v.douyin.com/p-rXmps4nWc/`（晓辉博士《Token经济》）

格式参考：[Shortcuts 文件结构](https://github.com/MoOx/shortcuts-reference)、[观测到的动作序列化格式](https://github.com/findlaywebb/shortcut-lib/blob/main/docs/wire-format-quirks.md)。动作格式也对照了仓库原版 iCloud 工作流，未依赖第三方签名服务。
