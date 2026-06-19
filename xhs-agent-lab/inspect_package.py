from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parent
REQUIRED_FILES = [
    "小红书正文.md",
    "公众号文章.md",
    "发布清单.md",
    "cards_used.json",
]
AI_SMELL_PHRASES = [
    "在当今",
    "随着时代的发展",
    "赋能",
    "降本增效",
    "全方位",
    "多维度",
    "深度解析",
    "干货满满",
    "建议收藏",
    "综上所述",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect the latest xhs-agent-lab publish package.")
    parser.add_argument("--dir", help="发布包目录。默认检查 dist 下最新目录。")
    return parser.parse_args()


def latest_dist_dir() -> Path:
    dist = PROJECT_ROOT / "dist"
    candidates = [path for path in dist.iterdir() if path.is_dir()]
    if not candidates:
        raise FileNotFoundError("No publish packages found under dist/.")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def inspect_package(package_dir: Path) -> int:
    problems: list[str] = []
    notes: list[str] = []

    if not package_dir.exists():
        raise FileNotFoundError(f"Package directory not found: {package_dir}")

    for filename in REQUIRED_FILES:
        if not (package_dir / filename).exists():
            problems.append(f"缺少文件：{filename}")

    card_paths = sorted(package_dir.glob("card_*.png"))
    if len(card_paths) != 7:
        problems.append(f"卡片数量不是 7 张：当前 {len(card_paths)} 张")

    for path in card_paths:
        with Image.open(path) as image:
            size = f"{image.width}x{image.height}"
            if (image.width, image.height) != (1080, 1440):
                problems.append(f"{path.name} 尺寸异常：{size}")
            else:
                notes.append(f"{path.name}: {size}")

    combined = "\n".join(
        read_text(package_dir / filename)
        for filename in ["小红书正文.md", "公众号文章.md", "发布清单.md"]
    )
    hits = [phrase for phrase in AI_SMELL_PHRASES if phrase in combined]
    if hits:
        problems.append("发现偏 AI/营销腔词：" + "、".join(hits))

    cards_json = package_dir / "cards_used.json"
    if cards_json.exists():
        payload = json.loads(cards_json.read_text(encoding="utf-8"))
        cards = payload.get("cards", payload)
        titles = [str(card.get("title", "")).strip() for card in cards if isinstance(card, dict)]
        empty_titles = [str(index + 1) for index, title in enumerate(titles) if not title]
        if empty_titles:
            problems.append("这些卡片缺标题：" + "、".join(empty_titles))
        long_titles = [title for title in titles if len(title) > 26]
        if long_titles:
            problems.append("标题偏长：" + " / ".join(long_titles[:3]))

    quality = extract_quality(read_text(package_dir / "发布清单.md"))
    print(f"发布包：{package_dir}")
    if quality:
        print(f"低 AI 味评分：{quality}")
    print("图片：")
    for note in notes:
        print(f"- {note}")

    if problems:
        print("问题：")
        for problem in problems:
            print(f"- {problem}")
        return 1

    print("检查结果：通过")
    return 0


def extract_quality(text: str) -> str:
    match = re.search(r"低 AI 味评分：([0-9]+/100)", text)
    return match.group(1) if match else ""


def main() -> None:
    args = parse_args()
    package_dir = Path(args.dir).expanduser().resolve() if args.dir else latest_dist_dir()
    raise SystemExit(inspect_package(package_dir))


if __name__ == "__main__":
    main()
