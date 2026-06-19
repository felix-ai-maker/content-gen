from __future__ import annotations

import base64
import json
import os
import textwrap
import time
from io import BytesIO
from pathlib import Path

from PIL import Image


class DirectCardRenderer:
    """Ask the image model to design the full card, including typography."""

    def __init__(self, config: dict, project_root: Path):
        self.config = config
        self.project_root = project_root
        self.canvas = config.get("canvas", {})
        self.width = int(self.canvas.get("width", 1080))
        self.height = int(self.canvas.get("height", 1440))
        self.brand = config.get("brand", {}).get("name", "交易 Agent 实验室")
        self._last_ai_request_at = 0.0
        self.records: list[dict] = []
        self.preset: dict = {}

    def render_all(self, cards: list[dict], output_dir: Path, topic: str, style_plan: dict) -> list[Path]:
        self.preset = style_plan.get("style_preset") or {}
        paths: list[Path] = []
        for index, card in enumerate(cards, start=1):
            image = self._generate_card(
                cards=cards,
                card=card,
                index=index,
                total=len(cards),
                topic=topic,
                style_plan=style_plan,
            )
            path = output_dir / f"card_{index:02d}.png"
            image.save(path, "PNG", optimize=True)
            paths.append(path)
        self._write_generation_meta(output_dir)
        return paths

    def _generate_card(
        self,
        cards: list[dict],
        card: dict,
        index: int,
        total: int,
        topic: str,
        style_plan: dict,
    ) -> Image.Image:
        model_cfg = self.config.get("image_model", {})
        if not model_cfg.get("enabled", True):
            raise RuntimeError("Direct AI card mode requires image_model.enabled=true.")

        provider = str(model_cfg.get("provider", "vertex_gemini")).lower()
        if provider not in {"vertex_gemini", "gemini", "google_gemini"}:
            raise RuntimeError("Direct AI card mode currently supports provider=vertex_gemini.")

        image = self._try_vertex_gemini_card(model_cfg, cards, card, index, total, topic, style_plan)
        if image is None:
            raise RuntimeError(f"Nano Banana did not return an image for card_{index:02d}.")

        self.records.append(
            {
                "card": f"card_{index:02d}.png",
                "mode": "direct_full_card",
                "provider": model_cfg.get("provider", "vertex_gemini"),
                "model": model_cfg.get("model", "gemini-3.1-flash-image"),
                "project": model_cfg.get("project") or os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT"),
                "location": model_cfg.get("location", "global"),
                "prompt": self._full_card_prompt(card, cards, index, total, topic, style_plan),
            }
        )
        return self._fit_to_canvas(image.convert("RGB"))

    def _try_vertex_gemini_card(
        self,
        model_cfg: dict,
        cards: list[dict],
        card: dict,
        index: int,
        total: int,
        topic: str,
        style_plan: dict,
    ) -> Image.Image | None:
        from google import genai
        from google.genai.types import GenerateContentConfig, HttpOptions, Modality

        client = self._build_google_client(genai, HttpOptions, model_cfg)
        base_prompt = self._full_card_prompt(card, cards, index, total, topic, style_plan)
        attempts = max(1, int(model_cfg.get("recitation_retry_attempts", 3)))
        model_name = model_cfg.get("model", "gemini-3.1-flash-image")

        for attempt in range(attempts):
            prompt = base_prompt if attempt == 0 else base_prompt + self._recitation_nudge(attempt)
            self._wait_for_ai_slot(model_cfg)
            response = self._with_quota_retry(
                lambda p=prompt: client.models.generate_content(
                    model=model_name,
                    contents=p,
                    config=GenerateContentConfig(
                        response_modalities=[Modality.TEXT, Modality.IMAGE],
                    ),
                ),
                model_cfg,
            )
            image = self._image_from_gemini_response(response)
            if image is not None:
                return image
            reason = self._finish_reason(response)
            if "RECITATION" in reason and attempt < attempts - 1:
                print(
                    f"card_{index:02d} 撞 IMAGE_RECITATION（不出图、不扣费），"
                    f"换更抽象的措辞重试 {attempt + 1}/{attempts - 1}..."
                )
                continue
            # 非版权过滤的空响应，或重试已用尽：不再继续。
            break
        return None

    def _full_card_prompt(
        self,
        card: dict,
        cards: list[dict],
        index: int,
        total: int,
        topic: str,
        style_plan: dict,
    ) -> str:
        style = card.get("visual_style", {}) if isinstance(card.get("visual_style"), dict) else {}
        title_lines = self._title_lines(card)
        display_title_lines = self._display_title_lines(card, title_lines)
        bullets = self._normalize_bullets(card)
        display_bullets = self._display_bullets(card, bullets)
        chips = card.get("chips") or []
        if isinstance(chips, str):
            chips = [item.strip() for item in chips.split("|") if item.strip()]
        chip_text = "｜".join(str(item).strip() for item in chips if str(item).strip())
        exact_text = self._exact_text_block(card, index, total, display_title_lines, display_bullets, chip_text)

        if index == 1 or card.get("type") == "cover":
            page_instruction = self._cover_instruction(card, display_title_lines, chip_text)
        else:
            page_instruction = self._content_instruction(card, index, display_bullets)

        context_block = self._context_block(cards, card, index, total)

        preset = self.preset or {}
        preset_name = preset.get("name", "")
        art_direction = preset.get("art_direction", "")
        typography = preset.get("typography", "画面要有清楚的字体层级，标题有冲击但不过大。")
        palette_line = self._palette_line(preset.get("palette", {}))

        creative_brief_block = ""
        if style.get("composition") or style.get("details") or style.get("accent_focus"):
            creative_brief_block = (
                f"构图与焦点：{style.get('composition', '')}；"
                f"细节与材质光影：{style.get('details', '')}；"
                f"强调色落点：{style.get('accent_focus', '')}"
            )

        extra_brief = (self.config.get("extra_brief") or "").strip()
        extra_block = f"额外创意要求（优先满足）：{extra_brief}" if extra_brief else ""

        return textwrap.dedent(
            f"""
            请直接生成一张完整的竖版社媒图文卡片，尺寸 {self.width}×{self.height}。
            你不是在生成背景图，也不是在排 PPT；你是在做一张可以直接发布的内容卡。

            你的角色：
            - 移动端商业/科技内容主编
            - 高级信息图视觉设计师
            - 懂金融交易内容的编辑型设计师

            发布目标：
            让用户在手机信息流里停下来看，并觉得“这个人有自己的系统和判断”，而不是觉得这是 AI 生成海报。

            下面是给你理解内容用的完整素材。它不是画面文字清单，不能照搬到图里。
            你要先理解整套内容的逻辑、情绪、冲突、隐喻，再设计当前这一页。

            【完整理解素材，不要直接画进图片】
            系列主题：{topic}
            自动视觉主题：{style_plan.get("name", "")} / {style_plan.get("theme", "")}
            当前页视觉隐喻：{style.get("metaphor", "")}
            当前页视觉角色：{style.get("visual_role", "")}

            {context_block}

            只允许渲染下面【可见文字】里的文字。除此之外，画面中不要出现任何可读文字、英文、数字、伪文字或标签。
            但插画物件、纸张、界面、工具、环境可以有丰富的纹理、结构、刻度、无字图标符号、光影和细节层次——只是这些细节里不能出现任何可读文字或伪文字。
            【可见文字】必须逐字准确，不要增删改字；如果文字较多，优先保证标题、核心观点和品牌准确。

            【可见文字】
            {exact_text}

            本页创意 brief：
            {page_instruction}
            {creative_brief_block}

            视觉方向（本页风格模板：{preset_name}）：
            - 内容驱动，不要套固定模板。每一页都要根据观点生成不同的、有想象力的画面隐喻，不要每页都是桌面 + 卡片。
            - 风格基调：{art_direction}
            - {palette_line}
            - 画面要有一个清楚的主视觉，并围绕它叠加有层次的支撑元素、环境、景深和材质光影；画面要充实、有信息量、有设计细节，但保持秩序和一个明确焦点。
            - 质感要像精心设计过的高级图文卡：有层级、节奏、焦点和丰富的细节装饰，留白服务于焦点而不是让画面空洞。
            - 排版：{typography}
            - 保持移动端阅读感：第一眼看到标题，第二眼理解视觉隐喻，第三眼读到核心观点。

            排版要求：
            - 中文必须清晰、准确、可读。
            - 文字数量少而有力，避免满屏说明。
            - 顶部可以有页码或系列信息，但不要做成复杂仪表盘。
            - 底部只出现一次“{self.brand}”，作为页脚。
            - 视觉和文字不要互相遮挡。

            禁止：
            - 不要简单背景 + 大字。
            - 不要通用 AI 科技背景、机器人、芯片、神经网络大脑、代码屏幕、全息蓝光。
            - 不要 K 线、红绿涨跌、牛熊、金币、交易大屏。
            - 不要廉价财经号风格，不要知识付费海报风格。
            - 不要在插画里生成任何额外文字、英文缩写、时间、编号、Logo、假标签。
            - 不要给物件命名，不要给纸张写标题，不要给线条写说明，不要给系列加自创名称。
            - 不要把 prompt 里的说明词画出来。
            - 不要让所有页面长得一模一样。
            {extra_block}
            """
        ).strip()

    def _context_block(self, cards: list[dict], card: dict, index: int, total: int) -> str:
        return textwrap.dedent(
            f"""
            【整套卡片逻辑】
            {self._series_outline(cards)}

            【当前页完整文案素材】
            {self._card_copy_context(card, index, total)}

            【设计任务】
            - 你必须利用上面的完整文案来理解本页，而不是只看一句视觉提示词。
            - 找出本页最核心的冲突：{self._page_conflict(card)}
            - 把这个冲突转化成一个能一眼看懂的画面隐喻。
            - 图片里只画【可见文字】，完整文案只用于理解画面、情绪和信息层级。
            """
        ).strip()

    def _series_outline(self, cards: list[dict]) -> str:
        lines: list[str] = []
        for card_index, item in enumerate(cards, start=1):
            title = self._plain(item.get("title", ""))
            subtitle = self._plain(item.get("subtitle", ""))
            highlight = self._plain(item.get("highlight", ""))
            note = self._plain(item.get("note", ""))
            summary_parts = [part for part in [subtitle, highlight, note] if part]
            summary = " / ".join(summary_parts[:2])
            if len(summary) > 80:
                summary = summary[:79].rstrip("，。；、 ") + "。"
            lines.append(f"{card_index:02d}. {title}：{summary}")
        return "\n".join(lines)

    def _card_copy_context(self, card: dict, index: int, total: int) -> str:
        bullets = self._normalize_bullets(card)
        lines = [f"页码：{index:02d}/{total:02d}"]
        for label, key in [
            ("类型", "type"),
            ("系列小字", "kicker"),
            ("标签", "badge"),
            ("标题", "title"),
            ("副标题", "subtitle"),
            ("核心判断", "highlight"),
            ("封面钩子", "hook"),
            ("底部结论", "note"),
        ]:
            value = self._plain(card.get(key, ""))
            if value:
                lines.append(f"{label}：{value}")
        if bullets:
            lines.append("完整要点：")
            lines.extend(f"- {bullet}" for bullet in bullets)
        chips = card.get("chips") or []
        if isinstance(chips, str):
            chips = [item.strip() for item in chips.split("|") if item.strip()]
        if chips:
            lines.append("语气标签：" + " / ".join(self._plain(item) for item in chips if self._plain(item)))
        return "\n".join(lines)

    def _page_conflict(self, card: dict) -> str:
        title = self._plain(card.get("title", ""))
        highlight = self._plain(card.get("highlight", ""))
        note = self._plain(card.get("note", ""))
        subtitle = self._plain(card.get("subtitle", ""))
        if "不是" in highlight and "而是" in highlight:
            return highlight
        if "不是" in title and "而是" in title:
            return title
        if note:
            return note
        if highlight:
            return highlight
        return subtitle or title

    @staticmethod
    def _plain(value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, list):
            return " / ".join(DirectCardRenderer._plain(item) for item in value)
        if isinstance(value, dict):
            return " / ".join(
                DirectCardRenderer._plain(item)
                for item in value.values()
                if DirectCardRenderer._plain(item)
            )
        return str(value).strip()

    def _cover_instruction(self, card: dict, title_lines: list[str], chip_text: str) -> str:
        visual = self._visual_subject(card)
        return textwrap.dedent(
            f"""
            封面要有“停下来”的力量，但不要粗暴大字海报。
            - 主标题 2 行左右，放在上半区或左上区，留出呼吸感。
            - 副标题只做一句解释，不要写成长段。
            - 主视觉占画面 50%-65%，要和本页标题、核心观点直接相关，并用有层次的场景和细节把它撑满。
            - 按本页风格模板的基调构图，主视觉服务于下面的隐喻，可以加入丰富的环境、道具和光影细节，但不要套用与内容无关的固定场景。
            - 不要做股票行情感，不要交易软件感，不要蓝色仪表盘感。

            主视觉：
            {visual}
            """
        ).strip()

    def _content_instruction(self, card: dict, index: int, bullets: list[str]) -> str:
        visual = self._visual_subject(card)
        bullet_lines = "\n".join(f"{i:02d} {item}" for i, item in enumerate(bullets, start=1))
        return textwrap.dedent(
            f"""
            内容页不是 PPT，也不是说明书。每页只讲一个判断。
            - 页码小，不抢戏。
            - 标题清楚，不要超大。
            - 核心判断做成视觉焦点之一，可以像一句批注、研究笔记、贴纸或画面中的重点信息。
            - 主视觉占 55%-65%，用一个有层次、有细节的隐喻场景解释这一页，而不是放一堆零散图标。
            - 底部一句作为收束，让用户愿意继续翻下一张。
            - 画面结构和隐喻每页都要明显不同，不要雷同、不要每页都套桌面 + 卡片那套元素。

            主视觉：
            {visual}
            """
        ).strip()

    def _visual_subject(self, card: dict) -> str:
        style = card.get("visual_style", {}) if isinstance(card.get("visual_style"), dict) else {}
        metaphor = style.get("metaphor", "")
        art = (self.preset or {}).get("art_direction", "")
        direction = f"画面整体服从风格模板基调：{art}" if art else ""
        if metaphor:
            return (
                f"围绕这个隐喻做一个主视觉：{metaphor}。"
                "以它为核心焦点，叠加有层次的支撑细节、道具、环境和材质光影，让画面充实、有信息量、有设计感，但保持秩序和焦点。"
                f"{direction}"
                "物件可以有丰富的无字纹理和结构，但不要出现可读文字，不要使用机器人、芯片、代码屏或股票元素。"
            )
        return (
            "画一个和本页观点直接相关、有层次和细节的解释型插画场景，不要做纯装饰背景，也不要太空。"
            f"{direction}"
        )

    def _exact_text_block(
        self,
        card: dict,
        index: int,
        total: int,
        title_lines: list[str],
        bullets: list[str],
        chip_text: str,
    ) -> str:
        if index == 1 or card.get("type") == "cover":
            lines = []
            if card.get("kicker"):
                lines.append(str(card.get("kicker")))
            lines.append(f"{index:02d}/{total:02d}")
            if card.get("badge"):
                lines.append(str(card.get("badge")))
            lines.extend(title_lines)
            if card.get("subtitle"):
                lines.append(self._short_text(str(card.get("subtitle")), 38))
            # Cover chips tend to become oversized in native image text rendering.
            # Keep them in Markdown, but leave the cover visually cleaner.
        else:
            lines = [f"{index:02d}"]
            lines.extend(title_lines[:1])
            if card.get("highlight"):
                lines.append(self._short_text(str(card.get("highlight")), 42))
            for bullet in bullets:
                lines.append(str(bullet))
            if card.get("note"):
                lines.append(self._short_text(str(card.get("note")), 34))
        lines.append(self.brand)
        return "\n".join(lines)

    @staticmethod
    def _short_text(text: str, max_chars: int) -> str:
        normalized = str(text).strip()
        if len(normalized) <= max_chars:
            return normalized
        # 在上限附近截到最后一个标点边界，保证是完整子句，不留半个词 + 省略号。
        window = normalized[: max_chars + 1]
        for i in range(len(window) - 1, max(0, len(window) - 16), -1):
            if window[i] in "，。；、！？,.;!?":
                trimmed = window[: i + 1].rstrip("，、；,; ")
                if trimmed:
                    return trimmed
        return normalized[:max_chars].rstrip("，。；、 ")

    @staticmethod
    def _title_lines(card: dict) -> list[str]:
        blocks = card.get("title_blocks")
        if isinstance(blocks, list) and blocks:
            lines = []
            for block in blocks:
                if isinstance(block, dict) and block.get("text"):
                    lines.append(str(block["text"]).strip())
                elif isinstance(block, str):
                    lines.append(block.strip())
            if lines:
                return lines
        title_lines = card.get("title_lines")
        if isinstance(title_lines, list) and title_lines:
            return [str(line).strip() for line in title_lines if str(line).strip()]
        return [str(card.get("title", "")).strip()]

    @staticmethod
    def _display_title_lines(card: dict, title_lines: list[str]) -> list[str]:
        if not (card.get("type") == "cover" or len(title_lines) > 2):
            return title_lines
        joined = "".join(title_lines)
        if "交易最危险的" in joined and "不是亏钱" in joined and "为什么买" in joined:
            return ["交易最危险的不是亏钱", "是你忘了为什么买"]
        if len(title_lines) <= 2:
            return title_lines
        midpoint = max(1, len(title_lines) // 2)
        return ["".join(title_lines[:midpoint]), "".join(title_lines[midpoint:])]

    @staticmethod
    def _normalize_bullets(card: dict) -> list[str]:
        bullets = card.get("bullets")
        if bullets is None:
            bullets = card.get("body", [])
        if isinstance(bullets, str):
            bullets = [item.strip() for item in bullets.splitlines() if item.strip()]
        if not isinstance(bullets, list):
            return []
        return [str(item).strip() for item in bullets if str(item).strip()]

    @staticmethod
    def _display_bullets(card: dict, bullets: list[str]) -> list[str]:
        if card.get("type") == "cover":
            return []
        # Full bullet depth is kept in Markdown. Image cards stay cleaner and
        # let the visual metaphor carry the argument.
        if card.get("highlight") and card.get("note"):
            return []
        short_items: list[str] = []
        for item in bullets[:1]:
            text = DirectCardRenderer._short_text(str(item).strip(), 26)
            if text:
                short_items.append(text)
        return short_items

    def _write_generation_meta(self, output_dir: Path) -> None:
        payload = {
            "image_model": self.config.get("image_model", {}),
            "canvas": {"width": self.width, "height": self.height},
            "mode": "direct_full_card",
            "style_preset": self.preset.get("key") or self.preset.get("name"),
            "cards": self.records,
        }
        (output_dir / "generation_meta.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _wait_for_ai_slot(self, model_cfg: dict) -> None:
        interval = float(model_cfg.get("request_interval_seconds", 12))
        if interval <= 0:
            return
        elapsed = time.monotonic() - self._last_ai_request_at
        if elapsed < interval:
            time.sleep(interval - elapsed)
        self._last_ai_request_at = time.monotonic()

    @staticmethod
    def _with_quota_retry(call, model_cfg: dict):
        attempts = int(model_cfg.get("quota_retry_attempts", 2)) + 1
        delay = float(model_cfg.get("quota_retry_seconds", 75))
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                return call()
            except Exception as exc:
                last_error = exc
                if "429" not in str(exc) and "RESOURCE_EXHAUSTED" not in str(exc):
                    raise
                if attempt < attempts - 1:
                    print(f"Vertex quota hit, waiting {int(delay)}s before retry...")
                    time.sleep(delay)
        if last_error:
            raise last_error
        raise RuntimeError("Vertex request failed without an exception.")

    @staticmethod
    def _build_google_client(genai, HttpOptions, model_cfg: dict):
        project = model_cfg.get("project") or os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT")
        location = (
            model_cfg.get("location")
            or os.getenv("GOOGLE_CLOUD_LOCATION")
            or os.getenv("GOOGLE_CLOUD_REGION")
            or "global"
        )
        api_key_env = model_cfg.get("api_key_env", "GOOGLE_API_KEY")
        api_key = model_cfg.get("api_key") or os.getenv(api_key_env) or os.getenv("GEMINI_API_KEY")
        http_options = HttpOptions(api_version=model_cfg.get("api_version", "v1"))

        if project:
            return genai.Client(vertexai=True, project=project, location=location, http_options=http_options)
        if api_key:
            return genai.Client(vertexai=True, api_key=api_key, http_options=http_options)
        return genai.Client(http_options=http_options)

    @staticmethod
    def _palette_line(palette: dict) -> str:
        if not palette:
            return "配色克制、有质感，强调色只做点睛，不要全屏铺满。"
        return (
            f"严格使用这套配色：背景纸色 {palette.get('paper', '')}、"
            f"主文字色 {palette.get('ink', '')}、强调色 {palette.get('accent', '')}、"
            f"辅助色 {palette.get('support', '')}；强调色只做点睛，不要全屏铺满。"
        )

    @staticmethod
    def _finish_reason(response) -> str:
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return ""
        reason = getattr(candidates[0], "finish_reason", None)
        if reason is None:
            return ""
        return getattr(reason, "name", str(reason)).upper()

    @staticmethod
    def _recitation_nudge(attempt: int) -> str:
        variants = [
            "\n\n[重试提示] 上一次因「版权复述」被安全系统拒绝出图。"
            "请用完全原创、抽象的几何形体、纸张材质与线条来表达隐喻，"
            "不要参考任何已有插画、摄影、绘画作品、品牌、影视画面或可识别角色，"
            "不要生成任何能被追溯到具体来源的画面。",
            "\n\n[重试提示] 仍被拒绝。请进一步极简化："
            "只用朴素纸张、大面积留白、少量基础几何体与细线表达本页观点，"
            "去掉一切复杂具象场景，保持原创、通用、无任何可识别出处。",
        ]
        idx = min(max(attempt - 1, 0), len(variants) - 1)
        return variants[idx]

    @staticmethod
    def _image_from_gemini_response(response) -> Image.Image | None:
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return None
        content = getattr(candidates[0], "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            inline_data = getattr(part, "inline_data", None)
            if not inline_data:
                continue
            data = getattr(inline_data, "data", None)
            if not data:
                continue
            if isinstance(data, str):
                data = base64.b64decode(data)
            return Image.open(BytesIO(data)).convert("RGB")
        return None

    def _fit_to_canvas(self, image: Image.Image) -> Image.Image:
        ratio = max(self.width / image.width, self.height / image.height)
        resized = image.resize((int(image.width * ratio), int(image.height * ratio)), Image.Resampling.LANCZOS)
        left = (resized.width - self.width) // 2
        top = (resized.height - self.height) // 2
        return resized.crop((left, top, left + self.width, top + self.height))
