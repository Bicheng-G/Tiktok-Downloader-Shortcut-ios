"""Build reviewable plist + unsigned Shortcut; signing happens on macOS.

Only standard-library Python is required. UUIDs are stable for readable diffs.
"""
import argparse
import plistlib
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

ROOT = Path(__file__).resolve().parents[1]
NAME = "TikTok Downloader"
URL_PATTERN = (r'https?://(?:v\.douyin\.com|(?:www\.)?douyin\.com|www-hj\.douyin\.com|'
               r'(?:www\.)?iesdouyin\.com)/[^\s<>"，。；！？、）)\]}]+')


def ident(label):
    return str(uuid5(NAMESPACE_URL, "douyin-shortcut/v2/" + label)).upper()


def output(label):
    return {"Type": "ActionOutput", "OutputUUID": ident(label), "OutputName": label}


def attachment(value):
    return {"Value": value, "WFSerializationType": "WFTextTokenAttachment"}


def text(*parts):
    value, attachments = "", {}
    for part in parts:
        if isinstance(part, dict):
            offset = len(value.encode("utf-16-le")) // 2
            attachments[f"{{{offset}, 1}}"] = part
            value += "\ufffc"
        else:
            value += str(part)
    return {"Value": {"string": value, "attachmentsByRange": attachments},
            "WFSerializationType": "WFTextTokenString"}


def dictionary(items):
    return {"Value": {"WFDictionaryFieldValueItems": [
        {"WFItemType": 0, "WFKey": text(key), "WFValue": value if isinstance(value, dict) else text(value)}
        for key, value in items.items()
    ]}, "WFSerializationType": "WFDictionaryFieldValue"}


def workflow():
    actions = []

    def add(action, label, **params):
        actions.append({"WFWorkflowActionIdentifier": "is.workflow.actions." + action,
                        "WFWorkflowActionParameters": {"UUID": ident(label), **params}})
        return output(label)

    def condition(label, value, code=100):
        add("conditional", label, GroupingIdentifier=ident(label + "-group"), WFControlFlowMode=0,
            WFCondition=code, WFInput={"Type": "Variable", "Variable": attachment(value)})

    def branch(label, mode):
        add("conditional", label + str(mode), GroupingIdentifier=ident(label + "-group"), WFControlFlowMode=mode)

    def getkey(label, source, key):
        return add("getvalueforkey", label, WFInput=attachment(source), WFDictionaryKey=key,
                   WFGetDictionaryValueType="Value")

    add("comment", "About", WFCommentActionText=(
        "抖音视频 → 自建解析 API → 存储到照片。\n"
        "在下方两个文本动作填写 API /resolve 地址和你自己的 Token。\n"
        "优先读取分享输入，否则读取剪贴板；只有匹配的抖音链接会发往 API。\n"
        "首次运行请允许网络访问和添加照片。发布副本前清除自己的 Token。"))
    endpoint = add("gettext", "API Endpoint", WFTextActionText="https://YOUR_BACKEND.example/resolve")
    token = add("gettext", "API Token", WFTextActionText="YOUR_API_TOKEN")
    extension = {"Type": "ExtensionInput"}
    condition("Share input", extension)
    add("setvariable", "Use share input", WFVariableName="ShareText", WFInput=attachment(extension))
    branch("Share input", 1)
    clipboard = add("getclipboard", "Clipboard")
    add("setvariable", "Use clipboard", WFVariableName="ShareText", WFInput=attachment(clipboard))
    branch("Share input", 2)
    matches = add("text.match", "Douyin links", WFMatchTextPattern=URL_PATTERN,
                  WFMatchTextCaseSensitive=False,
                  text=attachment({"Type": "Variable", "VariableName": "ShareText"}))
    condition("Missing link", matches, 101)
    add("showresult", "Missing link message", Text=text("请先复制抖音视频分享链接，或从分享菜单运行此快捷指令。"))
    add("exit", "Stop without link")
    branch("Missing link", 2)
    link = add("getitemfromlist", "Video link", WFInput=attachment(matches), WFItemSpecifier="First Item")
    response = add("downloadurl", "API response", WFURL=text(endpoint), WFHTTPMethod="POST",
                   WFHTTPBodyType="JSON", WFJSONValues=dictionary({"url": text(link)}),
                   ShowHeaders=True,
                   WFHTTPHeaders=dictionary({"Authorization": text("Bearer ", token),
                                             "Content-Type": "application/json"}))
    download_url = getkey("Download URL", response, "download_url")
    condition("Missing download", download_url, 101)
    message = getkey("API error", response, "message")
    add("showresult", "API error message", Text=text("解析失败：", message, "\n请检查 API 地址、Token 或稍后重试。"))
    add("exit", "Stop after API error")
    branch("Missing download", 2)
    headers = getkey("CDN headers", response, "headers")
    ua = getkey("CDN User-Agent", headers, "User-Agent")
    referer = getkey("CDN Referer", headers, "Referer")
    video = add("downloadurl", "Video file", WFURL=text(download_url), WFHTTPMethod="GET",
                ShowHeaders=True, WFHTTPHeaders=dictionary({"User-Agent": text(ua), "Referer": text(referer)}))
    # The API bearer token is deliberately absent from the request to the CDN.
    add("savetocameraroll", "Saved video", WFInput=attachment(video))
    add("notification", "Success", WFNotificationActionTitle="抖音下载完成",
        WFNotificationActionBody="视频已存储到照片。", WFNotificationActionSound=False)
    return {
        "WFWorkflowName": NAME,
        "WFWorkflowClientVersion": "1092.8.3.1",
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowIcon": {"WFWorkflowIconStartColor": 946986751, "WFWorkflowIconGlyphNumber": 59822},
        "WFWorkflowTypes": ["ActionExtension"],
        "WFWorkflowInputContentItemClasses": ["WFStringContentItem", "WFURLContentItem"],
        "WFWorkflowOutputContentItemClasses": [],
        "WFWorkflowHasShortcutInputVariables": True,
        "WFWorkflowHasOutputFallback": False,
        "WFWorkflowImportQuestions": [
            {"ActionIndex": index, "Category": "Parameter", "ParameterKey": "WFTextActionText",
             "Text": prompt, "DefaultValue": default}
            for index, prompt, default in (
                (1, "填写后端完整 HTTPS 解析地址（以 /resolve 结尾）", "https://YOUR_BACKEND.example/resolve"),
                (2, "填写你自己的 API Token（不含 Bearer 前缀）", "YOUR_API_TOKEN"),
            )
        ],
        "WFWorkflowActions": actions,
    }


def build(destination=ROOT / "src"):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    data = workflow()
    (destination / (NAME + ".plist")).write_bytes(plistlib.dumps(data, fmt=plistlib.FMT_XML, sort_keys=False))
    (destination / (NAME + ".shortcut")).write_bytes(plistlib.dumps(data, fmt=plistlib.FMT_BINARY, sort_keys=False))
    return data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "src")
    args = parser.parse_args()
    result = build(args.output_dir)
    print(f"Generated {len(result['WFWorkflowActions'])} actions in {args.output_dir}; .shortcut is UNSIGNED.")
