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
from copy_pipeline import (
    AI_SMELL_PHRASES,
    build_cards_from_copy,
    build_publish_checklist,
    read_copy_input,
    run_quality_checks,
)
from copy_writer import compose_body, generate_cards
from direct_card_renderer import DirectCardRenderer
from style_director import apply_style_plan

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - fallback for fresh local Python installs.
    yaml = None


PROJECT_ROOT = Path(__file__).resolve().parent

# 持久化密钥：启动时从 .env 读入环境变量，省得每次开会话重新 export。
# 不覆盖已存在的 shell 变量（override 默认 False），所以临时 export 仍优先。
try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except ModuleNotFoundError:  # pragma: no cover - dotenv 未安装时静默跳过，不影响主流程
    pass


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

    brand = config.get("brand", {}).get("name", "交易 Agent 实验室")
    llm_cards = generate_cards(topic, copy_text, config, brand)
    if llm_cards:
        return llm_cards, "llm-from-copy"

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


def emit_progress(
    progress,
    *,
    stage: str,
    percent: int,
    message: str,
    done: int | None = None,
    total: int | None = None,
) -> None:
    if not progress:
        return
    payload = {
        "stage": stage,
        "percent": max(0, min(100, int(percent))),
        "message": message,
    }
    if done is not None:
        payload["done"] = done
    if total is not None:
        payload["total"] = total
    progress(payload)


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
    metaphor: str = "",
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

    # 用户编辑了画面提示词：覆盖该卡的视觉隐喻，清掉旧的创意层细节避免冲突。
    if metaphor and metaphor.strip():
        vs = cards[index - 1].setdefault("visual_style", {})
        vs["metaphor"] = metaphor.strip()
        for key in ("composition", "details", "accent_focus"):
            vs.pop(key, None)

    renderer = DirectCardRenderer(config=config, project_root=project_root)
    log(f"重新生成第 {index} 张卡…")
    path = renderer.render_one(cards, index, pkg, topic, style_plan)
    log("完成。")
    _append_prompt_log(
        project_root,
        {
            "timestamp": datetime.now(ZoneInfo(config.get("timezone", "Asia/Shanghai"))).isoformat(timespec="seconds"),
            "event": "regenerate",
            "package_name": package_name,
            "index": index,
            "extra_brief": extra_brief or "",
            "metaphor": metaphor or "",
        },
    )
    return {"package_name": package_name, "index": index, "card": path.name, "extra_brief": extra_brief}


def _card_bullets(card: dict) -> list[str]:
    bullets = card.get("bullets") or card.get("body") or []
    if isinstance(bullets, str):
        bullets = [item.strip() for item in bullets.splitlines() if item.strip()]
    if not isinstance(bullets, list):
        return []
    return [str(item).strip() for item in bullets if str(item).strip()]


def _count_chinese(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def build_publish_review(topic: str, cards: list[dict], xhs_post: str, wechat_article: str, config: dict) -> dict:
    """面向发布动作的轻量检查。

    这不是代码质量检查，而是帮 Web 工作台判断“现在离可发布还有多远”。
    """
    items: list[dict] = []

    def add(level: str, title: str, detail: str) -> None:
        items.append({"level": level, "title": title, "detail": detail})

    if len(cards) == 7:
        add("ok", "组图数量", "当前是 7 张，适合小红书组图发布。")
    else:
        add("fix", "组图数量", f"当前是 {len(cards)} 张，建议固定 7 张。")

    cover = cards[0] if cards else {}
    cover_title = str(cover.get("title", "")).strip()
    if not cover_title:
        add("fix", "封面标题", "封面标题为空，需要先补一个能独立成立的主标题。")
    elif len(cover_title) > 30:
        add("warn", "封面标题", f"封面标题 {len(cover_title)} 字，信息流里可能太长。")
    elif len(cover_title) < 8:
        add("warn", "封面标题", "封面标题偏短，可能缺少具体冲突或对象。")
    else:
        add("ok", "封面标题", "长度适中，可以在信息流里独立阅读。")

    titles = [str(card.get("title", "")).strip() for card in cards if str(card.get("title", "")).strip()]
    duplicates = sorted({title for title in titles if titles.count(title) > 1})
    if duplicates:
        add("warn", "卡片差异", "有重复标题：" + "、".join(duplicates[:3]))
    else:
        add("ok", "卡片差异", "未发现重复标题。")

    thin_cards = []
    long_title_cards = []
    missing_note_cards = []
    for index, card in enumerate(cards[1:], start=2):
        title = str(card.get("title", "")).strip()
        subtitle = str(card.get("subtitle", "")).strip()
        bullets = _card_bullets(card)
        note = str(card.get("note", "")).strip()
        body_len = len(title) + len(subtitle) + sum(len(item) for item in bullets)
        if body_len < 48:
            thin_cards.append(str(index))
        if len(title) > 26:
            long_title_cards.append(str(index))
        if not note:
            missing_note_cards.append(str(index))

    if thin_cards:
        add("fix", "内容厚度", "这些卡片信息偏薄：" + "、".join(thin_cards))
    else:
        add("ok", "内容厚度", "内容页信息量没有明显过薄。")
    if long_title_cards:
        add("warn", "卡片标题", "这些卡片标题偏长：" + "、".join(long_title_cards))
    else:
        add("ok", "卡片标题", "内容页标题长度整体可控。")
    if missing_note_cards:
        add("warn", "收束句", "这些卡片缺少底部 note：" + "、".join(missing_note_cards))
    else:
        add("ok", "收束句", "内容页都有收束句，发布时更完整。")

    combined = "\n".join([topic, xhs_post, wechat_article, json.dumps(cards, ensure_ascii=False)])
    hits = [phrase for phrase in AI_SMELL_PHRASES if phrase in combined]
    if hits:
        add("fix", "AI/营销腔", "发现：" + "、".join(hits[:8]))
    else:
        add("ok", "AI/营销腔", "未发现常见空泛词。")

    brand = config.get("brand", {}).get("name", "交易 Agent 实验室")
    if brand in combined:
        add("ok", "品牌露出", f"正文或卡片里已出现「{brand}」。")
    else:
        add("warn", "品牌露出", f"正文和卡片里没有出现「{brand}」。")

    xhs_chars = _count_chinese(xhs_post)
    if xhs_chars < 220:
        add("warn", "小红书正文", f"正文约 {xhs_chars} 个中文字符，可能偏薄。")
    elif xhs_chars > 1100:
        add("warn", "小红书正文", f"正文约 {xhs_chars} 个中文字符，可能太长。")
    else:
        add("ok", "小红书正文", f"正文约 {xhs_chars} 个中文字符，长度合适。")

    if "#" in xhs_post:
        add("ok", "话题标签", "小红书正文包含标签。")
    else:
        add("warn", "话题标签", "小红书正文没有标签，发布前可以补 3-5 个。")

    trading_terms = ["交易", "股票", "仓位", "买入", "卖出", "收益", "亏损", "财报", "行情"]
    has_trading_context = any(term in combined for term in trading_terms)
    if has_trading_context and "不构成投资建议" not in combined:
        add("fix", "风险提示", "交易/行情内容建议保留“不构成投资建议”。")
    elif has_trading_context:
        add("ok", "风险提示", "已包含“不构成投资建议”。")

    fix_count = sum(1 for item in items if item["level"] == "fix")
    warn_count = sum(1 for item in items if item["level"] == "warn")
    score = max(0, 100 - fix_count * 12 - warn_count * 5)
    if fix_count:
        summary = f"还有 {fix_count} 个必须处理项，建议先改文案再正式出图。"
    elif warn_count:
        summary = f"没有硬伤，有 {warn_count} 个可优化项。"
    else:
        summary = "已经接近可发布状态。"
    return {"score": score, "summary": summary, "items": items}


def update_package_cards(
    package_name: str,
    cards: list[dict],
    *,
    config: dict | None = None,
    config_path: Path | None = None,
    project_root: Path = PROJECT_ROOT,
) -> dict:
    """保存 Web 编辑后的卡片文案，并同步重建正文、发布清单和检查结果。"""
    if config is None:
        config = load_config(config_path or (project_root / "config.yaml"))

    output_dir = (project_root / "dist" / package_name).resolve()
    dist_dir = (project_root / "dist").resolve()
    if dist_dir not in output_dir.parents or not output_dir.is_dir():
        raise ValueError(f"发布包不存在：{package_name}")

    inputs_path = output_dir / "inputs.json"
    inputs = json.loads(inputs_path.read_text(encoding="utf-8")) if inputs_path.exists() else {}
    topic = str(inputs.get("topic") or package_name).strip()
    copy_text = str(inputs.get("copy_text") or "")
    cards_source = "edited-cards_used"
    copy_source = "inputs.json" if copy_text.strip() else "未提供"

    xhs_post, wechat_article = compose_body(
        topic=topic, cards=cards, config=config, cards_source=cards_source, copy_text=copy_text
    )
    quality = run_quality_checks(
        topic=topic, cards=cards, xhs_post=xhs_post, wechat_article=wechat_article, config=config
    )
    rendered_count = len(list(output_dir.glob("card_*.png"))) or len(cards)

    write_json(output_dir / "cards_used.json", {"cards": cards})
    write_markdown(output_dir / "小红书正文.md", xhs_post)
    write_markdown(output_dir / "公众号文章.md", wechat_article)
    write_markdown(
        output_dir / "发布清单.md",
        build_publish_checklist(
            topic=topic,
            cards_source=cards_source,
            copy_source=copy_source,
            rendered_count=rendered_count,
            quality=quality,
        ),
    )

    timezone = config.get("timezone", "Asia/Shanghai")
    updated_inputs = {
        **inputs,
        "timestamp": datetime.now(ZoneInfo(timezone)).isoformat(timespec="seconds"),
        "topic": topic,
        "copy_text": copy_text,
        "cards_edited_from_web": True,
    }
    write_json(inputs_path, updated_inputs)

    return {
        "package_name": output_dir.name,
        "xhs_md": xhs_post,
        "wechat_md": wechat_article,
        "cards": sorted(path.name for path in output_dir.glob("card_*.png")),
        "quality": {"score": quality.score, "warnings": list(quality.warnings)},
        "review": build_publish_review(topic, cards, xhs_post, wechat_article, config),
        "inputs": updated_inputs,
    }


def rebuild_package(
    package_name: str,
    *,
    mode: str = "local",
    extra_brief: str = "",
    refresh_style: bool = False,
    push: bool = False,
    config: dict | None = None,
    config_path: Path | None = None,
    project_root: Path = PROJECT_ROOT,
    log=print,
    progress=None,
) -> dict:
    """基于已存在发布包重建正文、检查清单，并按需重渲染卡片。"""
    if mode not in {"local", "direct", "background"}:
        raise ValueError(f"未知出图模式：{mode}")
    if config is None:
        config = load_config(config_path or (project_root / "config.yaml"))
    if mode == "local":
        config.setdefault("image_model", {})["enabled"] = False
    if extra_brief and extra_brief.strip():
        config["extra_brief"] = extra_brief.strip()
    emit_progress(progress, stage="prepare", percent=5, message="正在读取当前发布包…")

    output_dir = (project_root / "dist" / package_name).resolve()
    dist_dir = (project_root / "dist").resolve()
    if dist_dir not in output_dir.parents or not output_dir.is_dir():
        raise ValueError(f"发布包不存在：{package_name}")

    cards = read_cards_json(output_dir / "cards_used.json")
    inputs_path = output_dir / "inputs.json"
    inputs = json.loads(inputs_path.read_text(encoding="utf-8")) if inputs_path.exists() else {}
    topic = str(inputs.get("topic") or package_name).strip()
    copy_text = str(inputs.get("copy_text") or "")
    style = str(inputs.get("style") or "") or None

    style_plan_path = output_dir / "style_plan.json"
    style_plan = json.loads(style_plan_path.read_text(encoding="utf-8")) if style_plan_path.exists() else {}
    if refresh_style or not style_plan:
        log("正在重新规划风格与创意…")
        emit_progress(progress, stage="style", percent=12, message="正在重新规划风格与创意…", done=0, total=len(cards))
        cards, style_plan = apply_style_plan(
            cards=cards,
            topic=topic,
            copy_text=copy_text,
            config=config,
            style_override=style,
        )
        emit_progress(progress, stage="style", percent=24, message="风格与创意规划完成。", done=0, total=len(cards))

    log("正在重建正文与发布检查…")
    emit_progress(progress, stage="copy", percent=28, message="正在重建正文与发布检查…", done=0, total=len(cards))
    cards_source = "edited-cards_used"
    copy_source = "inputs.json" if copy_text.strip() else "未提供"
    xhs_post, wechat_article = compose_body(
        topic=topic, cards=cards, config=config, cards_source=cards_source, copy_text=copy_text
    )
    quality = run_quality_checks(
        topic=topic, cards=cards, xhs_post=xhs_post, wechat_article=wechat_article, config=config
    )

    write_markdown(output_dir / "小红书正文.md", xhs_post)
    write_markdown(output_dir / "公众号文章.md", wechat_article)
    write_json(output_dir / "cards_used.json", {"cards": cards})
    write_json(output_dir / "style_plan.json", style_plan)

    log("正在重渲染卡片图…")
    render_start = 32
    render_span = 58

    def render_progress(done: int, total: int, message: str) -> None:
        percent = render_start + round((done / max(1, total)) * render_span)
        emit_progress(
            progress,
            stage="render",
            percent=percent,
            message=message,
            done=done,
            total=total,
        )

    emit_progress(
        progress,
        stage="render",
        percent=render_start,
        message=f"开始重渲染卡片图，共 {len(cards)} 张。",
        done=0,
        total=len(cards),
    )
    if mode == "direct":
        renderer = DirectCardRenderer(config=config, project_root=project_root)
        rendered_paths = renderer.render_all(
            cards=cards,
            output_dir=output_dir,
            topic=topic,
            style_plan=style_plan,
            progress=render_progress,
            log=log,
        )
    else:
        renderer = CardRenderer(config=config, project_root=project_root)
        rendered_paths = renderer.render_all(cards=cards, output_dir=output_dir, topic=topic, progress=render_progress, log=log)
    emit_progress(
        progress,
        stage="render",
        percent=92,
        message=f"卡片图重渲染完成，共 {len(rendered_paths)} 张。",
        done=len(rendered_paths),
        total=len(cards),
    )

    emit_progress(progress, stage="finalize", percent=95, message="正在整理发布包…", done=len(rendered_paths), total=len(cards))
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

    if push:
        emit_progress(progress, stage="push", percent=97, message="正在推送到 Telegram…", done=len(rendered_paths), total=len(cards))
    pushed = maybe_push_telegram(config, output_dir, no_push=not push, log=log)
    timezone = config.get("timezone", "Asia/Shanghai")
    updated_inputs = {
        **inputs,
        "timestamp": datetime.now(ZoneInfo(timezone)).isoformat(timespec="seconds"),
        "topic": topic,
        "copy_text": copy_text,
        "style": style or "",
        "mode": mode,
        "extra_brief": extra_brief or inputs.get("extra_brief", ""),
        "push": bool(push),
        "rebuilt_from_web": True,
    }
    write_json(inputs_path, updated_inputs)
    emit_progress(progress, stage="done", percent=100, message="更新完成。", done=len(rendered_paths), total=len(cards))

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
        "review": build_publish_review(topic, cards, xhs_post, wechat_article, config),
        "pushed": pushed,
        "inputs": updated_inputs,
    }


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
    mode: str = "background",  # "background" | "local" | "direct"
    extra_brief: str = "",
    playbook: str = "",
    direction: str = "",
    push: bool = True,
    config: dict | None = None,
    config_path: Path | None = None,
    project_root: Path = PROJECT_ROOT,
    log=print,
    progress=None,
) -> dict:
    """跑完整生成流程，返回结构化结果。

    mode: background=固定字体排版 + AI 视觉层；local=本地排版草稿；direct=实验整卡直出。
    extra_brief: 额外创意指令，注入图像 prompt（界面上的「按提示词优化」）。
    """
    if config is None:
        config = load_config(config_path or (project_root / "config.yaml"))

    emit_progress(progress, stage="prepare", percent=3, message="正在读取配置和素材…")
    direct = mode == "direct"
    if mode == "local":
        config.setdefault("image_model", {})["enabled"] = False
    if extra_brief and extra_brief.strip():
        config["extra_brief"] = extra_brief.strip()
    if playbook and playbook.strip():
        config["playbook_id"] = playbook.strip()
    if direction and direction.strip():
        config["anchor"] = direction.strip()

    resolved_copy, copy_source = read_copy_input(copy_text=copy_text or None, copy_file=copy_file)
    cards, cards_source = load_cards(project_root, cards_file, topic, resolved_copy, config)
    emit_progress(
        progress,
        stage="prepare",
        percent=8,
        message=f"已准备 {len(cards)} 张卡片文案。",
        done=0,
        total=len(cards),
    )
    log("正在规划风格与创意…")
    emit_progress(progress, stage="style", percent=12, message="正在规划风格与创意…", done=0, total=len(cards))
    cards, style_plan = apply_style_plan(
        cards=cards, topic=topic, copy_text=resolved_copy, config=config, style_override=style
    )
    emit_progress(progress, stage="style", percent=24, message="风格与创意规划完成。", done=0, total=len(cards))

    timezone = config.get("timezone", "Asia/Shanghai")
    output_dir = make_output_dir(project_root, topic, timezone)

    emit_progress(progress, stage="copy", percent=28, message="正在生成正文和发布检查…", done=0, total=len(cards))
    xhs_post, wechat_article = compose_body(
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
    render_start = 32
    render_span = 58

    def render_progress(done: int, total: int, message: str) -> None:
        percent = render_start + round((done / max(1, total)) * render_span)
        emit_progress(
            progress,
            stage="render",
            percent=percent,
            message=message,
            done=done,
            total=total,
        )

    emit_progress(
        progress,
        stage="render",
        percent=render_start,
        message=f"开始生成卡片图，共 {len(cards)} 张。",
        done=0,
        total=len(cards),
    )
    if direct:
        renderer = DirectCardRenderer(config=config, project_root=project_root)
        rendered_paths = renderer.render_all(
            cards=cards,
            output_dir=output_dir,
            topic=topic,
            style_plan=style_plan,
            progress=render_progress,
            log=log,
        )
    else:
        renderer = CardRenderer(config=config, project_root=project_root)
        rendered_paths = renderer.render_all(cards=cards, output_dir=output_dir, topic=topic, progress=render_progress, log=log)
    emit_progress(
        progress,
        stage="render",
        percent=92,
        message=f"卡片图生成完成，共 {len(rendered_paths)} 张。",
        done=len(rendered_paths),
        total=len(cards),
    )

    emit_progress(progress, stage="finalize", percent=95, message="正在整理发布包…", done=len(rendered_paths), total=len(cards))
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

    if push:
        emit_progress(progress, stage="push", percent=97, message="正在推送到 Telegram…", done=len(rendered_paths), total=len(cards))
    pushed = maybe_push_telegram(config, output_dir, no_push=not push, log=log)

    inputs = {
        "timestamp": datetime.now(ZoneInfo(timezone)).isoformat(timespec="seconds"),
        "topic": topic,
        "copy_text": copy_text,
        "style": style or "",
        "mode": mode,
        "extra_brief": extra_brief or "",
        "playbook": playbook or "",
        "direction": direction or "",
        "push": bool(push),
    }
    write_json(output_dir / "inputs.json", inputs)
    _append_prompt_log(
        project_root,
        {
            **inputs,
            "event": "generate",
            "package_name": output_dir.name,
            "score": quality.score,
            "cards_source": cards_source,
            "card_count": len(cards),
            "style_preset": (style_plan.get("style_preset") or {}).get("key"),
        },
    )
    emit_progress(progress, stage="done", percent=100, message="生成完成。", done=len(rendered_paths), total=len(cards))

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
