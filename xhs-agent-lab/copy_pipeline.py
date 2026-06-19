from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

from style_director import choose_style_profile


AI_SMELL_PHRASES = [
    "在当今",
    "随着时代的发展",
    "赋能",
    "降本增效",
    "打造闭环",
    "全方位",
    "多维度",
    "深度解析",
    "不难发现",
    "值得注意的是",
    "总的来说",
    "综上所述",
    "今天这组图",
    "干货满满",
    "建议收藏",
]

CARD_BLUEPRINTS = [
    {
        "kicker": "01 / PROBLEM",
        "title": "真正卡住人的，往往不是工具",
        "subtitle": "而是每天的判断过程太松散。",
        "keywords": ["问题", "噪音", "混乱", "很难", "缺失", "消耗", "卡住", "焦虑", "信息"],
        "fallback": [
            "信息看了很多，但最后很难变成稳定判断。",
            "临场情绪会把小波动放大，复盘时却找不到证据。",
            "如果过程不可追踪，结果再好也很难复制。",
        ],
        "note": "先把问题说准，后面才不会乱做。",
    },
    {
        "kicker": "02 / SYSTEM",
        "title": "我更想固定一条研究流程",
        "subtitle": "让重复劳动交给系统，让判断留给人。",
        "keywords": ["流程", "系统", "自动", "收集", "整理", "结构", "研究", "数据", "清单"],
        "fallback": [
            "先把资料、假设、证据和反证放到同一张桌面上。",
            "每次输出都要能回到来源，而不是只留下一个结论。",
            "系统越稳定，人越不容易被当天情绪牵着走。",
        ],
        "note": "自动化的第一步，是把流程写清楚。",
    },
    {
        "kicker": "03 / BOUNDARY",
        "title": "Agent 不能替我负责",
        "subtitle": "它可以提醒，但不应该替代判断。",
        "keywords": ["边界", "风险", "不能", "不是", "负责", "判断", "仓位", "错误", "证据"],
        "fallback": [
            "它可以给线索，但不能替代仓位纪律。",
            "它可以生成结论，但必须附带证据链。",
            "它可以提醒风险，但最后的选择要留在人这里。",
        ],
        "note": "边界越清楚，工具越好用。",
    },
    {
        "kicker": "04 / LOOP",
        "title": "每天留下可复盘的痕迹",
        "subtitle": "不只记录结果，也记录当时为什么这么想。",
        "keywords": ["复盘", "记录", "闭环", "留下", "每天", "输入", "输出", "回看", "日志"],
        "fallback": [
            "输入是什么，假设是什么，反证是什么，都要留档。",
            "不是只看赚没赚钱，而是看当时的判断有没有失真。",
            "复盘不是写总结，是给下一次决策留证据。",
        ],
        "note": "能回看的系统，才会慢慢变稳。",
    },
    {
        "kicker": "05 / TASTE",
        "title": "好系统应该让人更冷静",
        "subtitle": "它不制造刺激感，只减少混乱感。",
        "keywords": ["冷静", "克制", "清醒", "纪律", "冲动", "节奏", "耐心", "稳定", "少一点"],
        "fallback": [
            "少一点追热点，多一点结构化观察。",
            "少一点凭感觉，多一点证据和反证。",
            "少一点临场发挥，多一点可复盘的纪律。",
        ],
        "note": "克制不是风格，是风险管理的一部分。",
    },
    {
        "kicker": "06 / NEXT",
        "title": "先跑起来，再慢慢变聪明",
        "subtitle": "不要一开始就追求一个巨大的系统。",
        "keywords": ["接下来", "先", "再", "最后", "开始", "版本", "迭代", "做成", "每天"],
        "fallback": [
            "先让选题、素材整理和卡片生成稳定工作。",
            "再接入行情、公告、财报和自定义检查清单。",
            "最后把每天的判断沉淀成自己的研究资产。",
        ],
        "note": "真正有用的自动化，通常是长出来的。",
    },
]


PROFILE_CARD_BLUEPRINTS = {
    "product_ecosystem": [
        {
            "kicker": "01 / CORE",
            "title": "核心不是功能，是入口变了",
            "subtitle": "真正值得看的是服务会怎样被分发。",
            "keywords": ["核心", "重点", "入口", "调用", "接入", "推荐", "分发", "生态"],
            "fallback": [
                "重点不是多接一个功能，而是服务有机会被新的入口发现。",
                "当平台开始理解需求，小程序可能从被搜索变成被推荐。",
                "入口位置变化，往往会改变一整套获客方式。",
            ],
            "note": "先看入口，再看功能。",
        },
        {
            "kicker": "02 / RULE",
            "title": "最关键的一句话",
            "subtitle": "有没有接入，决定能不能被调用。",
            "keywords": ["未完成", "无法", "不能", "必须", "接入", "调用", "能力"],
            "fallback": [
                "未完成接入的服务，未来可能无法进入新的调用链路。",
                "这不是一个按钮变化，而是服务能不能被平台理解的问题。",
                "如果入口变了，服务也要重新适配新的分发方式。",
            ],
            "note": "新入口会重新定义谁能被看见。",
        },
        {
            "kicker": "03 / OLD",
            "title": "过去的入口靠用户主动找",
            "subtitle": "搜索、扫码、公众号、社群和朋友圈，都是用户先动。",
            "keywords": ["过去", "搜索", "扫码", "公众号", "社群", "朋友圈", "用户"],
            "fallback": [
                "过去用户找服务，通常要先知道自己要找什么。",
                "搜索、扫码和公众号，本质上都依赖用户主动触发。",
                "服务能不能被找到，很大程度取决于入口运营。",
            ],
            "note": "旧入口解决的是“用户主动找”。",
        },
        {
            "kicker": "04 / NEW",
            "title": "未来可能是 AI 主动分发",
            "subtitle": "用户说出需求，平台替他匹配合适服务。",
            "keywords": ["未来", "AI", "推荐", "主动", "需求", "分发", "匹配", "调用"],
            "fallback": [
                "如果 AI 能理解需求，它就可能直接推荐可调用的服务。",
                "用户不一定先打开小程序，而是先表达自己要解决的问题。",
                "平台从入口管理，变成需求理解和服务匹配。",
            ],
            "note": "新入口解决的是“需求被理解”。",
        },
        {
            "kicker": "05 / PATH",
            "title": "可能出现的新路径",
            "subtitle": "从用户找服务，变成服务被 AI 调用。",
            "keywords": ["路径", "用户", "需求", "理解", "推荐", "调用", "服务"],
            "fallback": [
                "用户说出需求，AI 理解意图，再推荐合适的小程序。",
                "小程序不只是被打开，而是可能被放进真实任务流程。",
                "服务入口会从页面跳转，变成任务链路中的一环。",
            ],
            "note": "变化不在按钮，在分发逻辑。",
        },
        {
            "kicker": "06 / SUMMARY",
            "title": "一句话总结",
            "subtitle": "重点不是陪用户聊天，而是帮用户调用服务。",
            "keywords": ["总结", "重点", "不是", "而是", "服务", "调用"],
            "fallback": [
                "真正的变化，可能不是多一个聊天入口，而是多一条服务分发链路。",
                "小程序要面对的不是新功能，而是新的被发现方式。",
                "谁能被 AI 理解和调用，谁就可能进入新入口。",
            ],
            "note": "入口变了，服务的表达方式也要变。",
        },
    ],
    "business_balance": [
        {
            "kicker": "01 / FINDING",
            "title": "增长不是只有收入",
            "subtitle": "收入上来时，成本结构也会一起变化。",
            "keywords": ["收入", "增长", "同比", "企业服务", "需求"],
            "fallback": [
                "收入增长值得看，但更关键的是增长背后用了什么成本。",
                "企业服务收入上升，往往也会带来交付、设备和运营压力。",
                "增长不是单向好消息，它也会改变利润结构。",
            ],
            "note": "先看收入，再看代价。",
        },
        {
            "kicker": "02 / COST",
            "title": "成本为什么也上来了",
            "subtitle": "设备、折旧、运营和推广都会吃掉利润。",
            "keywords": ["成本", "折旧", "运营", "费用", "推广", "营销"],
            "fallback": [
                "AI 相关业务通常需要新的设备投入和运行成本。",
                "推广费用上升，说明增长可能还需要持续投入。",
                "如果成本涨得太快，收入增长未必马上变成利润。",
            ],
            "note": "增长质量，要看成本怎么走。",
        },
        {
            "kicker": "03 / MARGIN",
            "title": "毛利率才是第二层答案",
            "subtitle": "收入和成本同时发生，毛利率会告诉你压力在哪。",
            "keywords": ["毛利", "毛利率", "利润", "56", "55", "下降"],
            "fallback": [
                "如果收入增长但毛利率下滑，说明成本压力正在显现。",
                "毛利率不是小数点变化，它反映业务模式的真实负担。",
                "新业务越重，越要看它能不能守住利润率。",
            ],
            "note": "别只看增长，也要看效率。",
        },
        {
            "kicker": "04 / REASON",
            "title": "原因通常藏在投入里",
            "subtitle": "AI 带来的不是单纯收入，而是一套新能力成本。",
            "keywords": ["原因", "AI", "设备", "原生应用", "投入", "折旧"],
            "fallback": [
                "AI 原生应用需要算力、设备、研发和运营投入。",
                "这些投入会先进入成本，再慢慢验证商业回报。",
                "短期看是费用，长期看要看能不能形成复用能力。",
            ],
            "note": "投入能否复用，决定后面的利润空间。",
        },
        {
            "kicker": "05 / BALANCE",
            "title": "关键是收入和利润能不能同向",
            "subtitle": "只增长收入不够，利润效率也要跟上。",
            "keywords": ["收入", "利润", "促进", "同时", "发生", "效率"],
            "fallback": [
                "收入促进和利润投入，正在同时发生。",
                "如果投入能换来长期能力，短期成本就有解释空间。",
                "如果投入不能沉淀能力，增长就会变得很贵。",
            ],
            "note": "天平两边都要看。",
        },
        {
            "kicker": "06 / SUMMARY",
            "title": "一句话总结",
            "subtitle": "AI 带来增长，也带来成本。",
            "keywords": ["总结", "一句话", "增长", "成本", "利润"],
            "fallback": [
                "AI 不是只带来收入，它也会重塑成本结构。",
                "真正要看的，是收入增长能不能覆盖能力建设的代价。",
                "增长能不能变成利润，才是后面要继续跟踪的重点。",
            ],
            "note": "增长和代价，要放在同一张表里。",
        },
    ],
}


@dataclass
class QualityReport:
    score: int
    warnings: list[str]
    details: list[str]

    def to_markdown(self) -> str:
        lines = [
            "## 质量检查",
            "",
            f"- 低 AI 味评分：{self.score}/100",
        ]
        if self.warnings:
            lines.append("- 发布前建议处理：")
            for warning in self.warnings:
                lines.append(f"  - {warning}")
        else:
            lines.append("- 发布前建议处理：暂无明显问题。")
        if self.details:
            lines.append("- 检查记录：")
            for detail in self.details:
                lines.append(f"  - {detail}")
        return "\n".join(lines)


def read_copy_input(copy_text: str | None, copy_file: str | None) -> tuple[str, str]:
    parts: list[str] = []
    source = "未提供"

    if copy_file:
        path = Path(copy_file).expanduser().resolve()
        parts.append(path.read_text(encoding="utf-8"))
        source = path.name

    if copy_text:
        parts.append(copy_text)
        source = "命令行 --copy" if source == "未提供" else f"{source} + --copy"

    return "\n\n".join(part.strip() for part in parts if part.strip()), source


def build_cards_from_copy(topic: str, copy_text: str, config: dict) -> list[dict]:
    brand = config.get("brand", {}).get("name", "交易 Agent 实验室")
    sentences = extract_sentences(copy_text)
    used: set[str] = set()
    profile_key = choose_style_profile("\n".join([topic, copy_text]))
    blueprints = PROFILE_CARD_BLUEPRINTS.get(profile_key, CARD_BLUEPRINTS)

    cards = [
        {
            "type": "cover",
            "kicker": f"{brand} 001",
            "title": make_cover_title(topic),
            "subtitle": make_cover_subtitle(topic, sentences),
            "accent": "RESEARCH / SIGNAL / REVIEW",
        }
    ]

    for blueprint in blueprints:
        bullets = pick_bullets(sentences, blueprint["keywords"], used, blueprint["fallback"])
        used.update(bullets)
        cards.append(
            {
                "type": "content",
                "kicker": blueprint["kicker"],
                "title": refine_title(blueprint["title"], bullets),
                "subtitle": blueprint["subtitle"],
                "bullets": bullets,
                "note": blueprint["note"],
            }
        )

    return cards


def extract_sentences(copy_text: str) -> list[str]:
    normalized = re.sub(r"\r\n?", "\n", copy_text)
    normalized = re.sub(r"[ \t]+", " ", normalized)
    raw_parts = re.split(r"(?<=[。！？!?；;])\s*|\n+", normalized)

    sentences: list[str] = []
    for part in raw_parts:
        cleaned = clean_sentence(part)
        if 8 <= len(cleaned) <= 72 and cleaned not in sentences:
            sentences.append(cleaned)
    return sorted(sentences, key=sentence_score, reverse=True)


def clean_sentence(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^[\-*•\d.、\s]+", "", text)
    text = re.sub(r"\s+", " ", text)
    text = remove_ai_smell(text)
    return text.strip(" ，,")


def remove_ai_smell(text: str) -> str:
    result = text
    for phrase in AI_SMELL_PHRASES:
        result = result.replace(phrase, "")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in result.splitlines()]
    return "\n".join(lines).strip()


def sentence_score(sentence: str) -> float:
    score = 0.0
    length = len(sentence)
    score += 18 - abs(length - 32) * 0.35
    if re.search(r"\d|我|自己|每天|后来|当时|这次|复盘|证据|风险|输入|输出", sentence):
        score += 6
    if re.search(r"因为|但是|而是|所以|如果|不是|不能", sentence):
        score += 4
    if any(phrase in sentence for phrase in AI_SMELL_PHRASES):
        score -= 12
    if sentence.endswith(("。", "！", "？")):
        score += 1
    return score


def pick_bullets(sentences: list[str], keywords: list[str], used: set[str], fallback: list[str]) -> list[str]:
    scored: list[tuple[float, str]] = []
    for sentence in sentences:
        if sentence in used:
            continue
        hit = sum(1 for keyword in keywords if keyword in sentence)
        if hit:
            scored.append((hit * 10 + sentence_score(sentence), sentence))

    selected = [sentence for _, sentence in sorted(scored, reverse=True)[:3]]
    for item in fallback:
        if len(selected) >= 3:
            break
        if item not in selected:
            selected.append(item)
    return [polish_bullet(item) for item in selected[:3]]


def polish_bullet(text: str) -> str:
    text = remove_ai_smell(text)
    text = text.strip()
    if len(text) > 58:
        text = text[:56].rstrip("，,、；;") + "。"
    if not text.endswith(("。", "？", "！")):
        text += "。"
    return text


def make_cover_title(topic: str) -> str:
    title = topic.strip()
    title = re.sub(r"[。！？!?.]+$", "", title)
    if len(title) <= 24:
        return title
    return title[:23].rstrip("，,、：:") + "？"


def make_cover_subtitle(topic: str, sentences: list[str]) -> str:
    if sentences:
        first = sentences[0].rstrip("。！？")
        if len(first) <= 34:
            return first + "。"
    if "Agent" in topic or "agent" in topic:
        return "不是把判断外包给机器，而是把过程留在纸面上。"
    return "先把一个真实问题说清楚，再让系统帮你重复执行。"


def refine_title(default_title: str, bullets: list[str]) -> str:
    if not bullets:
        return default_title
    first = bullets[0].rstrip("。！？")
    if "不是" in first and len(first) <= 24:
        return first
    return default_title


def run_quality_checks(topic: str, cards: list[dict], xhs_post: str, wechat_article: str, config: dict) -> QualityReport:
    warnings: list[str] = []
    details: list[str] = []
    combined = "\n".join([topic, xhs_post, wechat_article, cards_to_text(cards)])
    score = 100

    hits = [phrase for phrase in AI_SMELL_PHRASES if phrase in combined]
    if hits:
        score -= min(28, len(hits) * 4)
        warnings.append("发现偏 AI/营销腔的词：" + "、".join(hits[:8]))
    else:
        details.append("未发现常见 AI 腔口头禅。")

    avg_sentence_len = average_sentence_length(combined)
    if avg_sentence_len > 46:
        score -= 10
        warnings.append(f"平均句长约 {avg_sentence_len:.0f} 字，建议拆短。")
    else:
        details.append(f"平均句长约 {avg_sentence_len:.0f} 字，可读性正常。")

    if len(cards) != 7:
        score -= 15
        warnings.append(f"当前卡片数为 {len(cards)}，小红书组图建议固定 7 张。")
    else:
        details.append("卡片数为 7，符合发布包约定。")

    short_cards = [
        str(index + 1)
        for index, card in enumerate(cards)
        if sum(len(str(item)) for item in card.get("bullets", [])) < 24 and card.get("type") != "cover"
    ]
    if short_cards:
        score -= 8
        warnings.append("这些卡片正文偏薄：" + "、".join(short_cards))
    else:
        details.append("内容页信息量没有明显过薄。")

    required_brand = config.get("brand", {}).get("name", "交易 Agent 实验室")
    if required_brand not in combined:
        score -= 8
        warnings.append(f"正文里没有出现品牌名「{required_brand}」。")

    return QualityReport(score=max(0, min(100, score)), warnings=warnings, details=details)


def average_sentence_length(text: str) -> float:
    parts = [part.strip() for part in re.split(r"[。！？!?；;\n]+", text) if part.strip()]
    if not parts:
        return 0.0
    return sum(len(part) for part in parts) / len(parts)


def cards_to_text(cards: list[dict]) -> str:
    chunks: list[str] = []
    for card in cards:
        chunks.append(str(card.get("title", "")))
        chunks.append(str(card.get("subtitle", "")))
        bullets = card.get("bullets", [])
        if isinstance(bullets, list):
            chunks.extend(str(item) for item in bullets)
    return "\n".join(chunks)


def build_publish_checklist(
    topic: str,
    cards_source: str,
    copy_source: str,
    rendered_count: int,
    quality: QualityReport,
) -> str:
    lines = [
        f"# 发布清单：{topic}",
        "",
        "## 文件",
        "",
        "- 小红书正文：`小红书正文.md`",
        "- 公众号文章：`公众号文章.md`",
        f"- 图文卡片：`card_01.png` 到 `card_{rendered_count:02d}.png`",
        "- 卡片结构：`cards_used.json`",
        "",
        "## 来源",
        "",
        f"- 卡片来源：`{cards_source}`",
        f"- 文案素材：`{copy_source}`",
        "",
        quality.to_markdown(),
        "",
        "## 发布前人工确认",
        "",
        "- 核对事实、数字、股票代码、人名和时间。",
        "- 小红书封面只保留一个强标题，不加廉价财经图形。",
        "- 公众号开头保留一个具体场景，删掉空泛口号。",
        "- 交易相关内容默认补充风险提示，不写收益承诺。",
    ]
    return "\n".join(lines)


def estimate_reading_minutes(text: str) -> int:
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    ascii_words = len(re.findall(r"[A-Za-z0-9_]+", text))
    return max(1, math.ceil((chinese_chars + ascii_words * 0.6) / 420))
