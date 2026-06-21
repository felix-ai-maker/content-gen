"""核心纯逻辑 + 兜底路径的回归测试（零外部依赖、不联网、不调 LLM）。

运行：
    cd xhs-agent-lab && ./.venv/bin/python -m unittest discover -s tests
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import copy_writer
import copy_pipeline
import creative_director
import pipeline
import style_director


def _write_json(tmp: Path, payload: dict) -> Path:
    path = tmp / "playbook.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


class PlaybookSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def _doc(self):
        return {
            "active": "b",
            "playbooks": [
                {"id": "a", "name": "甲", "categories": [{"key": "k", "name": "标题", "enabled": True, "tactics": ["招甲"]}]},
                {"id": "b", "name": "乙", "categories": [{"key": "k", "name": "标题", "enabled": True, "tactics": ["招乙"]}]},
            ],
        }

    def test_pick_by_id(self):
        self.assertEqual(copy_writer._pick_playbook(self._doc(), "a")["name"], "甲")

    def test_pick_uses_active_when_no_id(self):
        self.assertEqual(copy_writer._pick_playbook(self._doc(), "")["name"], "乙")

    def test_pick_falls_back_to_first_when_active_missing(self):
        doc = self._doc()
        doc["active"] = "zzz"
        self.assertEqual(copy_writer._pick_playbook(doc, "")["name"], "甲")

    def test_build_text_selects_playbook(self):
        path = _write_json(self.tmp, self._doc())
        self.assertIn("招甲", copy_writer._build_playbook_text(path, "a"))
        self.assertIn("招乙", copy_writer._build_playbook_text(path, "b"))

    def test_disabled_category_excluded(self):
        doc = self._doc()
        doc["playbooks"][0]["categories"][0]["enabled"] = False
        path = _write_json(self.tmp, doc)
        self.assertNotIn("招甲", copy_writer._build_playbook_text(path, "a"))

    def test_legacy_categories_structure(self):
        path = _write_json(self.tmp, {"categories": [{"name": "标题", "enabled": True, "tactics": ["老招"]}]})
        self.assertIn("老招", copy_writer._build_playbook_text(path, ""))

    def test_missing_file_falls_back_to_default(self):
        self.assertEqual(copy_writer._build_playbook_text(self.tmp / "nope.json", ""), copy_writer._DEFAULT_PLAYBOOK)

    def test_empty_playbook_falls_back(self):
        path = _write_json(self.tmp, {"active": "a", "playbooks": [{"id": "a", "name": "x", "categories": []}]})
        self.assertEqual(copy_writer._build_playbook_text(path, "a"), copy_writer._DEFAULT_PLAYBOOK)


class CopyWriterParseTests(unittest.TestCase):
    def test_normalize_bullets_from_string(self):
        self.assertEqual(copy_writer._normalize_bullets("a\n\nb\nc"), ["a", "b", "c"])

    def test_normalize_bullets_caps_at_three(self):
        self.assertEqual(len(copy_writer._normalize_bullets(["1", "2", "3", "4"])), 3)

    def test_parse_cards_requires_seven(self):
        self.assertIsNone(copy_writer._parse_cards(json.dumps([{"title": "x"}]), "brand"))

    def test_parse_cards_ok(self):
        data = [{"title": "封面"}] + [{"title": f"页{i}", "bullets": ["a", "b"]} for i in range(6)]
        cards = copy_writer._parse_cards(json.dumps(data), "实验室")
        self.assertEqual(len(cards), 7)
        self.assertEqual(cards[0]["type"], "cover")
        self.assertEqual(cards[1]["type"], "content")

    def test_extract_json_strips_fence(self):
        self.assertEqual(copy_writer._extract_json('```json\n{"a":1}\n```', "{", "}"), {"a": 1})

    def test_generate_cards_returns_none_without_llm(self):
        self.assertIsNone(copy_writer.generate_cards("选题", "", {"copy_llm": {"enabled": False}}, "brand"))


class StyleDirectorTests(unittest.TestCase):
    PRESETS = {
        "presets": {
            "alpha": {"name": "甲", "match_keywords": ["复盘", "判断"], "art_direction": "x"},
            "beta": {"name": "乙", "match_keywords": ["国风"], "art_direction": "y"},
        }
    }

    def _cfg(self):
        return {"style_presets": self.PRESETS["presets"], "image_model": {"default_style": "beta"}}

    def test_override_respected(self):
        self.assertEqual(style_director.resolve_style_preset("t", "", self._cfg(), override="alpha")["key"], "alpha")

    def test_invalid_override_raises(self):
        with self.assertRaises(ValueError):
            style_director.resolve_style_preset("t", "", self._cfg(), override="zzz")

    def test_keyword_matching(self):
        preset = style_director.resolve_style_preset("如何做复盘和判断", "复盘", self._cfg())
        self.assertEqual(preset["key"], "alpha")

    def test_no_match_uses_default(self):
        preset = style_director.resolve_style_preset("毫不相干的题目", "", self._cfg())
        self.assertEqual(preset["key"], "beta")

    def test_no_presets_returns_builtin_default(self):
        preset = style_director.resolve_style_preset("t", "", {})
        self.assertEqual(preset["key"], style_director.DEFAULT_STYLE_PRESET["key"])

    def test_choose_profile_defaults_to_knowledge(self):
        key, _ = style_director._choose_profile("毫无关键词的纯文本")
        self.assertEqual(key, "knowledge_note")

    def test_as_keyword_list_handles_flow_string(self):
        self.assertEqual(style_director._as_keyword_list('["a", "b"]'), ["a", "b"])

    def test_choose_style_preset_none_without_llm(self):
        self.assertIsNone(creative_director.choose_style_preset("t", "", [], self.PRESETS["presets"], None))


class CopyPipelineTests(unittest.TestCase):
    def test_remove_ai_smell(self):
        self.assertNotIn("赋能", copy_pipeline.remove_ai_smell("用 AI 赋能内容"))

    def test_extract_sentences_filters_length(self):
        out = copy_pipeline.extract_sentences("太短。这是一句长度刚好合适的话用来测试提取逻辑是否工作。")
        self.assertTrue(all(8 <= len(s) <= 72 for s in out))

    def test_polish_bullet_adds_period(self):
        self.assertTrue(copy_pipeline.polish_bullet("没有句号").endswith("。"))

    def test_make_cover_title_truncates(self):
        self.assertLessEqual(len(copy_pipeline.make_cover_title("一" * 40)), 24)

    def test_build_cards_from_copy_returns_seven(self):
        cards = copy_pipeline.build_cards_from_copy("交易复盘", "我在复盘交易，记录证据和反证。", {})
        self.assertEqual(len(cards), 7)
        self.assertEqual(cards[0]["type"], "cover")


class PipelineHelperTests(unittest.TestCase):
    def test_safe_topic_name_strips_illegal(self):
        self.assertNotIn("/", pipeline.safe_topic_name("a/b:c?*"))

    def test_safe_topic_name_fallback(self):
        self.assertEqual(pipeline.safe_topic_name("///"), "未命名选题")

    def test_load_simple_yaml_basic(self):
        data = pipeline.load_simple_yaml("a: 1\nb:\n  c: hi\n")
        self.assertEqual(data["a"], 1)
        self.assertEqual(data["b"]["c"], "hi")

    def test_parse_yaml_scalar_types(self):
        self.assertEqual(pipeline.parse_yaml_scalar("true"), True)
        self.assertEqual(pipeline.parse_yaml_scalar("42"), 42)
        self.assertEqual(pipeline.parse_yaml_scalar('"x"'), "x")


class CreativeDirectorParseTests(unittest.TestCase):
    def test_parse_briefs_count_mismatch(self):
        self.assertIsNone(creative_director._parse_briefs(json.dumps([{"index": 1, "metaphor": "m"}]), expected=2))

    def test_parse_briefs_ok(self):
        briefs = creative_director._parse_briefs(json.dumps([{"index": 1, "metaphor": "m"}]), expected=1)
        self.assertEqual(briefs[0]["metaphor"], "m")

    def test_parse_briefs_rejects_empty_metaphor(self):
        self.assertIsNone(creative_director._parse_briefs(json.dumps([{"index": 1, "metaphor": ""}]), expected=1))


class AppHelperTests(unittest.TestCase):
    def setUp(self):
        import webapp.app as app

        self.app = app

    def test_normalize_categories_drops_empty_tactics(self):
        cats = self.app._normalize_categories([{"name": "x", "tactics": ["a", "", "  "]}])
        self.assertEqual(cats[0]["tactics"], ["a"])

    def test_normalize_playbooks_assigns_ids(self):
        pbs = self.app._normalize_playbooks([{"name": "无id打法", "categories": []}])
        self.assertTrue(pbs[0]["id"])

    def test_mask_secret(self):
        self.assertEqual(self.app._mask_secret("sk-1234567890abcd"), "sk-1····abcd")
        self.assertEqual(self.app._mask_secret(""), "")

    def test_extract_json_array(self):
        self.assertEqual(self.app._extract_json_array('```json\n[{"x":1}]\n```'), [{"x": 1}])
        self.assertEqual(self.app._extract_json_array("not json"), [])


if __name__ == "__main__":
    unittest.main()
