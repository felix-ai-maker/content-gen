from __future__ import annotations

from datetime import date
import re

from copy_pipeline import estimate_reading_minutes, extract_sentences, remove_ai_smell


def _brand(config: dict) -> str:
    return config.get("brand", {}).get("name", "交易 Agent 实验室")


def _card_title(card: dict, fallback: str) -> str:
    return str(card.get("title") or fallback).strip()


def _card_bullets(card: dict) -> list[str]:
    bullets = card.get("bullets") or card.get("body") or []
    if isinstance(bullets, str):
        bullets = [item.strip() for item in bullets.splitlines() if item.strip()]
    return [str(item).strip() for item in bullets if str(item).strip()]


def _plain_title(topic: str) -> str:
    title = topic.strip().rstrip("。！？!?")
    title = re.sub(r"[｜|]\s*(小红书)?[^｜|]*(视觉|风格|发布|候选|终版|版本)[^｜|]*$", "", title).strip()
    return title


def _first_good_sentence(copy_text: str, fallback: str) -> str:
    sentences = extract_sentences(copy_text) if copy_text.strip() else []
    for sentence in sentences:
        if 14 <= len(sentence) <= 48:
            return sentence.rstrip("。！？") + "。"
    return fallback


def _compact_bullets(cards: list[dict], limit: int = 4) -> list[str]:
    result: list[str] = []
    for card in cards:
        if card.get("type") == "cover":
            continue
        title = _card_title(card, "")
        bullets = _card_bullets(card)
        if title and bullets:
            result.append(f"{title}：{bullets[0].rstrip('。')}")
        elif title:
            result.append(title)
        if len(result) >= limit:
            break
    return result


def build_xhs_post(
    topic: str,
    cards: list[dict],
    config: dict,
    cards_source: str,
    copy_text: str = "",
) -> str:
    brand = _brand(config)
    hashtags = config.get("content", {}).get(
        "hashtags",
        ["#交易Agent", "#AI工作流", "#量化交易", "#个人知识系统", "#自动化"],
    )
    title = _plain_title(topic)
    opening = _first_good_sentence(
        copy_text,
        "我最近越来越觉得，交易系统最难的地方不是多接一个模型，而是让每天的判断留下痕迹。",
    )
    focus = _compact_bullets(cards, limit=4)
    highlights = [str(card.get("highlight", "")).strip() for card in cards if str(card.get("highlight", "")).strip()]

    lines = [
        f"# {title}",
        "",
        opening,
        "",
        "我想搭一个自己的交易 Agent，不是因为我相信它能神奇预测涨跌。",
        "",
        "恰恰相反，我现在更想解决的是一个很朴素的问题：我每天看了那么多信息，为什么复盘时还是说不清自己当时到底怎么判断的？",
        "",
        "所以这套系统第一版会先盯住四件事：",
        "",
    ]
    for index, item in enumerate(focus, start=1):
        lines.append(f"{index}. {item}")

    if highlights:
        lines.extend(["", "我给自己定了一个很硬的标准：", ""])
        for item in highlights[:3]:
            lines.append(f"- {item}")

    lines.extend(
        [
            "",
            "我不希望 Agent 变成一个替我拍板的黑箱。",
            "",
            "更理想的状态是：它把资料摆好，把假设写清，把风险提醒出来，把反证条件也放在桌面上。最后那一下仍然由人负责。",
            "",
            "如果一个工具让人更冲动，那它就不适合交易。好的自动化，应该让人慢下来，让每次行动都能回到证据链。",
            "",
            f"这套图文先放在「{brand}」里迭代。下一步我会把它接到真实流程里：选题、资料整理、检查清单、复盘记录。",
            "",
            "不构成投资建议。这里只记录我搭系统、约束自己、减少冲动的过程。",
            "",
            " ".join(hashtags),
        ]
    )
    return remove_ai_smell("\n".join(lines))


def build_wechat_article(
    topic: str,
    cards: list[dict],
    config: dict,
    cards_source: str,
    copy_text: str = "",
) -> str:
    brand = _brand(config)
    today = date.today().isoformat()
    title = _plain_title(topic)
    opening = _first_good_sentence(
        copy_text,
        "我最近在整理自己的交易流程时，反复卡在同一个地方：信息很多，判断也不少，但真正能留下来、下次还能复用的东西并不多。",
    )
    body_preview = "\n".join(_compact_bullets(cards, limit=6))
    reading_minutes = estimate_reading_minutes(body_preview + copy_text)

    lines = [
        f"# {title}",
        "",
        f"> {brand} · {today} · 预计阅读 {reading_minutes} 分钟",
        "",
        "## 开头先说人话",
        "",
        opening,
        "",
        "我搭交易 Agent，不是为了得到一个神秘答案。更实际的目标是：把每天重复出现的研究动作固定下来，让信息、假设、风险和复盘都能被看见。",
        "",
        "这件事如果做得不好，会很像另一个花哨工具；如果做得好，它应该像一张安静的工作台。",
        "",
    ]

    for index, card in enumerate(cards[1:], start=1):
        title = _card_title(card, f"卡片 {index:02d}")
        lines.extend([f"## {index}. {title}", ""])
        subtitle = card.get("subtitle")
        if subtitle:
            lines.extend([str(subtitle).strip(), ""])
        bullets = _card_bullets(card)
        for bullet_index, bullet in enumerate(bullets, start=1):
            prefix = "先看这一点" if bullet_index == 1 else "再补一层"
            if bullet_index == 3:
                prefix = "最后留个检查口"
            lines.append(f"{prefix}：{bullet}")
            lines.append("")
        if bullets:
            note = card.get("note")
            if note:
                lines.extend([f"> {note}", ""])

    lines.extend(
        [
            "## 我会给它留三条边界",
            "",
            "第一，Agent 只做研究辅助，不替我承担结果。",
            "",
            "第二，任何结论都必须回到证据链。没有来源、没有反证、没有风险提示的输出，默认不能用。",
            "",
            "第三，它要服务于复盘。判断对了要知道为什么对，判断错了也要知道错在哪里。",
            "",
            "## 今天先到这里",
            "",
            "这只是第一版。我会继续把它往真实可用的方向推进：少一点表演感，多一点每天能复用的动作。",
            "",
            "不构成投资建议。发布前我还会手动核对事实、时间、数据和具体标的。",
        ]
    )
    article = "\n".join(lines)
    article = re.sub(r"\n{3,}", "\n\n", article)
    return remove_ai_smell(article)
