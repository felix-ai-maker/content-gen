from __future__ import annotations

import base64
import json
import os
import textwrap
import time
from io import BytesIO
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageStat


COMMON_FONT_PATHS = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def first_existing_font(paths: Iterable[str | None]) -> str | None:
    for path in paths:
        if path and Path(path).expanduser().exists():
            return str(Path(path).expanduser())
    return None


class FontBook:
    def __init__(self, config: dict):
        font_config = config.get("font", {})
        regular_paths = font_config.get("regular", []) + COMMON_FONT_PATHS
        bold_paths = font_config.get("bold", []) + regular_paths

        self.regular_path = first_existing_font(regular_paths)
        self.bold_path = first_existing_font(bold_paths)

    def regular(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        return self._load(self.regular_path, size)

    def bold(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        return self._load(self.bold_path or self.regular_path, size)

    @staticmethod
    def _load(path: str | None, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        if path:
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                pass
        return ImageFont.load_default()


class CardRenderer:
    def __init__(self, config: dict, project_root: Path):
        self.config = config
        self.project_root = project_root
        self.brand = config.get("brand", {}).get("name", "交易 Agent 实验室")
        self.canvas = config.get("canvas", {})
        self.width = int(self.canvas.get("width", 1080))
        self.height = int(self.canvas.get("height", 1440))
        self.margin = int(self.canvas.get("margin", 88))
        self.content_width = int(self.canvas.get("content_width", 650))
        self.colors = config.get("colors", {})
        self.fonts = FontBook(config)
        self._background_warnings_seen: set[str] = set()
        self._last_ai_request_at = 0.0
        self.generation_records: list[dict] = []

    def render_all(self, cards: list[dict], output_dir: Path, topic: str) -> list[Path]:
        paths: list[Path] = []
        for index, card in enumerate(cards, start=1):
            image = self.render_card(card=card, index=index, total=len(cards), topic=topic)
            path = output_dir / f"card_{index:02d}.png"
            image.save(path, "PNG", optimize=True)
            paths.append(path)
        self._write_generation_meta(output_dir)
        return paths

    def render_card(self, card: dict, index: int, total: int, topic: str) -> Image.Image:
        kind = card.get("type", "content")
        previous_style = getattr(self, "_active_visual_style", {})
        self._active_visual_style = card.get("visual_style", {}) if isinstance(card.get("visual_style"), dict) else {}
        try:
            image = self._make_background(card, index, topic)
            draw = ImageDraw.Draw(image)

            self._draw_system_marks(draw, index, total)
            if kind == "cover" or index == 1:
                self._draw_cover(draw, card, index)
            else:
                self._draw_content(draw, card, index)
            self._draw_footer(draw)
            return image
        finally:
            self._active_visual_style = previous_style

    def _make_background(self, card: dict, index: int, topic: str) -> Image.Image:
        model_cfg = self.config.get("image_model", {})
        ai_bg, candidate_meta = self._try_ai_background_candidates(card, index, topic)
        used_ai = ai_bg is not None
        if ai_bg is None:
            bg = Image.new("RGB", (self.width, self.height), self._color("background"))
        else:
            bg = self._compose_ai_visual_subject(ai_bg, cover=card.get("type") == "cover" or index == 1)

        self.generation_records.append(
            {
                "card": f"card_{index:02d}.png",
                "ai_background_used": used_ai,
                "provider": model_cfg.get("provider", "vertex_gemini"),
                "model": model_cfg.get("model", "gemini-3.1-flash-image"),
                "project": model_cfg.get("project") or os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT"),
                "location": model_cfg.get("location", "global"),
                "background_blur": self.canvas.get("ai_background_blur", 18),
                "background_blend": self.canvas.get("ai_background_blend", 0.98),
                "candidate_count": candidate_meta.get("candidate_count", 0),
                "selected_candidate": candidate_meta.get("selected_candidate"),
                "candidate_scores": candidate_meta.get("candidate_scores", []),
            }
        )

        draw = ImageDraw.Draw(bg, "RGBA")
        grid = self._color("grid") + (42,)

        for x in range(self.margin, self.width - self.margin, 128):
            draw.line((x, 150, x, self.height - 220), fill=grid, width=1)
        for y in range(180, self.height - 230, 150):
            draw.line((self.margin, y, self.width - self.margin, y), fill=grid, width=1)

        draw.rectangle((20, 20, self.width - 20, self.height - 20), outline=self._color("hairline") + (70,), width=1)
        return bg

    def _write_generation_meta(self, output_dir: Path) -> None:
        payload = {
            "image_model": self.config.get("image_model", {}),
            "canvas": {
                "width": self.width,
                "height": self.height,
                "ai_background_blur": self.canvas.get("ai_background_blur"),
                "ai_background_blend": self.canvas.get("ai_background_blend"),
                "ai_background_color": self.canvas.get("ai_background_color"),
                "ai_background_contrast": self.canvas.get("ai_background_contrast"),
            },
            "cards": self.generation_records,
        }
        (output_dir / "generation_meta.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _compose_ai_visual_subject(self, image: Image.Image, cover: bool) -> Image.Image:
        base = Image.new("RGB", (self.width, self.height), self._color("background"))
        visual = self._resize_cover(image, self.width, self.height)
        visual = visual.filter(ImageFilter.GaussianBlur(float(self.canvas.get("ai_background_blur", 0))))
        visual = ImageEnhance.Color(visual).enhance(float(self.canvas.get("ai_background_color", 0.82)))
        visual = ImageEnhance.Contrast(visual).enhance(float(self.canvas.get("ai_background_contrast", 1.08)))
        blend = float(self.canvas.get("ai_background_blend", 0.36))
        visual = Image.blend(visual, base, blend)

        mask = Image.new("L", (self.width, self.height), 0)
        draw = ImageDraw.Draw(mask)
        max_alpha = int(self.canvas.get("ai_subject_alpha", 238))
        top_clean = int(self.canvas.get("cover_ai_top_clean" if cover else "content_ai_top_clean", 520 if cover else 470))
        solid_y = int(self.canvas.get("cover_ai_solid_y" if cover else "content_ai_solid_y", 760 if cover else 620))
        bottom_fade_start = int(self.canvas.get("cover_ai_bottom_fade_start" if cover else "content_ai_bottom_fade_start", 1280))
        for y in range(top_clean, self.height):
            if y < solid_y:
                t = (y - top_clean) / max(1, solid_y - top_clean)
                alpha = int(max_alpha * (t**1.65))
            elif y > bottom_fade_start:
                t = 1 - (y - bottom_fade_start) / max(1, self.height - bottom_fade_start)
                alpha = int(max_alpha * max(0, t) ** 1.4)
            else:
                alpha = max_alpha
            draw.line((0, y, self.width, y), fill=alpha, width=1)

        side_fade = int(self.canvas.get("ai_side_fade", 78))
        for x in range(side_fade):
            factor = (x / max(1, side_fade)) ** 0.8
            col = mask.crop((x, 0, x + 1, self.height))
            col = ImageEnhance.Brightness(col).enhance(factor)
            mask.paste(col, (x, 0))
            right_x = self.width - x - 1
            col = mask.crop((right_x, 0, right_x + 1, self.height))
            col = ImageEnhance.Brightness(col).enhance(factor)
            mask.paste(col, (right_x, 0))

        return Image.composite(visual, base, mask)

    def _apply_reading_scrim(self, image: Image.Image) -> Image.Image:
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay, "RGBA")
        bg = self._color("background")

        left_solid = int(self.width * 0.62)
        fade_width = int(self.width * 0.22)
        draw.rectangle((0, 0, left_solid, self.height), fill=bg + (250,))
        for step in range(fade_width):
            alpha = int(250 * (1 - step / fade_width) ** 2.0)
            x = left_solid + step
            draw.line((x, 0, x, self.height), fill=bg + (alpha,), width=1)
        draw.rectangle((0, 0, self.width, 168), fill=bg + (178,))
        for step in range(150):
            alpha = int(178 * (1 - step / 150) ** 2.0)
            y = 168 + step
            draw.line((0, y, self.width, y), fill=bg + (alpha,), width=1)
        return Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")

    def _draw_editorial_design_layer(self, draw: ImageDraw.ImageDraw, card: dict, index: int) -> None:
        accent = self._color("accent")
        ghost = self._color("ghost")
        hairline = self._color("hairline")
        role = str(card.get("kicker", "")).lower()

        if card.get("type") == "cover" or index == 1:
            self._draw_cover_design(draw, accent, ghost, hairline)
            self._draw_signal_glow_layer(draw, index, accent, hairline, cover=True)
            return

        self._draw_content_design_rail(draw, index, accent, ghost, hairline)
        self._draw_signal_glow_layer(draw, index, accent, hairline, cover=False)
        if "why" in role or "problem" in role:
            self._draw_fragment_design(draw, accent, ghost, hairline)
        elif "role" in role or "system" in role:
            self._draw_workbench_design(draw, accent, ghost, hairline)
        elif "boundary" in role:
            self._draw_boundary_design(draw, accent, ghost, hairline)
        elif "loop" in role:
            self._draw_loop_design(draw, accent, ghost, hairline)
        elif "tone" in role or "taste" in role:
            self._draw_restraint_design(draw, accent, ghost, hairline)
        else:
            self._draw_next_design(draw, accent, ghost, hairline)

    def _draw_content_design_rail(
        self,
        draw: ImageDraw.ImageDraw,
        index: int,
        accent: tuple[int, int, int],
        ghost: tuple[int, int, int],
        hairline: tuple[int, int, int],
    ) -> None:
        rail_left = self.margin + self.content_width + 28
        rail_right = self.width - self.margin
        rail_top = 176
        rail_bottom = self.height - 252

        draw.rounded_rectangle(
            (rail_left, rail_top, rail_right, rail_bottom),
            radius=8,
            fill=ghost + (34,),
            outline=hairline + (100,),
            width=2,
        )
        draw.line((rail_left, rail_top, rail_left, rail_bottom), fill=accent + (52,), width=5)
        draw.line((rail_left + 34, rail_top + 42, rail_right - 30, rail_top + 42), fill=hairline + (90,), width=2)
        draw.line((rail_left + 34, rail_bottom - 120, rail_right - 30, rail_bottom - 120), fill=hairline + (90,), width=2)

        for offset, y in enumerate(range(rail_top + 104, rail_bottom - 160, 92)):
            alpha = 50 if offset % 2 else 34
            draw.line((rail_left + 34, y, rail_right - 30, y), fill=hairline + (alpha,), width=1)
            if offset % 3 == 0:
                draw.rectangle((rail_right - 58, y - 7, rail_right - 36, y + 7), fill=accent + (46,))
            else:
                draw.rectangle((rail_right - 52, y - 5, rail_right - 40, y + 5), fill=hairline + (86,))

        number = f"{index:02d}"
        number_font = self.fonts.bold(156)
        bbox = draw.textbbox((0, 0), number, font=number_font)
        draw.text(
            (rail_right - (bbox[2] - bbox[0]) - 18, rail_bottom - 106),
            number,
            font=number_font,
            fill=hairline + (94,),
        )

        for x in (rail_left + 52, rail_left + 112, rail_left + 172):
            draw.line((x, rail_top + 70, x, rail_bottom - 160), fill=hairline + (24,), width=1)

    def _draw_signal_glow_layer(
        self,
        draw: ImageDraw.ImageDraw,
        index: int,
        accent: tuple[int, int, int],
        hairline: tuple[int, int, int],
        cover: bool,
    ) -> None:
        if cover:
            route = [(694, 1072), (806, 964), (902, 840), (1000, 750)]
            node_boxes = [(780, 934, 814, 968), (884, 820, 918, 854), (980, 730, 1014, 764)]
            arc_box = (610, 610, 1080, 1110)
        else:
            route = [(770, 1170), (838, 1048), (912, 930), (1000, 850)]
            shift = (index % 3) * 32
            node_boxes = [
                (820, 1014 - shift, 848, 1042 - shift),
                (906, 898 - shift, 934, 926 - shift),
                (986, 834 - shift, 1012, 860 - shift),
            ]
            arc_box = (650, 640 - shift, 1120, 1180 - shift)

        for width, alpha in ((18, 22), (10, 34), (4, 96)):
            draw.line(route, fill=accent + (alpha,), width=width, joint="curve")
        draw.arc(arc_box, start=208, end=322, fill=accent + (28,), width=12)
        draw.arc((arc_box[0] + 42, arc_box[1] + 52, arc_box[2] - 62, arc_box[3] - 80), start=218, end=318, fill=accent + (42,), width=5)

        for box in node_boxes:
            draw.ellipse(box, fill=accent + (118,))
            pad = 16
            draw.ellipse(
                (box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad),
                outline=accent + (42,),
                width=2,
            )

        for y in range(380, 1180, 170):
            draw.line((768, y, 1018, y + 46), fill=hairline + (44,), width=2)

    def _draw_cover_design(
        self,
        draw: ImageDraw.ImageDraw,
        accent: tuple[int, int, int],
        ghost: tuple[int, int, int],
        hairline: tuple[int, int, int],
    ) -> None:
        cx, cy = self.width * 0.72, self.height * 0.56
        draw.rounded_rectangle(
            (664, 168, 1008, 1230),
            radius=10,
            fill=ghost + (28,),
            outline=hairline + (84,),
            width=2,
        )
        draw.rectangle((688, 190, 722, 1190), fill=accent + (28,))
        draw.polygon(
            [
                (cx, cy - 330),
                (cx + 280, cy - 40),
                (cx + 20, cy + 330),
                (cx - 260, cy + 30),
            ],
            fill=ghost + (66,),
            outline=hairline + (120,),
        )
        draw.polygon(
            [
                (self.width * 0.56, self.height * 0.66),
                (self.width * 0.89, self.height * 0.84),
                (self.width * 0.62, self.height * 0.96),
            ],
            fill=hairline + (52,),
        )
        for step in range(5):
            y = 420 + step * 118
            draw.line((716, y, 982, y + 42), fill=hairline + (54,), width=2)
        draw.line((650, 690, 910, 540), fill=accent + (78,), width=5)
        draw.line((720, 770, 1010, 1010), fill=hairline + (120,), width=3)
        draw.ellipse((924, 484, 960, 520), fill=accent + (68,))
        draw.ellipse((900, 460, 984, 544), outline=hairline + (96,), width=2)

    def _draw_fragment_design(
        self,
        draw: ImageDraw.ImageDraw,
        accent: tuple[int, int, int],
        ghost: tuple[int, int, int],
        hairline: tuple[int, int, int],
    ) -> None:
        shapes = [
            (754, 224, 1010, 390),
            (796, 760, 1012, 980),
            (748, 1000, 962, 1196),
        ]
        for offset, box in enumerate(shapes):
            draw.rounded_rectangle(box, radius=7, fill=ghost + (52 + offset * 9,), outline=hairline + (118,), width=2)
            draw.line((box[0] + 26, box[1] + 42, box[2] - 24, box[1] + 42), fill=hairline + (82,), width=2)
        draw.line((774, 374, 936, 872), fill=hairline + (118,), width=2)
        draw.ellipse((928, 864, 950, 886), fill=accent + (120,))

    def _draw_workbench_design(
        self,
        draw: ImageDraw.ImageDraw,
        accent: tuple[int, int, int],
        ghost: tuple[int, int, int],
        hairline: tuple[int, int, int],
    ) -> None:
        for item_index, y in enumerate((650, 760, 870, 980)):
            x1 = 758 + item_index * 18
            draw.rounded_rectangle((x1, y, 1000, y + 78), radius=5, fill=ghost + (44,), outline=hairline + (112,), width=2)
            draw.line((x1 + 28, y + 39, 972, y + 39), fill=hairline + (96,), width=1)
            draw.ellipse((x1 + 22, y + 28, x1 + 40, y + 46), fill=(accent if item_index == 1 else hairline) + (92,))
        draw.line((788, 650, 890, 1058), fill=accent + (68,), width=4)

    def _draw_boundary_design(
        self,
        draw: ImageDraw.ImageDraw,
        accent: tuple[int, int, int],
        ghost: tuple[int, int, int],
        hairline: tuple[int, int, int],
    ) -> None:
        draw.rectangle((884, 176, 932, 1210), fill=ghost + (58,))
        draw.line((908, 176, 908, 1210), fill=accent + (70,), width=4)
        draw.polygon([(776, 780), (1040, 636), (1040, 1056), (776, 1190)], fill=hairline + (48,))
        draw.line((810, 838, 1004, 742), fill=hairline + (122,), width=3)
        draw.line((814, 930, 998, 838), fill=accent + (52,), width=3)
        for y in (312, 456, 600, 744, 888, 1032):
            draw.line((856, y, 960, y), fill=hairline + (64,), width=1)

    def _draw_loop_design(
        self,
        draw: ImageDraw.ImageDraw,
        accent: tuple[int, int, int],
        ghost: tuple[int, int, int],
        hairline: tuple[int, int, int],
    ) -> None:
        box = (720, 612, 1080, 1048)
        draw.arc(box, start=18, end=330, fill=hairline + (132,), width=10)
        draw.arc((768, 672, 1036, 996), start=205, end=520, fill=ghost + (150,), width=24)
        draw.arc((814, 724, 996, 944), start=30, end=280, fill=hairline + (70,), width=5)
        draw.ellipse((982, 758, 1008, 784), fill=accent + (120,))
        draw.rounded_rectangle((760, 1054, 1000, 1164), radius=6, fill=ghost + (48,), outline=hairline + (110,), width=2)
        draw.line((792, 1108, 962, 1108), fill=hairline + (92,), width=2)

    def _draw_restraint_design(
        self,
        draw: ImageDraw.ImageDraw,
        accent: tuple[int, int, int],
        ghost: tuple[int, int, int],
        hairline: tuple[int, int, int],
    ) -> None:
        draw.line((760, 374, 1000, 374), fill=hairline + (112,), width=2)
        draw.line((806, 430, 960, 990), fill=hairline + (74,), width=2)
        draw.ellipse((900, 772, 940, 812), fill=accent + (78,))
        draw.ellipse((874, 746, 966, 838), outline=hairline + (110,), width=2)
        draw.ellipse((846, 718, 994, 866), outline=hairline + (54,), width=2)
        draw.rounded_rectangle((748, 1030, 1000, 1124), radius=7, fill=ghost + (40,), outline=hairline + (92,), width=2)
        for x in (790, 852, 914, 976):
            draw.line((x, 1056, x, 1098), fill=hairline + (66,), width=2)

    def _draw_next_design(
        self,
        draw: ImageDraw.ImageDraw,
        accent: tuple[int, int, int],
        ghost: tuple[int, int, int],
        hairline: tuple[int, int, int],
    ) -> None:
        layers = [
            [(742, 900), (1004, 812), (1042, 884), (780, 988)],
            [(764, 1034), (1018, 938), (1050, 1012), (800, 1120)],
            [(792, 1166), (1030, 1068), (1064, 1136), (832, 1242)],
        ]
        for item_index, layer in enumerate(layers):
            draw.polygon(layer, fill=ghost + (50 + item_index * 8,), outline=hairline + (116,))
        draw.line((770, 924, 1014, 1118), fill=accent + (72,), width=4)
        draw.ellipse((1004, 1108, 1024, 1128), fill=accent + (104,))

    def _try_ai_background_candidates(self, card: dict, index: int, topic: str) -> tuple[Image.Image | None, dict]:
        model_cfg = self.config.get("image_model", {})
        count = self._candidate_count_for_card(card, index, model_cfg)
        candidate_scores: list[dict] = []
        best_image: Image.Image | None = None
        best_score: float | None = None
        selected_candidate: int | None = None

        for candidate_index in range(1, count + 1):
            image = self._try_ai_background(card, index, topic, candidate_index, count)
            if image is None:
                candidate_scores.append({"candidate": candidate_index, "ok": False})
                continue
            score = self._score_ai_candidate(image, cover=card.get("type") == "cover" or index == 1)
            score_record = {"candidate": candidate_index, "ok": True, **score}
            candidate_scores.append(score_record)
            if best_score is None or score["score"] > best_score:
                best_score = score["score"]
                best_image = image
                selected_candidate = candidate_index

        return best_image, {
            "candidate_count": count,
            "selected_candidate": selected_candidate,
            "candidate_scores": candidate_scores,
        }

    @staticmethod
    def _candidate_count_for_card(card: dict, index: int, model_cfg: dict) -> int:
        if card.get("type") == "cover" or index == 1:
            return max(1, int(model_cfg.get("cover_candidates_per_card", model_cfg.get("candidates_per_card", 1))))
        return max(1, int(model_cfg.get("content_candidates_per_card", model_cfg.get("candidates_per_card", 1))))

    def _score_ai_candidate(self, image: Image.Image, cover: bool) -> dict:
        resized = self._resize_cover(image.convert("RGB"), self.width, self.height).convert("L")
        right_x = int(self.width * (0.48 if cover else 0.56))
        left_x = int(self.width * 0.52)
        top_y = int(self.height * 0.22)

        right = resized.crop((right_x, top_y, self.width, self.height))
        left = resized.crop((0, 0, left_x, self.height))
        top = resized.crop((0, 0, self.width, 230))
        title_zone = resized.crop((0, 180, self.width if cover else left_x, 680 if cover else 360))

        right_stat = ImageStat.Stat(right)
        left_edge = ImageStat.Stat(left.filter(ImageFilter.FIND_EDGES)).mean[0]
        top_edge = ImageStat.Stat(top.filter(ImageFilter.FIND_EDGES)).mean[0]
        title_edge = ImageStat.Stat(title_zone.filter(ImageFilter.FIND_EDGES)).mean[0]
        right_edge = ImageStat.Stat(right.filter(ImageFilter.FIND_EDGES)).mean[0]
        right_contrast = right_stat.stddev[0]
        score = right_contrast * 1.1 + right_edge * 1.8 - left_edge * 1.4 - top_edge * 1.2 - title_edge * 0.8

        return {
            "score": round(score, 2),
            "right_contrast": round(right_contrast, 2),
            "right_edge": round(right_edge, 2),
            "left_edge_penalty": round(left_edge, 2),
            "top_edge_penalty": round(top_edge, 2),
            "title_edge_penalty": round(title_edge, 2),
        }

    def _try_ai_background(
        self,
        card: dict,
        index: int,
        topic: str,
        candidate_index: int = 1,
        total_candidates: int = 1,
    ) -> Image.Image | None:
        model_cfg = self.config.get("image_model", {})
        if not model_cfg.get("enabled", True):
            return None

        provider = str(model_cfg.get("provider", "vertex_gemini")).lower()
        if provider in {"vertex_gemini", "gemini", "google_gemini"}:
            return self._try_vertex_gemini_background(model_cfg, card, index, topic, candidate_index, total_candidates)
        if provider in {"vertex_imagen", "imagen", "google_imagen"}:
            return self._try_vertex_imagen_background(model_cfg, card, index, topic, candidate_index, total_candidates)
        if provider == "openai":
            return self._try_openai_background(model_cfg, card, index, topic, candidate_index, total_candidates)

        self._warn_background_skip("unknown-provider", index, f"unknown provider {provider!r}")
        return None

    def _try_openai_background(
        self,
        model_cfg: dict,
        card: dict,
        index: int,
        topic: str,
        candidate_index: int,
        total_candidates: int,
    ) -> Image.Image | None:
        if not os.getenv("OPENAI_API_KEY"):
            return None
        try:
            from openai import OpenAI

            client = OpenAI()
            prompt = self._background_prompt(card, index, topic, candidate_index, total_candidates)
            result = client.images.generate(
                model=model_cfg.get("model", "gpt-image-1"),
                prompt=prompt,
                size=model_cfg.get("size", "1024x1536"),
                quality=model_cfg.get("quality", "high"),
                n=1,
            )
            image_b64 = result.data[0].b64_json
            if not image_b64:
                return None
            return Image.open(BytesIO(base64.b64decode(image_b64))).convert("RGB")
        except Exception as exc:
            self._warn_background_skip("openai", index, exc)
            return None

    def _try_vertex_gemini_background(
        self,
        model_cfg: dict,
        card: dict,
        index: int,
        topic: str,
        candidate_index: int,
        total_candidates: int,
    ) -> Image.Image | None:
        try:
            from google import genai
            from google.genai.types import GenerateContentConfig, HttpOptions, Modality

            client = self._build_google_client(genai, HttpOptions, model_cfg)
            prompt = self._background_prompt(card, index, topic, candidate_index, total_candidates)
            self._wait_for_ai_slot(model_cfg)
            response = self._with_quota_retry(
                lambda: client.models.generate_content(
                    model=model_cfg.get("model", "gemini-2.5-flash-image"),
                    contents=prompt,
                    config=GenerateContentConfig(
                        response_modalities=[Modality.TEXT, Modality.IMAGE],
                    ),
                ),
                model_cfg,
            )
            return self._image_from_gemini_response(response)
        except Exception as exc:
            self._warn_background_skip("vertex-gemini", index, exc)
            return None

    def _try_vertex_imagen_background(
        self,
        model_cfg: dict,
        card: dict,
        index: int,
        topic: str,
        candidate_index: int,
        total_candidates: int,
    ) -> Image.Image | None:
        try:
            from google import genai
            from google.genai.types import GenerateImagesConfig, HttpOptions

            client = self._build_google_client(genai, HttpOptions, model_cfg)
            prompt = self._background_prompt(card, index, topic, candidate_index, total_candidates)
            self._wait_for_ai_slot(model_cfg)
            response = self._with_quota_retry(
                lambda: client.models.generate_images(
                    model=model_cfg.get("model", "imagen-4.0-generate-001"),
                    prompt=prompt,
                    config=GenerateImagesConfig(
                        number_of_images=int(model_cfg.get("number_of_images", 1)),
                        image_size=model_cfg.get("image_size", "1K"),
                        aspect_ratio=model_cfg.get("aspect_ratio", "3:4"),
                    ),
                ),
                model_cfg,
            )
            generated = response.generated_images[0]
            image_obj = generated.image
            if hasattr(image_obj, "image_bytes") and image_obj.image_bytes:
                return Image.open(BytesIO(image_obj.image_bytes)).convert("RGB")

            buffer = BytesIO()
            image_obj.save(buffer)
            buffer.seek(0)
            return Image.open(buffer).convert("RGB")
        except Exception as exc:
            self._warn_background_skip("vertex-imagen", index, exc)
            return None

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

    def _warn_background_skip(self, key: str, index: int, error: object) -> None:
        if key in self._background_warnings_seen:
            return
        self._background_warnings_seen.add(key)
        print(f"AI background skipped from card_{index:02d}: {error}")

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
            return genai.Client(
                vertexai=True,
                project=project,
                location=location,
                http_options=http_options,
            )
        if api_key:
            return genai.Client(
                vertexai=True,
                api_key=api_key,
                http_options=http_options,
            )
        return genai.Client(http_options=http_options)

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

    def _background_prompt(
        self,
        card: dict,
        index: int,
        topic: str,
        candidate_index: int = 1,
        total_candidates: int = 1,
    ) -> str:
        design_cfg = self.config.get("design_prompt", {})
        negative = ", ".join(self.config.get("visual_rules", {}).get("avoid", []))
        principles = "; ".join(design_cfg.get("principles", []))
        materials = "; ".join(design_cfg.get("materials", []))
        composition = self._composition_prompt(card, index)
        metaphor = self._metaphor_prompt(card, index)
        visual_brief = self._visual_brief_prompt(card, index)
        variation = self._candidate_variation_prompt(candidate_index)
        style = card.get("visual_style", {}) if isinstance(card.get("visual_style"), dict) else {}
        directed_prompt = style.get("prompt", "")
        directed_theme = style.get("theme", "")
        directed_layout = style.get("layout", "")
        directed_metaphor = style.get("metaphor", "")
        directed_language = style.get("visual_language", "")
        directed_illustration = style.get("illustration_style", "")
        accent_color = style.get("accent_color", self.colors.get("accent", "#1F6FFF"))
        support_color = style.get("support_color", self.colors.get("ghost", "#E8EBF0"))
        is_cover = card.get("type") == "cover" or index == 1
        thumbnail_goal = (
            "This is the cover, so the visual must read instantly as a premium topic-specific knowledge-card object at phone thumbnail size."
            if is_cover
            else "This is a content card, so the visual should explain the card idea clearly without stealing the text area."
        )
        text_field = "top 42%" if is_cover else "top 34%"
        visual_field = "middle and lower 58%" if is_cover else "middle 48% to lower 38%"
        return textwrap.dedent(
            f"""
            You are the art director for a premium Chinese vertical social-media editorial series.
            Create a complete visual background layer for a 1080x1440 Xiaohongshu card. Local code will add
            the Chinese typography later, so your image must carry the visual design value without containing text.

            Editorial theme: {topic}
            Card role: {card.get("kicker", f"card {index:02d}")}
            Card title meaning: {card.get("title", "")}
            Card emotional metaphor: {metaphor}
            Auto-generated style theme: {directed_theme}
            Auto-generated card layout: {directed_layout}
            Auto-generated visual metaphor: {directed_metaphor}
            Auto-generated style direction:
            {directed_prompt}

            Primary visual brief:
            {visual_brief}

            Candidate variation:
            {variation}

            Composition contract:
            - {thumbnail_goal}
            - {text_field}: quiet premium off-white reading field for Chinese typography, almost empty, no marks that resemble letters.
            - {visual_field}: the main designed visual metaphor, rich, dimensional, and memorable.
            - The visual must have one memorable focal object or spatial structure, not just abstract wallpaper.
            - Use a clear foreground object, one midground system relation, and a restrained background field.
            - Leave enough quiet space for Chinese type, but make the overall image feel designed and publishable.
            - Build a strong silhouette: the focal object should still be recognizable if blurred slightly.

            Design system:
            - Art direction: premium Xiaohongshu knowledge-card explainer, topic-specific metaphor, editorial information design, human-made publication layout.
            - Visual language: {directed_language}
            - Illustration mode: {directed_illustration}
            - Palette: off-white, black, graphite grey, mist grey, accent {accent_color}, soft support tint {support_color}.
            - Principles: {principles}
            - Materials and texture: {materials}
            - Composition: {composition}
            - All visible surfaces must be blank geometric material: no letters, no label marks, no readable numbers.
            - Visual vocabulary must follow the auto-generated style direction above: use explanatory metaphor objects,
              simple diagram structure, topic-specific props, blank cards, panels, levers, routes, gates, scales,
              folders, checks, calibration marks, or app-like modules only when they fit the copy.
            - Design quality target: attractive enough for a user to stop scrolling on Xiaohongshu; restrained but not bland.
            - Visual energy: less corporate, less generic AI poster, more like a carefully designed independent creator card.
            - Must be visually obvious: the final background must not look like a plain paper texture.
            - Depth: make the central/lower visual field visibly rich, spatial, and premium, with clear metaphor objects.
            - Use a few large forms instead of many tiny details, because local Chinese typography will be added later.
            - Text safety: keep the top reading field bright enough for black Chinese text, but do not make the whole image plain.
            - Finish: this should feel like a designed editorial explainer object, not a generic AI background.

            Absolute bans:
            - no readable text, no Chinese characters, no pseudo text, no letter-like marks, no numbers, no typography, no watermark
            - do not render the letters A or I, do not render the word AI, even as a tiny interface label
            - no UI words, no logos, no app icons, no charts, no candlesticks, no bull/bear, no coins, no red/green trading cues
            - no poster title, no slogan, no fake labels, no interface screenshot, no dashboard
            - no generic blue-purple cyberpunk, no neon city, no server-rack cliche, no finance influencer style
            - do not ignore the auto-generated metaphor; do not default to generic glass technology scenery

            Avoid: {negative}
            """
        ).strip()

    @staticmethod
    def _candidate_variation_prompt(candidate_index: int) -> str:
        variations = [
            "Variation A: vertical installation, tall glass core, restrained blue routes, magazine-cover clarity.",
            "Variation B: diagonal lab-bench perspective, layered slabs, stronger foreground depth, more scroll-stopping.",
            "Variation C: compact object cluster, luminous core, sparse surrounding space, highly refined and quiet.",
        ]
        return variations[(candidate_index - 1) % len(variations)]

    @staticmethod
    def _visual_brief_prompt(card: dict, index: int) -> str:
        style = card.get("visual_style", {}) if isinstance(card.get("visual_style"), dict) else {}
        if style.get("prompt"):
            return str(style["prompt"])
        role = str(card.get("kicker", "")).lower()
        if card.get("type") == "cover" or index == 1:
            return (
                "Build a striking hero scene about reconstructing a forgotten decision: a tall transparent evidence vault "
                "with blank memory plates locked into place, a graphite responsibility base, and one sharp electric-blue "
                "route tracing the decision chain. It should feel like a premium product launch poster for a personal "
                "research instrument, not software UI. Strong right-side silhouette, deeper graphite shadows, clear "
                "foreground object, high thumbnail impact."
            )
        if "why" in role or "problem" in role or "pain" in role or "memory" in role:
            return (
                "Show memory loss becoming traceable evidence: a few large blank glass archive plates, separated graphite "
                "folders, missing slots, and a blue optical thread reconnecting them into a vertical evidence spine. Make "
                "the broken-to-ordered transformation visible through composition, not through text."
            )
        if "impulse" in role or "tone" in role or "taste" in role:
            return (
                "Show impulse being slowed down: a precise physical brake gate, a suspended blue control core, muted glass "
                "planes, and a narrow decision channel that forces pause before action. Premium, tense, and disciplined, "
                "not dramatic or chaotic."
            )
        if "rule" in role or "role" in role or "system" in role:
            return (
                "Show a research assistant workbench without any human or screen text: a premium glass desk object with "
                "three blank evidence trays, one risk tray, and a small blue reasoning core. Make it feel useful, calm, "
                "and organized, not magical or predictive."
            )
        if "counter" in role:
            return (
                "Show a counter-evidence instrument: two opposing blank glass plates held in balanced tension, a blue "
                "calibration line between them, and graphite restraints that prevent one side from dominating. No arrows, "
                "no text, no market symbols."
            )
        if "responsibility" in role or "boundary" in role:
            return (
                "Show a responsibility boundary: a tall transparent threshold plane cutting through a lab table, with tool "
                "materials on one side and an empty human decision space on the other. Use one precise blue edge light and "
                "controlled tension, with no trading symbols."
            )
        if "loop" in role:
            return (
                "Show a closed-loop research machine: a circular glass rail with four unlabeled stations, modular plates, "
                "blue optical flow, and a grounded review dock. No arrows and no text, but the viewer should feel input, "
                "evidence, action, and review."
            )
        return (
            "Show a small system growing into a usable workstation: ascending modular glass blocks, a compact lab-bench "
            "structure, and a blue route linking each module. It should feel practical, composable, and ready to use tomorrow."
        )

    def _composition_prompt(self, card: dict, index: int) -> str:
        style = card.get("visual_style", {}) if isinstance(card.get("visual_style"), dict) else {}
        if style.get("layout"):
            return (
                f"{style.get('layout')} layout, topic-specific explanatory composition, "
                f"visual metaphor: {style.get('metaphor', '')}, clean large Chinese text field"
            )
        role = str(card.get("kicker", "")).lower()
        if card.get("type") == "cover" or index == 1:
            return (
                "hero cover layout, large premium product-like sci-tech object in the right half and lower half, "
                "visible glass panels, blue optical traces, dimensional depth, wide blank editorial field on the left, "
                "high thumbnail contrast"
            )
        if "why" in role or "problem" in role or "pain" in role or "memory" in role:
            return (
                "fragmented information motif, multiple floating translucent data shards and signal particles "
                "being organized into a system, clean main text area, strong technology feel on the right"
            )
        if "impulse" in role:
            return (
                "impulse-control motif, a premium physical brake gate and blue calibration core, controlled tension, "
                "clean main text area, high-end restraint instead of excitement"
            )
        if "rule" in role or "role" in role or "system" in role:
            return (
                "AI research workbench motif, layered glass control surfaces, blue connection paths, abstract "
                "agent orchestration nodes, calm but clearly high-tech"
            )
        if "counter" in role:
            return (
                "counter-evidence motif, two opposing blank glass plates in balanced tension, calibration line, "
                "disciplined system feel, no trading symbols"
            )
        if "responsibility" in role or "boundary" in role:
            return (
                "boundary and responsibility motif, a bright vertical threshold plane, split-space glass architecture, "
                "controlled tension, premium technical lighting"
            )
        if "loop" in role:
            return (
                "closed-loop system motif, circular blue energy path and layered workflow modules in perspective, "
                "no literal trading charts, high-end sci-tech editorial design"
            )
        if "tone" in role or "taste" in role:
            return (
                "restraint motif, disciplined negative space with a precise glowing blue control core, minimal "
                "glass geometry, calm advanced laboratory atmosphere"
            )
        if "version" in role or "next" in role:
            return (
                "next-step roadmap motif, ascending glass planes and modular system blocks, blue optical route, "
                "forward motion without arrows or trading imagery"
            )
        return (
            "editorial content-page layout, asymmetric pale geometric layers around the margins, high readability, "
            "large blank center-left area"
        )

    @staticmethod
    def _metaphor_prompt(card: dict, index: int) -> str:
        style = card.get("visual_style", {}) if isinstance(card.get("visual_style"), dict) else {}
        if style.get("metaphor"):
            return str(style["metaphor"])
        title = str(card.get("title", ""))
        if index == 1:
            return "reconstructing the forgotten reason behind a trade into a visible evidence chain"
        role = str(card.get("kicker", "")).lower()
        if "memory" in role or "失忆" in title:
            return "lost decision memory being reconstructed into traceable evidence"
        if "impulse" in role or "冲动" in title:
            return "slowing impulsive action before it pretends to be a system"
        if "counter" in role or "反证" in title:
            return "forcing each opinion to stand beside its counter-evidence"
        if "responsibility" in role or "甩锅" in title:
            return "a clear boundary between automation and human responsibility"
        if "判断" in title or "信息" in title or "pain" in role:
            return "scattered fragments slowly becoming a research structure"
        if "预测" in title or "助理" in title:
            return "an assistant desk that organizes evidence without making decisions"
        if "负责" in title or "危险" in title:
            return "a clear boundary between tool output and human responsibility"
        if "闭环" in title or "复盘" in title:
            return "a repeatable loop of input, evidence, action, and review"
        if "刺激" in title or "冷静" in title or "克制" in title:
            return "lowering emotional noise and preserving discipline"
        if "小版本" in title or "跑起来" in title:
            return "a small usable system growing layer by layer"
        return "structured thinking becoming visible"

    def _draw_cover(self, draw: ImageDraw.ImageDraw, card: dict, index: int) -> None:
        x = self.margin
        y = 190
        max_width = int(self.canvas.get("cover_title_width", self.width - self.margin * 2))

        kicker = card.get("kicker", f"{self.brand}  /  {index:02d}")
        self._draw_kicker(draw, x, y, kicker)

        badge = str(card.get("badge", "")).strip()
        if badge:
            y += 58
            badge_font = self.fonts.bold(24)
            badge_text = f" {badge} "
            badge_width = int(draw.textlength(badge_text, font=badge_font)) + 28
            draw.rounded_rectangle(
                (x, y, x + badge_width, y + 46),
                radius=8,
                fill=self._color("text"),
            )
            draw.text((x + 14, y + 11), badge_text, font=badge_font, fill=self._color("background"))
            y += 62
        else:
            y += 92

        title = card.get("title", "")
        title_size = int(card.get("title_size", 94 if len(str(title)) <= 24 else 86))
        title_blocks = card.get("title_blocks")
        title_lines = card.get("title_lines")
        if isinstance(title_blocks, list) and title_blocks:
            y = self._draw_title_blocks(draw, title_blocks, (x, y), title_size)
        elif isinstance(title_lines, list) and title_lines:
            y = self._draw_lines_text(
                draw,
                [str(line).strip() for line in title_lines if str(line).strip()],
                (x, y),
                self.fonts.bold(title_size),
                self._color("text"),
                line_spacing=14,
            )
        else:
            y = self._draw_wrapped_text(
                draw,
                title,
                (x, y),
                self.fonts.bold(title_size),
                self._color("text"),
                max_width,
                line_spacing=14,
            )

        draw.line((x, y + 12, x + 168, y + 12), fill=self._color("accent"), width=10)

        subtitle = card.get("subtitle", "")
        if subtitle:
            y += 54
            y = self._draw_wrapped_text(
                draw,
                subtitle,
                (x, y),
                self.fonts.regular(36),
                self._color("muted"),
                max_width - 40,
                line_spacing=14,
            )

        hook = str(card.get("hook", "")).strip()
        if hook:
            y += 40
            y = self._draw_cover_hook(draw, x, y, hook, max_width - 70)

        chips = card.get("chips") or []
        if isinstance(chips, str):
            chips = [item.strip() for item in chips.split("/") if item.strip()]
        chip_y = max(y + 70, self.height - 420)
        self._draw_chips(draw, x, chip_y, [str(item).strip() for item in chips if str(item).strip()])

        accent_text = card.get("accent", "SYSTEM / SIGNAL / ACTION")
        accent_y = self.height - 318
        draw.line((x, accent_y, x + 136, accent_y), fill=self._color("accent"), width=8)
        draw.text((x, accent_y + 42), accent_text, font=self.fonts.bold(28), fill=self._color("accent"))

        number_font = self.fonts.bold(220)
        number = f"{index:02d}"
        bbox = draw.textbbox((0, 0), number, font=number_font)
        draw.text(
            (self.width - self.margin - (bbox[2] - bbox[0]), self.height - 455),
            number,
            font=number_font,
            fill=self._color("ghost"),
        )

    def _draw_content(self, draw: ImageDraw.ImageDraw, card: dict, index: int) -> None:
        x = self.margin
        y = 154
        max_width = self.width - self.margin * 2

        self._draw_kicker(draw, x, y, f"{index:02d}")

        title = card.get("title", "")
        y += 70
        y = self._draw_wrapped_text(
            draw,
            title,
            (x, y),
            self.fonts.bold(56),
            self._color("text"),
            max_width,
            line_spacing=12,
        )

        subtitle = card.get("subtitle", "")
        if subtitle:
            y += 22
            y = self._draw_wrapped_text(
                draw,
                subtitle,
                (x, y),
                self.fonts.regular(31),
                self._color("muted"),
                max_width,
                line_spacing=12,
            )

        highlight = str(card.get("highlight", "")).strip()
        if highlight:
            y += 34
            self._draw_statement_bar(draw, x, y, highlight, max_width)

        bullets = self._normalize_bullets(card)
        note = card.get("note")
        bottom_y = self.height - 312
        if bullets:
            self._draw_micro_points(draw, x, bottom_y - 88, bullets[:2], max_width)
        if note:
            note_y = bottom_y
            draw.line((x, note_y, x + 92, note_y), fill=self._color("accent"), width=5)
            self._draw_wrapped_text(
                draw,
                note,
                (x, note_y + 30),
                self.fonts.bold(31),
                self._color("text"),
                max_width,
                line_spacing=10,
            )

    def _draw_statement_bar(
        self,
        draw: ImageDraw.ImageDraw,
        x: int,
        y: int,
        text: str,
        max_width: int,
    ) -> int:
        font = self.fonts.bold(35)
        lines = self._wrap_text(draw, text, font, max_width - 72)
        line_spacing = 10
        line_heights = []
        for line in lines:
            bbox = draw.textbbox((0, 0), line or " ", font=font)
            line_heights.append(bbox[3] - bbox[1])
        text_height = sum(line_heights) + line_spacing * max(0, len(lines) - 1)
        bottom = y + text_height + 42
        fill = self._mix(self._color("ghost"), self._color("background"), 0.44)
        draw.rounded_rectangle(
            (x, y, x + max_width, bottom),
            radius=8,
            fill=fill,
            outline=self._mix(self._color("hairline"), self._color("background"), 0.28),
            width=1,
        )
        draw.rectangle((x, y, x + 12, bottom), fill=self._color("accent"))
        cursor_y = y + 20
        for line, line_height in zip(lines, line_heights):
            draw.text((x + 40, cursor_y), line, font=font, fill=self._color("text"))
            cursor_y += line_height + line_spacing
        return bottom

    def _draw_micro_points(
        self,
        draw: ImageDraw.ImageDraw,
        x: int,
        y: int,
        bullets: list[str],
        max_width: int,
    ) -> int:
        font = self.fonts.regular(25)
        cursor_y = y
        for bullet in bullets:
            text = str(bullet).strip("。")
            if len(text) > 25:
                text = text[:24].rstrip("，。；、 ") + "…"
            draw.ellipse((x, cursor_y + 12, x + 9, cursor_y + 21), fill=self._color("accent"))
            draw.text((x + 26, cursor_y), text, font=font, fill=self._color("muted"))
            cursor_y += 38
        return cursor_y

    def _draw_cover_hook(
        self,
        draw: ImageDraw.ImageDraw,
        x: int,
        y: int,
        text: str,
        max_width: int,
    ) -> int:
        font = self.fonts.regular(30)
        lines = self._wrap_text(draw, text, font, max_width - 38)
        line_spacing = 11
        heights = []
        for line in lines:
            bbox = draw.textbbox((0, 0), line or " ", font=font)
            heights.append(bbox[3] - bbox[1])
        total_height = sum(heights) + line_spacing * max(0, len(lines) - 1)
        top = y - 8
        bottom = y + total_height + 20
        draw.rectangle((x, top, x + 9, bottom), fill=self._color("accent"))
        draw.rounded_rectangle(
            (x + 24, top, x + max_width, bottom),
            radius=8,
            fill=self._mix(self._color("ghost"), self._color("background"), 0.46),
            outline=self._mix(self._color("hairline"), self._color("background"), 0.44),
            width=1,
        )
        cursor_y = y
        for line, height in zip(lines, heights):
            draw.text((x + 46, cursor_y), line, font=font, fill=self._color("text"))
            cursor_y += height + line_spacing
        return bottom

    def _draw_title_blocks(
        self,
        draw: ImageDraw.ImageDraw,
        blocks: list[dict],
        xy: tuple[int, int],
        fallback_size: int,
    ) -> int:
        x, y = xy
        allowed_colors = {"background", "text", "muted", "accent", "hairline", "grid", "ghost"}
        for block in blocks:
            if isinstance(block, dict):
                text = str(block.get("text", "")).strip()
                size = int(block.get("size", fallback_size))
                fill_name = str(block.get("fill", "text"))
            else:
                text = str(block).strip()
                size = fallback_size
                fill_name = "text"
            if not text:
                continue
            font = self.fonts.bold(size)
            fill = self._color(fill_name) if fill_name in allowed_colors else self._color("text")
            draw.text((x, y), text, font=font, fill=fill)
            bbox = draw.textbbox((x, y), text, font=font)
            y += (bbox[3] - bbox[1]) + int(max(8, size * 0.12))
        return y

    def _draw_chips(self, draw: ImageDraw.ImageDraw, x: int, y: int, chips: list[str]) -> None:
        if not chips:
            return
        font = self.fonts.bold(25)
        cursor_x = x
        for index, chip in enumerate(chips[:3]):
            text = f" {chip} "
            width = int(draw.textlength(text, font=font)) + 34
            fill = self._color("accent") if index == 0 else self._color("background")
            outline = self._color("accent") if index == 0 else self._color("hairline")
            text_fill = self._color("background") if index == 0 else self._color("text")
            draw.rounded_rectangle(
                (cursor_x, y, cursor_x + width, y + 48),
                radius=8,
                fill=fill,
                outline=outline,
                width=2,
            )
            draw.text((cursor_x + 17, y + 12), text, font=font, fill=text_fill)
            cursor_x += width + 16

    def _draw_highlight(
        self,
        draw: ImageDraw.ImageDraw,
        x: int,
        y: int,
        text: str,
        max_width: int,
    ) -> int:
        font = self.fonts.bold(34)
        label_font = self.fonts.bold(20)
        text_x = x + 38
        text_width = max_width - 68
        lines = self._wrap_text(draw, text, font, text_width)
        line_spacing = 12
        line_heights = []
        for line in lines:
            bbox = draw.textbbox((0, 0), line or " ", font=font)
            line_heights.append(bbox[3] - bbox[1])
        text_height = sum(line_heights) + line_spacing * max(0, len(lines) - 1)
        top = y
        bottom = y + text_height + 72
        draw.rounded_rectangle(
            (x, top, x + max_width, bottom),
            radius=8,
            fill=self._mix(self._color("ghost"), self._color("background"), 0.58),
            outline=self._color("hairline"),
            width=2,
        )
        draw.rectangle((x, top, x + 12, bottom), fill=self._color("accent"))
        draw.text((text_x, top + 18), "核心判断", font=label_font, fill=self._color("accent"))
        cursor_y = top + 44
        for line, line_height in zip(lines, line_heights):
            draw.text((text_x, cursor_y), line, font=font, fill=self._color("text"))
            cursor_y += line_height + line_spacing
        return bottom

    def _draw_bullet(
        self,
        draw: ImageDraw.ImageDraw,
        x: int,
        y: int,
        text: str,
        bullet_index: int,
        max_width: int,
    ) -> int:
        font = self.fonts.regular(int(self.canvas.get("bullet_font_size", 34)))
        marker_font = self.fonts.bold(20)
        text_x = x + 76
        text_width = max_width - 76
        line_spacing = 15
        lines = self._wrap_text(draw, text, font, text_width)
        line_heights = []
        for line in lines:
            bbox = draw.textbbox((0, 0), line or " ", font=font)
            line_heights.append(bbox[3] - bbox[1])
        text_height = sum(line_heights) + line_spacing * max(0, len(lines) - 1)

        panel_fill = self._mix(self._color("ghost"), self._color("background"), 0.34)
        panel_line = self._mix(self._color("hairline"), self._color("background"), 0.74)
        panel_top = y - 16
        panel_bottom = y + text_height + 22
        draw.rounded_rectangle(
            (x - 4, panel_top, x + max_width, panel_bottom),
            radius=8,
            fill=panel_fill,
            outline=panel_line,
            width=1,
        )

        marker_color = self._color("accent") if bullet_index == 1 else self._color("text")
        draw.line((x + 12, y + 13, x + 12, panel_bottom - 18), fill=marker_color, width=4)
        draw.text(
            (x + 24, y + 18),
            f"{bullet_index:02d}",
            font=marker_font,
            fill=self._color("accent") if bullet_index == 1 else self._color("muted"),
        )

        cursor_y = y
        for line, line_height in zip(lines, line_heights):
            draw.text((text_x, cursor_y), line, font=font, fill=self._color("text"))
            cursor_y += line_height + line_spacing
        return panel_bottom

    def _draw_kicker(self, draw: ImageDraw.ImageDraw, x: int, y: int, text: str) -> None:
        draw.text((x, y), text, font=self.fonts.bold(26), fill=self._color("accent"))
        bbox = draw.textbbox((x, y), text, font=self.fonts.bold(26))
        draw.line((bbox[2] + 28, y + 17, self.width - self.margin, y + 17), fill=self._color("hairline"), width=2)

    def _draw_system_marks(self, draw: ImageDraw.ImageDraw, index: int, total: int) -> None:
        return None

    def _draw_footer(self, draw: ImageDraw.ImageDraw) -> None:
        footer_y = self.height - 174
        draw.line((self.margin, footer_y, self.width - self.margin, footer_y), fill=self._color("hairline"), width=2)
        brand_font = self.fonts.bold(30)
        bbox = draw.textbbox((0, 0), self.brand, font=brand_font)
        draw.text(
            ((self.width - (bbox[2] - bbox[0])) / 2, footer_y + 52),
            self.brand,
            font=brand_font,
            fill=self._color("text"),
        )

    def _draw_wrapped_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        xy: tuple[int, int],
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        fill: tuple[int, int, int],
        max_width: int,
        line_spacing: int,
    ) -> int:
        x, y = xy
        lines = self._wrap_text(draw, text, font, max_width)
        for line in lines:
            draw.text((x, y), line, font=font, fill=fill)
            bbox = draw.textbbox((x, y), line or " ", font=font)
            y += (bbox[3] - bbox[1]) + line_spacing
        return y

    def _draw_lines_text(
        self,
        draw: ImageDraw.ImageDraw,
        lines: list[str],
        xy: tuple[int, int],
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        fill: tuple[int, int, int],
        line_spacing: int,
    ) -> int:
        x, y = xy
        for line in lines:
            draw.text((x, y), line, font=font, fill=fill)
            bbox = draw.textbbox((x, y), line or " ", font=font)
            y += (bbox[3] - bbox[1]) + line_spacing
        return y

    def _wrap_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        max_width: int,
    ) -> list[str]:
        result: list[str] = []
        for paragraph in str(text).splitlines() or [""]:
            tokens = self._tokenize(paragraph)
            line = ""
            for token in tokens:
                candidate = f"{line}{token}" if line else token.lstrip()
                if draw.textlength(candidate, font=font) <= max_width:
                    line = candidate
                    continue
                if line:
                    result.append(line.rstrip())
                line = token.lstrip()
                while draw.textlength(line, font=font) > max_width and len(line) > 1:
                    cut = self._find_cut(draw, line, font, max_width)
                    result.append(line[:cut].rstrip())
                    line = line[cut:].lstrip()
            if line:
                result.append(line.rstrip())
        return result

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        tokens: list[str] = []
        buffer = ""
        for char in text:
            if char.isspace():
                if buffer:
                    tokens.append(buffer)
                    buffer = ""
                tokens.append(" ")
            elif ord(char) < 128:
                buffer += char
            else:
                if buffer:
                    tokens.append(buffer)
                    buffer = ""
                tokens.append(char)
        if buffer:
            tokens.append(buffer)
        return tokens

    @staticmethod
    def _find_cut(
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        max_width: int,
    ) -> int:
        for index in range(1, len(text) + 1):
            if draw.textlength(text[:index], font=font) > max_width:
                return max(1, index - 1)
        return len(text)

    @staticmethod
    def _normalize_bullets(card: dict) -> list[str]:
        bullets = card.get("bullets")
        if bullets is None:
            body = card.get("body", "")
            if isinstance(body, list):
                bullets = body
            else:
                bullets = [part.strip() for part in str(body).split("\n") if part.strip()]
        if isinstance(bullets, str):
            bullets = [part.strip() for part in bullets.split("\n") if part.strip()]
        return [str(item).strip() for item in bullets if str(item).strip()]

    @staticmethod
    def _resize_cover(image: Image.Image, width: int, height: int) -> Image.Image:
        ratio = max(width / image.width, height / image.height)
        resized = image.resize((int(image.width * ratio), int(image.height * ratio)), Image.Resampling.LANCZOS)
        left = (resized.width - width) // 2
        top = (resized.height - height) // 2
        return resized.crop((left, top, left + width, top + height)).filter(ImageFilter.SMOOTH)

    def _color(self, name: str) -> tuple[int, int, int]:
        defaults = {
            "background": "#F7F7F5",
            "text": "#111111",
            "muted": "#6D7178",
            "accent": "#1F6FFF",
            "hairline": "#DADDE2",
            "grid": "#ECEEF2",
            "ghost": "#E6E9EF",
        }
        active_style = getattr(self, "_active_visual_style", {}) or {}
        if name == "accent" and active_style.get("accent_color"):
            return hex_to_rgb(str(active_style["accent_color"]))
        if name == "ghost" and active_style.get("support_color"):
            return hex_to_rgb(str(active_style["support_color"]))
        return hex_to_rgb(self.colors.get(name, defaults[name]))

    @staticmethod
    def _mix(
        foreground: tuple[int, int, int],
        background: tuple[int, int, int],
        amount: float,
    ) -> tuple[int, int, int]:
        amount = max(0.0, min(1.0, amount))
        return tuple(round(background[i] + (foreground[i] - background[i]) * amount) for i in range(3))
