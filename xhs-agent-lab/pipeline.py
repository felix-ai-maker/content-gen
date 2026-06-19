"""发布包生成的核心流程，CLI(main.py) 和 Web(webapp) 共用。

把原本在 main.py 里的流程提取成 generate_package(...)，返回结构化结果（不直接 print），
方便 Web 后端调用、收集日志、回传给前端。
"""
from __future__ import annotations

import json
import os
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


# --------------------------------------------------------------------------- #
# 配置 / 卡片 / 输出 helper（自 main.py 迁移，行为不变）
# --------------------------------------------------------------------------- #
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


def maybe_push_telegram(config: dict, output_dir: Path, no_push: bool = False, log=print) -> bool:
    tg = config.get("telegram") or {}
    if no_push or not tg.get("enabled"):
        return False
    token = os.getenv(tg.get("bot_token_env", "TELEGRAM_BOT_TOKEN"))
    chat_id = os.getenv(tg.get("chat_id_env", "TELEGRAM_CHAT_ID"))
    if not token or not chat_id:
        log("（未设置 Telegram 凭据，跳过推送；export TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 后自动推送）")
        return False
    try:
        from push_telegram import push

        push(output_dir, token, chat_id)
        log("已推送到 Telegram。")
        return True
    except Exception as exc:  # noqa: BLE001 - 推送失败不影响发布包
        log(f"Telegram 推送失败（不影响发布包）：{exc}")
        return False


def regenerate_card(
    package_name: str,
    index: int,
    extra_brief: str = "",
    config: dict | None = None,
    config_path: Path | None = None,
    project_root: Path = PROJECT_ROOT,
    log=print,
) -> dict:
    """对已生成的发布包，单独重生成第 index 张卡（覆盖原图）。

    复用包内 cards_used.json / style_plan.json / inputs.json，只重抽一张，省配额。
    """
    if config is None:
        config = load_config(config_path or (project_root / "config.yaml"))
    pkg = (project_root / "dist" / package_name).resolve()
    if not pkg.is_dir():
        raise ValueError(f"发布包不存在：{package_name}")

    cards = read_cards_json(pkg / "cards_used.json")
    style_plan = json.loads((pkg / "style_plan.json").read_text(encoding="utf-8"))
    inputs_path = pkg / "inputs.json"
    inputs = json.loads(inputs_path.read_text(encoding="utf-8")) if inputs_path.exists() else {}
    topic = inputs.get("topic") or style_plan.get("name") or package_name
    if not 1 <= index <= len(cards):
        raise ValueError(f"卡片序号超出范围：{index}（共 {len(cards)} 张）")

    if extra_brief and extra_brief.strip():
        config["extra_brief"] = extra_brief.strip()

    renderer = DirectCardRenderer(config=config, project_root=project_root)
    log(f"重新生成第 {index} 张卡…")
    path = renderer.render_one(cards, index, pkg, topic, style_plan)
    log("完成。")
    return {"package_name": package_name, "index": index, "card": path.name, "extra_brief": extra_brief}


def _append_prompt_log(project_root: Path, entry: dict) -> None:
    """每次生成把输入提示词追加到 prompts_log.jsonl，供后续优化复盘。"""
    log_path = project_root / "prompts_log.jsonl"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------- #
# 核心：生成发布包
# --------------------------------------------------------------------------- #
def generate_package(
    topic: str,
    *,
    copy_text: str = "",
    copy_file: str | None = None,
    cards_file: str | None = None,
    style: str | None = None,
    mode: str = "direct",  # "direct" | "local" | "background"
    extra_brief: str = "",
    push: bool = True,
    config: dict | None = None,
    config_path: Path | None = None,
    project_root: Path = PROJECT_ROOT,
    log=print,
) -> dict:
    """跑完整生成流程，返回结构化结果。

    mode: direct=Nano Banana 整卡直出；local=本地排版草稿；background=本地排版 + AI 背景。
    extra_brief: 额外创意指令，注入图像 prompt（界面上的「按提示词优化」）。
    """
    if config is None:
        config = load_config(config_path or (project_root / "config.yaml"))

    direct = mode == "direct"
    if mode == "local":
        config.setdefault("image_model", {})["enabled"] = False
    if extra_brief and extra_brief.strip():
        config["extra_brief"] = extra_brief.strip()

    resolved_copy, copy_source = read_copy_input(copy_text=copy_text or None, copy_file=copy_file)
    cards, cards_source = load_cards(project_root, cards_file, topic, resolved_copy, config)
    log("正在规划风格与创意…")
    cards, style_plan = apply_style_plan(
        cards=cards, topic=topic, copy_text=resolved_copy, config=config, style_override=style
    )

    timezone = config.get("timezone", "Asia/Shanghai")
    output_dir = make_output_dir(project_root, topic, timezone)

    xhs_post = build_xhs_post(topic=topic, cards=cards, config=config, cards_source=cards_source, copy_text=resolved_copy)
    wechat_article = build_wechat_article(
        topic=topic, cards=cards, config=config, cards_source=cards_source, copy_text=resolved_copy
    )
    quality = run_quality_checks(
        topic=topic, cards=cards, xhs_post=xhs_post, wechat_article=wechat_article, config=config
    )

    write_markdown(output_dir / "小红书正文.md", xhs_post)
    write_markdown(output_dir / "公众号文章.md", wechat_article)
    write_json(output_dir / "cards_used.json", {"cards": cards})
    write_json(output_dir / "style_plan.json", style_plan)
    if resolved_copy.strip():
        write_markdown(output_dir / "source_copy.md", resolved_copy)

    log("正在生成卡片图…")
    if direct:
        renderer = DirectCardRenderer(config=config, project_root=project_root)
        rendered_paths = renderer.render_all(cards=cards, output_dir=output_dir, topic=topic, style_plan=style_plan)
    else:
        renderer = CardRenderer(config=config, project_root=project_root)
        rendered_paths = renderer.render_all(cards=cards, output_dir=output_dir, topic=topic)

    write_markdown(
        output_dir / "发布清单.md",
        build_publish_checklist(
            topic=topic,
            cards_source=cards_source,
            copy_source=copy_source,
            rendered_count=len(rendered_paths),
            quality=quality,
        ),
    )

    pushed = maybe_push_telegram(config, output_dir, no_push=not push, log=log)

    inputs = {
        "timestamp": datetime.now(ZoneInfo(timezone)).isoformat(timespec="seconds"),
        "topic": topic,
        "copy_text": copy_text,
        "style": style or "",
        "mode": mode,
        "extra_brief": extra_brief or "",
    }
    write_json(output_dir / "inputs.json", inputs)
    _append_prompt_log(
        project_root,
        {**inputs, "package_name": output_dir.name, "score": quality.score},
    )

    preset = style_plan.get("style_preset") or {}
    return {
        "output_dir": str(output_dir),
        "package_name": output_dir.name,
        "cards_source": cards_source,
        "mode": mode,
        "style_name": style_plan.get("name"),
        "style_theme": style_plan.get("theme"),
        "style_preset": preset.get("key"),
        "style_preset_name": preset.get("name"),
        "xhs_md": xhs_post,
        "wechat_md": wechat_article,
        "cards": [path.name for path in rendered_paths],
        "quality": {"score": quality.score, "warnings": list(quality.warnings)},
        "pushed": pushed,
        "inputs": inputs,
    }
