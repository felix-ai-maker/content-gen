"""招式库（分类多打法）+ 拆解存档的持久化与规范化。

从 webapp/app.py 抽出，纯数据/IO，不依赖 FastAPI app，便于复用与测试。
"""
from __future__ import annotations

import json
import uuid

from pipeline import PROJECT_ROOT

PLAYBOOK_PATH = PROJECT_ROOT / "playbook.json"
TEARDOWNS_PATH = PROJECT_ROOT / "teardowns.jsonl"

DEFAULT_CATEGORIES = [
    ("base", "基础准则"),
    ("title", "标题钩子"),
    ("emotion", "情绪驱动"),
    ("save", "收藏动机"),
    ("interact", "互动评论"),
    ("social", "社交货币 / 转发"),
    ("structure", "结构节奏"),
    ("topic", "选题时机"),
]


def read_teardowns() -> list[dict]:
    if not TEARDOWNS_PATH.exists():
        return []
    rows: list[dict] = []
    for line in TEARDOWNS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return list(reversed(rows))  # 最近在前


def default_categories() -> list[dict]:
    return [{"key": k, "name": n, "enabled": True, "tactics": []} for k, n in DEFAULT_CATEGORIES]


def default_playbook_doc() -> dict:
    return {
        "active": "default",
        "playbooks": [{"id": "default", "name": "默认打法", "desc": "通用爆款准则。", "categories": default_categories()}],
    }


def load_playbook() -> dict:
    """统一返回新结构 {active, playbooks:[{id,name,desc,categories}]}；兼容旧 {categories}。"""
    if PLAYBOOK_PATH.exists():
        try:
            data = json.loads(PLAYBOOK_PATH.read_text(encoding="utf-8"))
        except ValueError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("playbooks"), list) and data["playbooks"]:
            return data
        if isinstance(data, dict) and isinstance(data.get("categories"), list):
            return {
                "active": "default",
                "playbooks": [{"id": "default", "name": "默认打法", "desc": "", "categories": data["categories"]}],
            }
    return default_playbook_doc()


def normalize_categories(raw: list) -> list[dict]:
    cats: list[dict] = []
    seen: set[str] = set()
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip() or uuid.uuid4().hex[:6]
        while key in seen:
            key = uuid.uuid4().hex[:6]
        seen.add(key)
        cats.append(
            {
                "key": key,
                "name": str(item.get("name") or "未命名").strip(),
                "enabled": bool(item.get("enabled", True)),
                "tactics": [str(t).strip() for t in (item.get("tactics") or []) if str(t).strip()],
            }
        )
    return cats


def normalize_playbooks(raw: list) -> list[dict]:
    pbs: list[dict] = []
    seen: set[str] = set()
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("id") or "").strip() or uuid.uuid4().hex[:8]
        while pid in seen:
            pid = uuid.uuid4().hex[:8]
        seen.add(pid)
        pbs.append(
            {
                "id": pid,
                "name": str(item.get("name") or "未命名打法").strip(),
                "desc": str(item.get("desc") or "").strip(),
                "categories": normalize_categories(item.get("categories") or []),
            }
        )
    return pbs


def save_playbook(data: dict) -> None:
    PLAYBOOK_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def extract_json_array(content: str) -> list:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return []
    return data if isinstance(data, list) else []
