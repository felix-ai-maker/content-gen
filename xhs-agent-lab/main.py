from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from card_renderer import CardRenderer
from content_writer import build_wechat_article, build_xhs_post
from copy_pipeline import (
    build_cards_from_copy,
    build_publish_checklist,
    read_copy_input,
    run_quality_checks,
)
from direct_card_renderer import DirectCardRenderer
from style_director import apply_style_plan

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - fallback for fresh local Python installs.
    yaml = None


PROJECT_ROOT = Path(__file__).resolve().parent


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        text = f.read()
    if yaml is not None:
        return yaml.safe_load(text) or {}
    return load_simple_yaml(text)


def load_simple_yaml(text: str) -> dict:
    lines = text.splitlines()
    root: dict = {}
    stack: list[tuple[int, dict | list]] = [(-1, root)]

    for index, raw_line in enumerate(lines):
        line = strip_yaml_comment(raw_line).rstrip()
        if not line.strip():
            continue

        indent = len(line) - len(line.lstrip(" "))
        content = line.strip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]

        if content.startswith("- "):
            if not isinstance(parent, list):
                raise ValueError("Fallback YAML parser expected a list parent.")
            parent.append(parse_yaml_scalar(content[2:].strip()))
            continue

        key, sep, value = content.partition(":")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if value:
            if not isinstance(parent, dict):
                raise ValueError("Fallback YAML parser expected a dict parent.")
            parent[key] = parse_yaml_scalar(value)
            continue

        container: dict | list = [] if next_yaml_child_is_list(lines, index, indent) else {}
        if not isinstance(parent, dict):
            raise ValueError("Fallback YAML parser expected a dict parent.")
        parent[key] = container
        stack.append((indent, container))
    return root


def strip_yaml_comment(line: str) -> str:
    quote: str | None = None
    for index, char in enumerate(line):
        if char in {"'", '"'}:
            quote = None if quote == char else char
        if char == "#" and quote is None:
            return line[:index]
    return line


def next_yaml_child_is_list(lines: list[str], current_index: int, parent_indent: int) -> bool:
    for candidate in lines[current_index + 1 :]:
        candidate = strip_yaml_comment(candidate).rstrip()
        if not candidate.strip():
            continue
        indent = len(candidate) - len(candidate.lstrip(" "))
        if indent <= parent_indent:
            return False
        return candidate.strip().startswith("- ")
    return False


def parse_yaml_scalar(value: str) -> object:
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        return int(value)
    except ValueError:
        return value


def load_cards(project_root: Path, cards_file: str | None, topic: str, copy_text: str, config: dict) -> tuple[list[dict], str]:
    explicit_cards = Path(cards_file).expanduser().resolve() if cards_file else None
    default_cards = project_root / "cards.json"

    if explicit_cards:
        if not explicit_cards.exists():
            raise FileNotFoundError(f"Cards file not found: {explicit_cards}")
        return read_cards_json(explicit_cards), explicit_cards.name
    if default_cards.exists():
        return read_cards_json(default_cards), default_cards.name
    if copy_text.strip():
        return build_cards_from_copy(topic=topic, copy_text=copy_text, config=config), "auto-from-copy"

    sample_cards = project_root / "sample_cards.json"
    return read_cards_json(sample_cards), sample_cards.name


def read_cards_json(cards_path: Path) -> list[dict]:
    with cards_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    cards = payload.get("cards", payload) if isinstance(payload, dict) else payload
    if not isinstance(cards, list) or not cards:
        raise ValueError(f"{cards_path.name} must contain a non-empty cards list.")
    return cards


def safe_topic_name(topic: str, max_len: int = 48) -> str:
    normalized = re.sub(r"[\\/:*?\"<>|#]+", "", topic).strip()
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = normalized.strip(" .。？?！!")
    return normalized[:max_len] or "未命名选题"


def make_output_dir(project_root: Path, topic: str, timezone: str) -> Path:
    today = datetime.now(ZoneInfo(timezone)).strftime("%Y-%m-%d")
    output_dir = project_root / "dist" / f"{today}_{safe_topic_name(topic)}"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def write_markdown(path: Path, text: str) -> None:
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a Xiaohongshu + WeChat content publishing package."
    )
    parser.add_argument("--topic", required=True, help="当天选题，例如：我为什么要搭一个自己的交易 Agent？")
    parser.add_argument("--copy", help="一组原始文案/素材。适合短素材直接从命令行传入。")
    parser.add_argument("--copy-file", help="原始文案文件路径。适合粘贴长素材后自动拆卡。")
    parser.add_argument("--cards", help="指定 cards.json 路径。未指定时优先读取项目根目录 cards.json。")
    parser.add_argument(
        "--local-bg",
        action="store_true",
        help="只使用本地极简背景，不调用 Vertex/Gemini/OpenAI 图像模型。适合草稿迭代。",
    )
    parser.add_argument(
        "--direct-ai-card",
        action="store_true",
        help="让 Nano Banana/Vertex 直接生成整张成品卡，包含中文排版和主题插画。",
    )
    parser.add_argument(
        "--style",
        help="指定视觉风格模板（见 config.yaml 的 style_presets，如 magazine_editorial / bold_xhs / warm_editorial）。仅 --direct-ai-card 生效；不指定时按选题自动选。",
    )
    parser.add_argument(
        "--config",
        default=str(PROJECT_ROOT / "config.yaml"),
        help="品牌、颜色、字体与输出尺寸配置文件。",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    if args.local_bg and args.direct_ai_card:
        raise ValueError("--local-bg 和 --direct-ai-card 不能同时使用。")
    if args.local_bg:
        config.setdefault("image_model", {})["enabled"] = False
    copy_text, copy_source = read_copy_input(copy_text=args.copy, copy_file=args.copy_file)
    cards, cards_source = load_cards(
        project_root=PROJECT_ROOT,
        cards_file=args.cards,
        topic=args.topic,
        copy_text=copy_text,
        config=config,
    )
    try:
        cards, style_plan = apply_style_plan(
            cards=cards,
            topic=args.topic,
            copy_text=copy_text,
            config=config,
            style_override=args.style,
        )
    except ValueError as exc:
        raise SystemExit(str(exc))

    timezone = config.get("timezone", "Asia/Shanghai")
    output_dir = make_output_dir(PROJECT_ROOT, args.topic, timezone)

    xhs_post = build_xhs_post(
        topic=args.topic,
        cards=cards,
        config=config,
        cards_source=cards_source,
        copy_text=copy_text,
    )
    wechat_article = build_wechat_article(
        topic=args.topic,
        cards=cards,
        config=config,
        cards_source=cards_source,
        copy_text=copy_text,
    )
    quality = run_quality_checks(
        topic=args.topic,
        cards=cards,
        xhs_post=xhs_post,
        wechat_article=wechat_article,
        config=config,
    )

    write_markdown(
        output_dir / "小红书正文.md",
        xhs_post,
    )
    write_markdown(
        output_dir / "公众号文章.md",
        wechat_article,
    )
    write_json(output_dir / "cards_used.json", {"cards": cards})
    write_json(output_dir / "style_plan.json", style_plan)
    if copy_text.strip():
        write_markdown(output_dir / "source_copy.md", copy_text)

    if args.direct_ai_card:
        renderer = DirectCardRenderer(config=config, project_root=PROJECT_ROOT)
        rendered_paths = renderer.render_all(
            cards=cards,
            output_dir=output_dir,
            topic=args.topic,
            style_plan=style_plan,
        )
    else:
        renderer = CardRenderer(config=config, project_root=PROJECT_ROOT)
        rendered_paths = renderer.render_all(cards=cards, output_dir=output_dir, topic=args.topic)
    write_markdown(
        output_dir / "发布清单.md",
        build_publish_checklist(
            topic=args.topic,
            cards_source=cards_source,
            copy_source=copy_source,
            rendered_count=len(rendered_paths),
            quality=quality,
        ),
    )

    print(f"发布包已生成：{output_dir}")
    print(f"卡片来源：{cards_source}")
    print(f"视觉主题：{style_plan.get('name')} / {style_plan.get('theme')}")
    print(f"出图模式：{'Nano Banana 直接成品图' if args.direct_ai_card else '本地排版 + AI 视觉层'}")
    if args.direct_ai_card:
        preset = style_plan.get("style_preset") or {}
        print(f"视觉风格：{preset.get('name', '')}（{preset.get('key', '')}）")
    print(f"低 AI 味评分：{quality.score}/100")
    if quality.warnings:
        print("发布前建议：")
        for warning in quality.warnings:
            print(f"- {warning}")
    for path in rendered_paths:
        print(f"- {path.name}")


if __name__ == "__main__":
    main()
