from __future__ import annotations

import copy
import re


STYLE_PROFILES = {
    "decision_trace": {
        "name": "判断留痕",
        "theme": "把一次交易判断还原成可复盘的证据链",
        "visual_language": "编辑型交易复盘、纸质证据、个人研究桌、少量轻科技界面、可追溯的判断痕迹",
        "illustration_style": "高级移动端知识卡、杂志式信息图、纸张与半透明界面混合、克制科技蓝点缀",
        "accent_color": "#1677FF",
        "support_color": "#EAF3FF",
        "avoid": "K线、红绿涨跌、交易软件大屏、科幻玻璃柱、机器人、AI芯片图标、廉价霓虹、赛博夜店感、通用蓝色科技背景",
    },
    "business_balance": {
        "name": "增长与代价",
        "theme": "把收入、成本、利润和投入之间的关系讲清楚",
        "visual_language": "商业专栏解释图、天平、账本、成本杠杆、增长标记",
        "illustration_style": "纸张质感财经插画、干净线条、克制点缀物件",
        "accent_color": "#A97822",
        "support_color": "#F5E8CF",
        "avoid": "廉价金色财富感、股票K线、红绿涨跌视觉",
    },
    "product_ecosystem": {
        "name": "入口迁移",
        "theme": "把产品入口、服务分发和调用路径讲清楚",
        "visual_language": "干净的产品解释图、服务卡片、路由面板、入口地图",
        "illustration_style": "柔和的产品界面信息图、圆角模块、无真实品牌标志的服务图标",
        "accent_color": "#18A957",
        "support_color": "#E8F7EF",
        "avoid": "照抄真实应用界面、官方Logo、伪造可读界面文字",
    },
    "automation_workflow": {
        "name": "自动化工作流",
        "theme": "把输入、处理、判断、输出的自动化链路讲清楚",
        "visual_language": "工作流地图、模块卡片、输入输出链路、控制台",
        "illustration_style": "结构化系统信息图、干净模块、箭头、检查点、轻微空间感",
        "accent_color": "#2563EB",
        "support_color": "#EAF1FF",
        "avoid": "嘈杂仪表盘、密集终端屏幕、赛博霓虹",
    },
    "risk_boundary": {
        "name": "风险边界",
        "theme": "把红线、责任、风险和不能做的事讲清楚",
        "visual_language": "风险备忘录、边界门、警示面板、责任分隔线、安全清单",
        "illustration_style": "严肃的编辑型规则卡、高对比规则、克制警示",
        "accent_color": "#F97316",
        "support_color": "#FFF1E7",
        "avoid": "灾难化恐吓图、红色股灾视觉、夸张恐惧营销",
    },
    "knowledge_note": {
        "name": "知识笔记",
        "theme": "把一个抽象观点拆成易理解的知识卡片",
        "visual_language": "干净知识解释图、笔记卡片、简单图解、概念物件",
        "illustration_style": "编辑型知识卡插画、清楚层级、克制物件",
        "accent_color": "#1F6FFF",
        "support_color": "#E8F0FF",
        "avoid": "通用抽象背景、无意义装饰物",
    },
}


DOMAIN_KEYWORDS = {
    "decision_trace": [
        "交易",
        "判断",
        "复盘",
        "证据",
        "反证",
        "亏损",
        "仓位",
        "止损",
        "买",
        "卖",
        "冲动",
        "Agent",
    ],
    "business_balance": [
        "收入",
        "成本",
        "利润",
        "毛利",
        "增长",
        "费用",
        "折旧",
        "运营",
        "营销",
        "投入",
        "企业服务",
    ],
    "product_ecosystem": [
        "微信",
        "小程序",
        "入口",
        "调用",
        "接入",
        "平台",
        "生态",
        "服务",
        "推荐",
        "开发者",
    ],
    "automation_workflow": [
        "自动化",
        "流程",
        "工作流",
        "系统",
        "API",
        "模型",
        "输入",
        "输出",
        "生成",
        "工具",
    ],
    "risk_boundary": [
        "风险",
        "边界",
        "不能",
        "必须",
        "责任",
        "红线",
        "规则",
        "警告",
        "危险",
        "失败",
    ],
}


# 风格模板兜底：config 里没配 style_presets，或某套缺字段时使用。
# 等价于改造前 direct_card_renderer 写死的「黑白灰 + 科技蓝」风格。
DEFAULT_STYLE_PRESET = {
    "key": "signature_mono",
    "name": "极简科技蓝",
    "palette": {
        "paper": "#F7F7F5",
        "ink": "#111111",
        "accent": "#1F6FFF",
        "support": "#666B73",
    },
    "typography": "克制的字体层级，标题有冲击但不过大，正文不像口号。",
    "art_direction": (
        "黑、白、灰为主，科技蓝只做点睛。不要满屏蓝线、不要大面积霓虹、不要赛博感。"
        "允许纸张、档案、桌面、透明界面、工具、门、天平、证据线、留痕轨迹等元素，但必须服务于本页观点。"
    ),
}


def _as_keyword_list(value: object) -> list[str]:
    """容忍 match_keywords 是 list 或（fallback YAML parser 下的）flow-list 字符串。"""
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        inner = value.strip().strip("[]")
        parts = [part.strip().strip("\"'") for part in inner.split(",")]
        return [part for part in parts if part]
    return []


def resolve_style_preset(
    topic: str,
    copy_text: str,
    config: dict,
    override: str | None = None,
) -> dict:
    """选出本次使用的视觉风格模板。

    override（来自 --style）优先；否则按 match_keywords 在选题/正文里计分自动选；
    无命中时回退 image_model.default_style，再回退到第一套或内置默认。
    """
    presets = config.get("style_presets") or {}
    default_key = str((config.get("image_model") or {}).get("default_style") or "").strip()

    def build(key: str) -> dict:
        data = presets.get(key) or {}
        merged = {**DEFAULT_STYLE_PRESET, **data}
        merged["key"] = key
        palette = {**DEFAULT_STYLE_PRESET["palette"], **(data.get("palette") or {})}
        merged["palette"] = palette
        return merged

    if override:
        if override not in presets:
            available = ", ".join(presets) or "（config 未配置 style_presets）"
            raise ValueError(f"未知的 --style 模板：{override}。可选：{available}")
        return build(override)

    if not presets:
        return dict(DEFAULT_STYLE_PRESET, palette=dict(DEFAULT_STYLE_PRESET["palette"]))

    text = "\n".join([topic or "", copy_text or ""])
    best_key, best_score = None, 0
    for key, data in presets.items():
        keywords = _as_keyword_list((data or {}).get("match_keywords"))
        score = sum(text.count(keyword) for keyword in keywords)
        if score > best_score:
            best_key, best_score = key, score

    if best_key is None:
        best_key = default_key if default_key in presets else next(iter(presets))
    return build(best_key)


LAYOUT_RULES = [
    ("warning_rule", ["不能", "必须", "关键", "红线", "危险", "警告", "不许", "默认"]),
    ("comparison", ["两种", "对比", "vs", "VS", "模式"]),
    ("process_flow", ["流程", "路径", "步骤", "先", "再", "最后", "闭环", "链路"]),
    ("before_after", ["过去", "未来", "变化", "以前", "现在", "开始", "变成"]),
    ("core_info", ["核心", "信息", "重点", "一句话", "总结"]),
    ("checklist", ["规则", "清单", "条件", "边界", "确认"]),
]


def apply_style_plan(
    cards: list[dict],
    topic: str,
    copy_text: str,
    config: dict,
    style_override: str | None = None,
) -> tuple[list[dict], dict]:
    styled_cards = copy.deepcopy(cards)
    combined = "\n".join([topic, copy_text, *_card_texts(styled_cards)])
    profile_key, scores = _choose_profile(combined)
    profile = STYLE_PROFILES[profile_key]

    # 未手动指定风格时，先让文本大模型按选题情绪/题材智能匹配；失败回退关键词匹配。
    if not style_override:
        try:
            from creative_director import choose_style_preset

            llm_key = choose_style_preset(
                topic, copy_text, styled_cards, config.get("style_presets") or {}, config.get("creative_llm")
            )
            if llm_key:
                style_override = llm_key
        except Exception as exc:  # noqa: BLE001 - 风格匹配永不阻断主流程
            print(f"[creative_llm] 跳过风格匹配，回退关键词：{exc}")

    preset = resolve_style_preset(topic, copy_text, config, override=style_override)

    # 文本创意层（可选）：用 DeepSeek 为每页创意生成视觉 brief；任何失败返回 None，
    # 下面循环就退回规则隐喻库 _choose_metaphor，绝不阻断出图。
    briefs_by_index: dict[int, dict] = {}
    try:
        from creative_director import generate_visual_briefs

        fallback_examples = [opts[0] for _, opts in METAPHOR_GROUPS]
        briefs = generate_visual_briefs(
            topic=topic,
            copy_text=copy_text,
            cards=styled_cards,
            profile=profile,
            preset=preset,
            llm_cfg=config.get("creative_llm"),
            fallback_examples=fallback_examples,
        )
        if briefs:
            briefs_by_index = {int(b.get("index", i + 1)): b for i, b in enumerate(briefs)}
    except Exception as exc:  # noqa: BLE001 - 创意层永不阻断主流程
        print(f"[creative_llm] 跳过创意层，回退规则隐喻库：{exc}")

    plan = {
        "profile": profile_key,
        "name": profile["name"],
        "theme": profile["theme"],
        "visual_language": profile["visual_language"],
        "illustration_style": profile["illustration_style"],
        "accent_color": profile["accent_color"],
        "support_color": profile["support_color"],
        "domain_scores": scores,
        "style_preset": preset,
        "cards": [],
    }

    for index, card in enumerate(styled_cards, start=1):
        layout = "cover_impact" if index == 1 or card.get("type") == "cover" else _choose_layout(card, index)
        visual_role = _visual_role(card, index)
        brief = briefs_by_index.get(index)
        # 创意层成功则用其隐喻，否则回退规则库。
        metaphor = (brief or {}).get("metaphor") or _choose_metaphor(card, profile_key, layout, index)
        card_style = {
            "theme": profile["theme"],
            "profile": profile_key,
            "layout": layout,
            "visual_role": visual_role,
            "metaphor": metaphor,
            "visual_language": profile["visual_language"],
            "illustration_style": profile["illustration_style"],
            "accent_color": profile["accent_color"],
            "support_color": profile["support_color"],
            "style_preset": preset["key"],
            "palette": preset["palette"],
            "art_direction": preset["art_direction"],
            "prompt": _build_card_prompt(card, profile, layout, metaphor, visual_role, preset),
        }
        if brief:
            card_style["creative_source"] = "deepseek"
            card_style["composition"] = brief.get("composition", "")
            card_style["details"] = brief.get("details", "")
            card_style["accent_focus"] = brief.get("accent_focus", "")
        if not isinstance(card.get("visual_style"), dict):
            card["visual_style"] = {}
        card["visual_style"].update(card_style)
        if index == 1:
            _enrich_cover(card, profile)
        plan["cards"].append(
            {
                "index": index,
                "title": str(card.get("title", "")),
                "layout": layout,
                "visual_role": visual_role,
                "metaphor": metaphor,
            }
        )

    return styled_cards, plan


def choose_style_profile(text: str) -> str:
    profile_key, _ = _choose_profile(text)
    return profile_key


def _card_texts(cards: list[dict]) -> list[str]:
    chunks: list[str] = []
    for card in cards:
        chunks.append(str(card.get("kicker", "")))
        chunks.append(str(card.get("title", "")))
        chunks.append(str(card.get("subtitle", "")))
        chunks.append(str(card.get("highlight", "")))
        bullets = card.get("bullets", [])
        if isinstance(bullets, list):
            chunks.extend(str(item) for item in bullets)
        else:
            chunks.append(str(bullets))
        chunks.append(str(card.get("note", "")))
    return chunks


def _choose_profile(text: str) -> tuple[str, dict]:
    scores = {
        name: sum(text.count(keyword) for keyword in keywords)
        for name, keywords in DOMAIN_KEYWORDS.items()
    }
    if not scores or max(scores.values()) <= 0:
        return "knowledge_note", scores

    # 风险词常常只是修饰词；除非明显高于主领域，否则让更具体的业务领域优先。
    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    winner, top_score = ordered[0]
    if winner == "risk_boundary" and len(ordered) > 1 and ordered[1][1] >= max(2, top_score - 1):
        winner = ordered[1][0]
    return winner, scores


def _choose_layout(card: dict, index: int) -> str:
    text = " ".join(_card_texts([card]))
    if "不是" in text and "而是" in text:
        return "comparison"
    for layout, keywords in LAYOUT_RULES:
        if any(keyword in text for keyword in keywords):
            return layout
    if index == 2:
        return "core_info"
    if index in {5, 6}:
        return "process_flow"
    if index >= 7:
        return "quote_summary"
    return "insight_card"


def _visual_role(card: dict, index: int) -> str:
    text = " ".join(_card_texts([card]))
    role = str(card.get("kicker", "")).lower()
    if index == 1 or card.get("type") == "cover":
        return "scroll-stopping cover"
    if "version" in role or "第一版" in text:
        return "implementation workflow card"
    if "反证" in text or "counter" in role:
        return "counter-evidence explainer"
    if "memory" in role or "失忆" in text or "复盘" in text:
        return "evidence record card"
    if "impulse" in role or "冲动" in text or "刺激" in text:
        return "impulse-control card"
    if "rule" in role or "不许" in text:
        return "rule card"
    if "风险" in text or "边界" in text or "责任" in text or "boundary" in role:
        return "risk-boundary rule card"
    if "流程" in text or "路径" in text or "闭环" in text or "loop" in role:
        return "workflow map"
    if "模式" in text or "两种" in text or "对比" in text:
        return "comparison card"
    if "一句话" in text or "总结" in text:
        return "quote summary"
    if "记录" in text or "证据" in text:
        return "evidence record card"
    return "single-idea explainer"


# 隐喻库：每类观点给一组跨题材候选，按页码轮换，避免每页都是「桌面 + 卡片」。
# 题材刻意跳出办公静物（天平/齿轮/河流/钟摆/水闸/桥/水车/交换台…），
# 但都保持无可读文字、无 K 线红绿、无机器人芯片、无股票元素。
METAPHOR_GROUPS = [
    ("收入|成本|利润|毛利|费用|投入", [
        "一架黄铜天平，一端堆着金属砝码、一端是抽象的生长枝丫，底座有细密刻度和投影",
        "一组相互咬合的机械齿轮带动一个缓慢上升的配重，传动结构清晰、有金属质感",
        "一条分叉的水渠把水引向不同田垄，闸口控制流量，水面有光",
    ]),
    ("微信|小程序|入口|调用|接入|生态", [
        "一张立体城市路网，多条道路从一个枢纽延伸到不同街区，有景深",
        "一座中央车站，多条轨道从站台分发向不同方向，灯光层叠",
        "一棵大树的根系把养分从主干分送到众多枝叶，结构有层次",
    ]),
    ("证据|复盘|记录|留痕|为什么", [
        "夜空下散落的光点被细线连成一条清晰的星座轨迹，背景有渐层光晕",
        "一段被剖开的年轮或地层剖面，一圈圈痕迹按先后记录着过往",
        "一条蜿蜒河流把上游多条支流汇聚成一道清晰主流，河床有沉积纹理",
        "一座灯塔的光束扫过夜海，在水面留下一道清晰的航迹",
        "退潮后的沙滩上留下层层水痕，记录着每一次涨落的先后",
        "许多发光节点被丝线连成一张立体追溯网络，灯光打出层次和景深",
    ]),
    ("反证|推翻|错了|不行动", [
        "一座金属钟摆在两个极端之间摆动，最终停在中线，结构有质感和投影",
        "两块磁极相互排斥又牵引，中间悬浮一个保持平衡的小球",
        "一架天平两端是两种相反的力，横梁微倾又被缓缓拉回",
    ]),
    ("冲动|刺激|上头|冷静", [
        "一道水闸把上涨的水流拦在闸门前，只留一条克制的细缝",
        "一只手将握紧又松开的缰绳，张力清晰可见",
        "一壶正在降温的水，蒸汽从翻腾逐渐归于平静",
    ]),
    ("风险|边界|责任|不能|必须", [
        "一座桥的护栏与桥面边缘，栏杆投下整齐的影子，界限分明",
        "一道防洪堤把汹涌的水挡在一侧，另一侧是平静的田野",
        "一道门槛与半开的门，门内门外呈现两种状态，光从门缝透入",
    ]),
    ("流程|路径|闭环|先|再|最后|输入|输出", [
        "一架水车把水从低处舀起再倒下，形成不停歇的循环，水花有动感",
        "一条环形轨道上一辆小车沿着几个节点循环运行，结构立体",
        "一套相互衔接的管道，液体从入口流经几个处理腔再回到起点",
    ]),
    ("AI|Agent|模型|自动化|工具", [
        "一台老式电话交换台，许多线路被有序接驳到一个中枢面板",
        "一架织布机，多股经纬线在中枢被编织成一块有纹理的布",
        "一个调音台般的控制面板，许多推子和旋钮协同控制一个整体输出",
    ]),
]

METAPHOR_PROFILE_DEFAULTS = {
    "decision_trace": [
        "夜空下散落的光点被细线连成一条清晰的星座轨迹",
        "一条蜿蜒河流把多条支流汇聚成一道主流，河床有沉积纹理",
        "一段年轮剖面，一圈圈痕迹按先后记录着判断",
    ],
    "business_balance": [
        "一架黄铜天平，砝码与生长枝丫两端制衡，底座有刻度",
        "一组咬合齿轮带动配重缓慢上升，传动有金属质感",
        "一条分叉水渠用闸口控制水流向不同田垄",
    ],
    "product_ecosystem": [
        "一张立体城市路网从枢纽延伸到不同街区",
        "一座中央车站把多条轨道分发向不同方向",
        "一棵大树的根系把养分分送到众多枝叶",
    ],
    "automation_workflow": [
        "一架水车把水舀起倒下，形成不停歇的循环",
        "一条环形轨道上小车沿着节点循环运行",
        "一套衔接的管道让液体循环流经几个处理腔",
    ],
    "risk_boundary": [
        "一座桥的护栏与边缘投下整齐的影子，界限分明",
        "一道防洪堤把汹涌的水挡在一侧",
        "一道门槛与半开的门分隔出两种状态",
    ],
}

METAPHOR_QUOTE = [
    "一座由金属或石材构成的大型抽象引号雕塑，旁边一个简洁象征物，光影分明",
    "一块被打磨的巨石上凿出一个引号形的凹槽，质感厚重",
    "一段折叠的丝带在空中盘成引号的造型，材质轻盈有光",
]

METAPHOR_KNOWLEDGE = [
    "一组层叠的透明玻璃片，每片有不同几何符号，叠起来构成一个完整图形",
    "一座由不同体块搭建的抽象雕塑，结构清晰、有光影层次",
    "一束光穿过棱镜分成有序的光谱层次",
]


def _choose_metaphor(card: dict, profile_key: str, layout: str, index: int = 1) -> str:
    text = " ".join(_card_texts([card]))
    pick = max(index - 1, 0)

    for pattern, options in METAPHOR_GROUPS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return options[pick % len(options)]

    if layout == "quote_summary":
        return METAPHOR_QUOTE[pick % len(METAPHOR_QUOTE)]

    options = METAPHOR_PROFILE_DEFAULTS.get(profile_key)
    if options:
        return options[pick % len(options)]
    return METAPHOR_KNOWLEDGE[pick % len(METAPHOR_KNOWLEDGE)]


def _build_card_prompt(
    card: dict,
    profile: dict,
    layout: str,
    metaphor: str,
    visual_role: str,
    preset: dict | None = None,
) -> str:
    title = str(card.get("title", ""))
    subtitle = str(card.get("subtitle", ""))
    style_line = ""
    if preset:
        style_line = f"视觉风格模板：{preset.get('name', '')}。{preset.get('art_direction', '')}"
    return (
        f"主题：{profile['theme']}。"
        f"本页角色：{visual_role}。版式：{layout}。"
        f"标题含义：{title}。辅助观点：{subtitle}。"
        f"可参考视觉方向：{metaphor}。"
        f"风格：{profile['illustration_style']}；{profile['visual_language']}。"
        f"{style_line}"
        "画面要解释文案，不要只做装饰。优先从标题、副标题和要点中提取具体物件、动作、流程、边界或关系；"
        "视觉方向只能辅助，不能让画面变成和文本关系很弱的抽象意象。"
        "以一个贴近文案的清晰主视觉为焦点，并叠加支撑元素、环境细节、层次和材质，让画面充实有信息量但有秩序。"
    )


def _enrich_cover(card: dict, profile: dict) -> None:
    card.setdefault("badge", profile["name"])
    accent_map = {
        "判断留痕": "EVIDENCE / COUNTER / REVIEW",
        "增长与代价": "REVENUE / COST / MARGIN",
        "入口迁移": "ENTRY / ROUTING / SERVICE",
        "自动化工作流": "INPUT / PROCESS / OUTPUT",
        "风险边界": "RULE / BOUNDARY / CHECK",
        "知识笔记": "POINT / REASON / SUMMARY",
    }
    if not card.get("accent") or card.get("accent") == "RESEARCH / SIGNAL / REVIEW":
        card["accent"] = accent_map.get(profile["name"], "POINT / REASON / SUMMARY")
    if "chips" not in card:
        chip_map = {
            "判断留痕": ["先写证据", "再谈行动", "复盘可回看"],
            "增长与代价": ["收入", "成本", "利润"],
            "入口迁移": ["新入口", "新路径", "新分发"],
            "自动化工作流": ["输入", "处理", "输出"],
            "风险边界": ["红线", "责任", "确认"],
            "知识笔记": ["核心信息", "原因", "结论"],
        }
        card["chips"] = chip_map.get(profile["name"], ["核心信息", "原因", "结论"])
