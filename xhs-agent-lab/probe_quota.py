"""一次性配额探针：只发一次图像请求，把成功或完整错误打印出来。

用途：撞 429 时，Google 的报错原文里会写明确切的 quota metric 名称和上限，
据此才能在 Cloud Console 的 Vertex Quotas 里准确找到对应配额项。
"""
from __future__ import annotations

import traceback
from pathlib import Path

import yaml

from direct_card_renderer import DirectCardRenderer

ROOT = Path(__file__).resolve().parent


def main() -> None:
    config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    model_cfg = config.get("image_model", {})
    model = model_cfg.get("model", "gemini-3.1-flash-image")

    print("=== 探针配置 ===")
    print("provider :", model_cfg.get("provider"))
    print("model    :", model)
    print("project  :", model_cfg.get("project"))
    print("location :", model_cfg.get("location"))
    print("api_ver  :", model_cfg.get("api_version"))
    print()

    from google import genai
    from google.genai.types import GenerateContentConfig, HttpOptions, Modality

    client = DirectCardRenderer._build_google_client(genai, HttpOptions, model_cfg)

    print("=== 发送 1 次最小图像请求 ===")
    try:
        resp = client.models.generate_content(
            model=model,
            contents="A plain matte light-grey paper texture, no text.",
            config=GenerateContentConfig(
                response_modalities=[Modality.TEXT, Modality.IMAGE],
            ),
        )
        img = DirectCardRenderer._image_from_gemini_response(resp)
        if img is not None:
            print("成功：返回了图像，尺寸", img.size, "→ 当前配额可用。")
        else:
            print("请求返回了，但没有图像 part。原始响应：")
            print(resp)
    except Exception as exc:  # noqa: BLE001 - 探针就是要看完整错误
        print("失败。完整错误原文如下（找 'quota metric' / 'limit' 字样）：")
        print("-" * 60)
        print(repr(exc))
        print("-" * 60)
        print(str(exc))
        print("-" * 60)
        traceback.print_exc()


if __name__ == "__main__":
    main()
