"""把发布包通过 Telegram bot 推送到手机（纯发送，不消费 updates，和别的接收程序互不干扰）。

凭据走环境变量，绝不落盘：
    export TELEGRAM_BOT_TOKEN="bot token"
    export TELEGRAM_CHAT_ID="你的 chat_id"

用法：
    python push_telegram.py            # 推最新发布包
    python push_telegram.py --dir <发布包目录>

推送内容：7 张卡片（相册）+ 小红书标题/正文+标签（一条可复制消息）+ 公众号文章（文件）。
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import urllib.request
from pathlib import Path

from prepare_publish import _read, _split_title, latest_package

TG_API = "https://api.telegram.org/bot{token}/{method}"


def _multipart(fields: dict, files: list[tuple[str, Path]]) -> tuple[str, bytes]:
    boundary = "----xhsAgentLabBoundary7MA4YWxkTrZu0gW"
    body = b""
    for key, value in fields.items():
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode()
    for name, path in files:
        fname = path.name
        ctype = mimetypes.guess_type(fname)[0] or "application/octet-stream"
        body += (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; "
            f"filename=\"{fname}\"\r\nContent-Type: {ctype}\r\n\r\n"
        ).encode()
        body += path.read_bytes() + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return boundary, body


def _api(token: str, method: str, fields: dict, files: list[tuple[str, Path]] | None = None, timeout: float = 90):
    url = TG_API.format(token=token, method=method)
    if files:
        boundary, body = _multipart(fields, files)
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    else:
        data = urllib.parse.urlencode(fields).encode()
        request = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"Telegram {method} 失败：{result}")
    return result


def _send_message(token: str, chat_id: str, text: str) -> None:
    # Telegram 单条上限 4096 字符，超长分段。
    chunks = [text[i : i + 3900] for i in range(0, len(text), 3900)] or [text]
    for chunk in chunks:
        _api(token, "sendMessage", {"chat_id": chat_id, "text": chunk, "disable_web_page_preview": "true"})


def _send_album(token: str, chat_id: str, images: list[Path], caption: str) -> None:
    media = []
    files: list[tuple[str, Path]] = []
    for i, img in enumerate(images):
        name = f"photo{i}"
        item = {"type": "photo", "media": f"attach://{name}"}
        if i == 0 and caption:
            item["caption"] = caption
        media.append(item)
        files.append((name, img))
    _api(token, "sendMediaGroup", {"chat_id": chat_id, "media": json.dumps(media)}, files)


def _send_document(token: str, chat_id: str, path: Path, caption: str = "") -> None:
    fields = {"chat_id": chat_id}
    if caption:
        fields["caption"] = caption
    _api(token, "sendDocument", fields, [("document", path)])


def push(pkg: Path, token: str, chat_id: str) -> None:
    images = sorted(pkg.glob("card_*.png"))
    xhs_title, xhs_body = _split_title(_read(pkg / "小红书正文.md"))

    if images:
        _send_album(token, chat_id, images, caption=f"📦 {pkg.name}\n按顺序保存这 {len(images)} 张图")
    xhs_text = f"📕 小红书｜标题\n{xhs_title}\n\n— — 正文 + 标签（复制下面）— —\n{xhs_body}"
    _send_message(token, chat_id, xhs_text)

    wechat = pkg / "公众号文章.md"
    if wechat.exists():
        _send_document(token, chat_id, wechat, caption="📰 公众号文章（建议电脑后台粘贴排版）")


def main() -> None:
    parser = argparse.ArgumentParser(description="把发布包推送到 Telegram。")
    parser.add_argument("--dir", help="发布包目录；默认取 dist/ 里最新的。")
    args = parser.parse_args()

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise SystemExit("缺少 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID 环境变量。")

    pkg = Path(args.dir).expanduser().resolve() if args.dir else latest_package()
    if pkg is None or not pkg.exists():
        raise SystemExit("没有找到发布包。先用 main.py 生成，或用 --dir 指定。")

    push(pkg, token, chat_id)
    print(f"已推送到 Telegram：{pkg.name}")


if __name__ == "__main__":
    main()
