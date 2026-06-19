"""文本创意层：用 DeepSeek 等文本大模型为每页创意生成「视觉 brief」。

设计原则：
- 只是在规则隐喻库之前加一层创意。任何失败（未启用 / 无 key / 网络 / 超时 /
  输出不合格）都返回 None，让调用方回退到 style_director 的规则库，绝不阻断出图。
- OpenAI 兼容的 chat/completions 接口，用标准库 urllib，不引入新依赖。
- API key 只从环境变量读，绝不落盘。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


# 底线约束：DeepSeek 创意时必须遵守，避免破坏现有视觉规则。
HARD_CONSTRAINTS = (
    "1) 画面里不要出现任何可读文字、英文、数字、伪文字、标签或 Logo（中文标题由后续排版另加）；"
    "2) 不要 K 线、红绿涨跌、牛熊、金币、交易软件大屏等廉价财经视觉；"
    "3) 不要机器人、芯片、神经网络大脑、代码屏、全息蓝光等通用 AI 科技俗套；"
    "4) 跳出「桌面 + 卡片 + 档案袋」这类办公静物，每页用不同题材的隐喻；"
    "5) 画面要丰富、有层次、有材质光影和景深，但保持秩序和一个明确焦点。"
)


def _card_brief_context(cards: list[dict]) -> str:
    lines: list[str] = []
    for index, card in enumerate(cards, start=1):
        parts = [f"第{index}页"]
        for label, key in [("标题", "title"), ("副标题", "subtitle"),
                           ("核心判断", "highlight"), ("底部结论", "note")]:
            value = str(card.get(key, "") or "").strip()
            if value:
                parts.append(f"{label}：{value}")
        bullets = card.get("bullets")
        if isinstance(bullets, list) and bullets:
            parts.append("要点：" + " / ".join(str(b).strip() for b in bullets if str(b).strip()))
        lines.append("；".join(parts))
    return "\n".join(lines)


def _build_messages(
    topic: str,
    copy_text: str,
    cards: list[dict],
    profile: dict,
    preset: dict,
    fallback_examples: list[str],
) -> list[dict]:
    examples = "\n".join(f"- {item}" for item in fallback_examples[:5] if item)
    system = (
        "你是资深移动端视觉创意总监，为小红书 / 公众号的高级知识卡片设计画面创意。"
        "你只产出画面创意，不写文案；中文标题正文由后续本地排版处理。"
    )
    user = (
        f"系列选题：{topic}\n"
        f"内容领域主题：{profile.get('theme', '')}\n"
        f"视觉风格模板：{preset.get('name', '')}——{preset.get('art_direction', '')}\n"
        f"配色：背景 {preset.get('palette', {}).get('paper', '')} / "
        f"文字 {preset.get('palette', {}).get('ink', '')} / "
        f"强调 {preset.get('palette', {}).get('accent', '')}\n\n"
        f"整套卡片文案（共 {len(cards)} 页）：\n{_card_brief_context(cards)}\n\n"
        f"风格基调示例（仅供参考方向、不要照抄，请为本选题原创）：\n{examples}\n\n"
        f"必须遵守的底线：{HARD_CONSTRAINTS}\n\n"
        f"请为每一页创意一个画面 brief。严格只输出一个 JSON 数组，共 {len(cards)} 个元素，"
        "每个元素形如 "
        '{"index": 1, "metaphor": "本页主隐喻场景（跨题材、有想象力）", '
        '"composition": "构图与焦点位置", "details": "支撑元素、材质、光影、景深等细节", '
        '"accent_focus": "强调色用在哪里"}。'
        "不要输出 JSON 以外的任何内容。"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _post_chat(base_url: str, api_key: str, payload: dict, timeout: float) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
    return body["choices"][0]["message"]["content"]


def _parse_briefs(content: str, expected: int) -> list[dict] | None:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        briefs = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(briefs, list) or len(briefs) != expected:
        return None
    normalized: list[dict] = []
    for i, item in enumerate(briefs, start=1):
        if not isinstance(item, dict):
            return None
        metaphor = str(item.get("metaphor", "")).strip()
        if not metaphor:
            return None
        normalized.append(
            {
                "index": int(item.get("index", i)),
                "metaphor": metaphor,
                "composition": str(item.get("composition", "")).strip(),
                "details": str(item.get("details", "")).strip(),
                "accent_focus": str(item.get("accent_focus", "")).strip(),
            }
        )
    return normalized


def generate_visual_briefs(
    topic: str,
    copy_text: str,
    cards: list[dict],
    profile: dict,
    preset: dict,
    llm_cfg: dict | None,
    fallback_examples: list[str],
) -> list[dict] | None:
    """成功返回每页 brief 列表；任何失败返回 None（调用方回退规则库）。"""
    if not llm_cfg or not llm_cfg.get("enabled"):
        return None
    api_key = os.getenv(str(llm_cfg.get("api_key_env", "DEEPSEEK_API_KEY")))
    if not api_key:
        print("[creative_llm] 未找到 API key 环境变量，回退规则隐喻库。")
        return None

    payload = {
        "model": llm_cfg.get("model", "deepseek-chat"),
        "messages": _build_messages(topic, copy_text, cards, profile, preset, fallback_examples),
        "temperature": float(llm_cfg.get("temperature", 0.9)),
        "stream": False,
    }
    try:
        content = _post_chat(
            base_url=str(llm_cfg.get("base_url", "https://api.deepseek.com")),
            api_key=api_key,
            payload=payload,
            timeout=float(llm_cfg.get("timeout_seconds", 60)),
        )
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as exc:
        print(f"[creative_llm] 调用失败，回退规则隐喻库：{exc}")
        return None

    briefs = _parse_briefs(content, expected=len(cards))
    if briefs is None:
        print("[creative_llm] 输出不合格（非预期 JSON），回退规则隐喻库。")
        return None
    print(f"[creative_llm] DeepSeek 创意层成功生成 {len(briefs)} 页视觉 brief。")
    return briefs
