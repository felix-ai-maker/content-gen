"""文案创意层：用 DeepSeek 等文本大模型生成卡片文案和图文正文。

设计原则（与 creative_director 一致）：
- 这是在规则模板之前加一层创意。任何失败（未启用 / 无 key / 网络 / 超时 /
  输出不合格）都返回 None 或回退规则模板，绝不阻断主流程。
- 复用 creative_director._post_chat（OpenAI 兼容接口，标准库 urllib，不引新依赖）。
- API key 只从环境变量读，绝不落盘。
- 让系统不再绑定「交易 Agent」单一题材：卡片和正文都由本次选题/素材驱动。
"""
from __future__ import annotations

import json
import os
import urllib.error
from pathlib import Path

from creative_director import _post_chat


# 爆款准则 / 招式库：外置到分类的 playbook.json，注入卡片/正文 prompt，让自动产出自带"被刷到"的设计。
# 每个类别带 enabled 开关，只注入"启用"的类别。在 Web「招式库」勾选/编辑或一键入库即可持续沉淀，
# 改动即时生效、无需改代码。
_PLAYBOOK_PATH = Path(__file__).resolve().parent / "playbook.json"
_PLAYBOOK_CACHE: dict = {}
# 内置兜底：playbook.json 缺失/为空/读坏时用这段，保证生成不退化。
_DEFAULT_PLAYBOOK = (
    "【爆款准则，务必遵守】"
    "1) 封面主标题套钩子公式（痛点型 / 好奇型 / 对比型 / 数字型其一），制造一个具体冲突或反差，"
    "让人忍不住点开；绝不用平铺直叙的陈述句、不用内部测试名或泛泛标题。"
    "2) 封面副标题 / 正文首段前 20 字要有钩子：要么戳中痛点，要么留悬念。"
    "3) 收尾（最后一页 note 或正文末尾）抛一个真诚、具体的问题，引导读者评论。"
    "4) 涉及交易 / 投资 / 理财时，弱化“量化、仓位、收益、止盈止损”等强监管词，"
    "改用“复盘、判断、自律、决策、系统、留痕”等更安全的表达以降低限流；"
    "不写收益承诺，保留风险与边界意识。"
)


def _playbook(config: dict | None = None) -> str:
    """从 playbook.json 拼出注入文本（按 mtime + 选中打法缓存）：取选中打法里启用类别的招式。
    config['playbook_id'] 指定用哪套打法；未指定用 active。缺失/为空/读坏回退内置准则。"""
    playbook_id = str((config or {}).get("playbook_id") or "").strip()
    try:
        mtime = _PLAYBOOK_PATH.stat().st_mtime
    except OSError:
        return _DEFAULT_PLAYBOOK
    cache_key = (mtime, playbook_id)
    if _PLAYBOOK_CACHE.get("key") != cache_key:
        _PLAYBOOK_CACHE["key"] = cache_key
        _PLAYBOOK_CACHE["text"] = _build_playbook_text(_PLAYBOOK_PATH, playbook_id)
    return _PLAYBOOK_CACHE.get("text") or _DEFAULT_PLAYBOOK


def _pick_playbook(data: dict, playbook_id: str) -> dict | None:
    playbooks = data.get("playbooks")
    if not isinstance(playbooks, list) or not playbooks:
        return None
    if playbook_id:
        for pb in playbooks:
            if isinstance(pb, dict) and pb.get("id") == playbook_id:
                return pb
    active = data.get("active")
    for pb in playbooks:
        if isinstance(pb, dict) and pb.get("id") == active:
            return pb
    return playbooks[0] if isinstance(playbooks[0], dict) else None


def _build_playbook_text(path: Path, playbook_id: str = "") -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return _DEFAULT_PLAYBOOK
    # 新结构 {active, playbooks:[{categories}]}；兼容旧结构 {categories}。
    pb = _pick_playbook(data, playbook_id)
    categories = pb.get("categories", []) if pb else data.get("categories", [])
    blocks: list[str] = []
    for cat in categories:
        if not isinstance(cat, dict) or not cat.get("enabled", True):
            continue
        tactics = [str(t).strip() for t in cat.get("tactics", []) if str(t).strip()]
        if not tactics:
            continue
        blocks.append(f"【{cat.get('name', '')}】\n" + "\n".join(f"- {t}" for t in tactics))
    if not blocks:
        return _DEFAULT_PLAYBOOK
    header = "【爆款准则 / 招式库，务必参考】"
    if pb and pb.get("name"):
        header = f"【本篇采用打法：{pb.get('name')}，务必参考】"
    return header + "\n" + "\n\n".join(blocks)


# --------------------------------------------------------------------------- #
# 配置解析：copy_llm 缺省时复用 creative_llm，省得用户配两份 key。
# --------------------------------------------------------------------------- #
def _resolve_copy_llm(config: dict) -> dict:
    cfg = config.get("copy_llm")
    if cfg is None:
        return config.get("creative_llm") or {}
    return cfg or {}


def _llm_ready(cfg: dict) -> bool:
    if not cfg or not cfg.get("enabled"):
        return False
    api_key = os.getenv(str(cfg.get("api_key_env", "DEEPSEEK_API_KEY")))
    if not api_key:
        print("[copy_llm] 未找到 API key 环境变量，回退规则文案模板。")
        return False
    return True


def _chat(cfg: dict, messages: list[dict], *, max_timeout: float | None = None) -> str:
    api_key = os.getenv(str(cfg.get("api_key_env", "DEEPSEEK_API_KEY")))
    payload = {
        "model": cfg.get("model", "deepseek-chat"),
        "messages": messages,
        "temperature": float(cfg.get("temperature", 0.8)),
        "stream": False,
    }
    timeout = float(cfg.get("timeout_seconds", 60))
    if max_timeout is not None:
        timeout = min(timeout, max_timeout)
    return _post_chat(
        base_url=str(cfg.get("base_url", "https://api.deepseek.com")),
        api_key=api_key,
        payload=payload,
        timeout=timeout,
    )


def _extract_json(content: str, opener: str, closer: str) -> object | None:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    start, end = text.find(opener), text.rfind(closer)
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


# --------------------------------------------------------------------------- #
# 卡片文案
# --------------------------------------------------------------------------- #
def _normalize_bullets(value: object) -> list[str]:
    if isinstance(value, str):
        value = [item.strip() for item in value.splitlines() if item.strip()]
    if not isinstance(value, list):
        return []
    bullets = [str(item).strip() for item in value if str(item).strip()]
    return bullets[:3]


def _parse_cards(content: str, brand: str) -> list[dict] | None:
    data = _extract_json(content, "[", "]")
    if not isinstance(data, list) or len(data) != 7:
        return None
    cards: list[dict] = []
    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            return None
        title = str(item.get("title", "")).strip()
        if not title:
            return None
        if index == 1:
            cards.append(
                {
                    "type": "cover",
                    "kicker": str(item.get("kicker", "") or f"{brand} 001").strip(),
                    "title": title,
                    "subtitle": str(item.get("subtitle", "")).strip(),
                    "accent": "RESEARCH / SIGNAL / REVIEW",
                }
            )
            continue
        bullets = _normalize_bullets(item.get("bullets"))
        if not bullets:
            return None
        card = {
            "type": "content",
            "kicker": str(item.get("kicker", "") or f"{index - 1:02d}").strip(),
            "title": title,
            "subtitle": str(item.get("subtitle", "")).strip(),
            "bullets": bullets,
            "note": str(item.get("note", "")).strip(),
        }
        highlight = str(item.get("highlight", "")).strip()
        if highlight:
            card["highlight"] = highlight
        cards.append(card)
    return cards


def generate_cards(topic: str, copy_text: str, config: dict, brand: str) -> list[dict] | None:
    """成功返回 7 张卡片文案；任何失败返回 None（调用方回退规则模板）。"""
    cfg = _resolve_copy_llm(config)
    if not _llm_ready(cfg):
        return None

    system = (
        "你是一个资深的小红书 / 公众号内容主编，擅长把一个选题或一段素材拆成一套 7 张图文卡。"
        "你写第一人称、克制、具体的中文，不写空泛鸡汤，不用营销腔，不夸张承诺，不给投资建议。"
        "你严格围绕用户给的这次选题来写，绝不套用与本选题无关的固定模板或案例。"
    )
    anchor = str((config or {}).get("anchor") or "").strip()
    anchor_line = (
        f"必须紧扣并贯穿这些核心关键词/元素（不得偏离到无关方向）：{anchor}\n" if anchor else ""
    )
    user = (
        f"本次选题：{topic.strip() or '未填写'}\n"
        + anchor_line
        + f"原始素材 / 关键词（可能为空）：\n{copy_text.strip() or '（无，请只依据选题发挥）'}\n\n"
        "请产出一套 7 张图文卡：第 1 张是封面，后 6 张是内容页，逻辑上层层递进（通常是"
        "问题→为什么→怎么做→边界/风险→收束）。严格只输出一个 JSON 数组，共 7 个元素：\n"
        "[\n"
        '  {"type":"cover","title":"封面主标题(<=22字,能独立成立、有具体冲突或对象)","subtitle":"一句副标题(<=34字)"},\n'
        '  {"type":"content","kicker":"01 / 关键词(英文小写或中文短词)","title":"本页标题(<=18字)",'
        '"subtitle":"一句解释(<=30字)","bullets":["要点1(<=28字)","要点2","要点3"],'
        '"highlight":"本页一句核心判断(可空)","note":"底部收束句(<=22字)"}\n'
        "  ... 内容页共 6 个\n"
        "]\n"
        "要求：每张内容页 bullets 给 2-3 条；标题彼此不要重复；语言具体、有个人表达。\n\n"
        + _playbook(config)
        + "\n\n不要输出 JSON 以外的任何内容。"
    )
    try:
        content = _chat(cfg, [{"role": "system", "content": system}, {"role": "user", "content": user}])
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as exc:
        print(f"[copy_llm] 卡片生成调用失败，回退规则模板：{exc}")
        return None

    cards = _parse_cards(content, brand)
    if cards is None:
        print("[copy_llm] 卡片输出不合格（非预期 JSON），回退规则模板。")
        return None
    print("[copy_llm] DeepSeek 文案层成功生成 7 张卡片文案。")
    return cards


# --------------------------------------------------------------------------- #
# 图文正文
# --------------------------------------------------------------------------- #
def _cards_context(cards: list[dict]) -> str:
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


def _generate_body_via_llm(topic: str, cards: list[dict], config: dict, copy_text: str) -> dict | None:
    cfg = _resolve_copy_llm(config)
    if not _llm_ready(cfg):
        return None

    brand = config.get("brand", {}).get("name", "")
    hashtags = config.get("content", {}).get("hashtags") or []
    hashtag_line = " ".join(str(tag) for tag in hashtags)

    system = (
        "你是一个资深的小红书 / 公众号内容主编。你写第一人称、克制、具体、少口号的中文，"
        "不用营销腔（不说赋能、降本增效、深度解析、干货满满、建议收藏这类词），不夸张承诺，不给投资建议。"
        "你严格根据用户给的这套卡片内容来写正文，绝不引入与本选题无关的固定叙事。"
    )
    anchor = str((config or {}).get("anchor") or "").strip()
    user = (
        f"本次选题：{topic.strip()}\n"
        + (f"必须紧扣并贯穿这些核心关键词/元素：{anchor}\n" if anchor else "")
        + (f"品牌署名：{brand}\n" if brand else "")
        + "\n这套图文卡的逐页内容如下，请据此写两份正文（不要照抄卡片，要展开成自然的口语化表达）：\n"
        + _cards_context(cards)
        + "\n\n请严格只输出一个 JSON 对象：\n"
        '{"xhs":"小红书正文(Markdown，首行用 # 写一个标题，正文分自然段，第一人称、具体、有钩子，'
        '结尾另起一行附上话题标签)","wechat":"公众号正文(Markdown，# 标题 + 一行作者署名引用 + 分小节 '
        '## 标题，逻辑完整、比小红书更细)"}\n'
        + (f"小红书结尾请附上这些话题标签：{hashtag_line}\n" if hashtag_line else "")
        + "小红书话题标签：除上述核心标签外，可再补 2-3 个更安全的场景词 / 人群词（如 #自律 #个人成长 #效率工具），"
        "避免只堆强金融词导致限流。\n"
        + "如果选题涉及交易/投资/收益，两份正文都要包含一句「不构成投资建议」，并保留风险/边界意识，不写收益承诺。\n\n"
        + _playbook(config)
        + "\n\n不要输出 JSON 以外的任何内容。"
    )
    try:
        content = _chat(cfg, [{"role": "system", "content": system}, {"role": "user", "content": user}])
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as exc:
        print(f"[copy_llm] 正文生成调用失败，回退规则模板：{exc}")
        return None

    data = _extract_json(content, "{", "}")
    if not isinstance(data, dict):
        print("[copy_llm] 正文输出不合格（非预期 JSON），回退规则模板。")
        return None
    xhs = str(data.get("xhs", "")).strip()
    wechat = str(data.get("wechat", "")).strip()
    if len(xhs) < 80 or len(wechat) < 120:
        print("[copy_llm] 正文过短，回退规则模板。")
        return None
    return {"xhs": xhs, "wechat": wechat}


def compose_body(
    topic: str, cards: list[dict], config: dict, cards_source: str, copy_text: str = ""
) -> tuple[str, str]:
    """返回 (小红书正文, 公众号正文)。LLM 成功用 LLM，否则回退规则模板。"""
    from content_writer import build_wechat_article, build_xhs_post
    from copy_pipeline import remove_ai_smell

    body = _generate_body_via_llm(topic, cards, config, copy_text)
    if body is not None:
        print("[copy_llm] DeepSeek 文案层成功生成图文正文。")
        return remove_ai_smell(body["xhs"]), remove_ai_smell(body["wechat"])

    xhs_post = build_xhs_post(topic=topic, cards=cards, config=config, cards_source=cards_source, copy_text=copy_text)
    wechat_article = build_wechat_article(
        topic=topic, cards=cards, config=config, cards_source=cards_source, copy_text=copy_text
    )
    return xhs_post, wechat_article
