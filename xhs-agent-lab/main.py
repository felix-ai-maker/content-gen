from __future__ import annotations

import argparse
from pathlib import Path

from pipeline import PROJECT_ROOT, generate_package


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
        "--no-push",
        action="store_true",
        help="本次不推送到 Telegram（即使 config.telegram.enabled=true）。",
    )
    parser.add_argument(
        "--config",
        default=str(PROJECT_ROOT / "config.yaml"),
        help="品牌、颜色、字体与输出尺寸配置文件。",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.local_bg and args.direct_ai_card:
        raise SystemExit("--local-bg 和 --direct-ai-card 不能同时使用。")
    mode = "local" if args.local_bg else ("direct" if args.direct_ai_card else "background")

    try:
        result = generate_package(
            topic=args.topic,
            copy_text=args.copy or "",
            copy_file=args.copy_file,
            cards_file=args.cards,
            style=args.style,
            mode=mode,
            push=not args.no_push,
            config_path=Path(args.config).resolve(),
        )
    except ValueError as exc:
        raise SystemExit(str(exc))

    print(f"发布包已生成：{result['output_dir']}")
    print(f"卡片来源：{result['cards_source']}")
    print(f"视觉主题：{result['style_name']} / {result['style_theme']}")
    print(f"出图模式：{'Nano Banana 直接成品图' if mode == 'direct' else '本地排版 + AI 视觉层'}")
    if mode == "direct":
        print(f"视觉风格：{result['style_preset_name']}（{result['style_preset']}）")
    print(f"低 AI 味评分：{result['quality']['score']}/100")
    if result["quality"]["warnings"]:
        print("发布前建议：")
        for warning in result["quality"]["warnings"]:
            print(f"- {warning}")
    for name in result["cards"]:
        print(f"- {name}")


if __name__ == "__main__":
    main()
