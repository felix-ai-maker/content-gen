"""本地 Web App 后端：把 pipeline.generate_package 包成浏览器界面用的 API。

启动：
    uvicorn webapp.app:app --port 8765
然后浏览器打开 http://localhost:8765
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import urllib.error
import uuid
from pathlib import Path
from typing import Optional

from creative_director import _post_chat

from dotenv import dotenv_values, set_key, unset_key
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pipeline import (
    PROJECT_ROOT,
    build_publish_review,
    generate_package,
    load_config,
    maybe_push_telegram,
    rebuild_package,
    regenerate_card,
    update_package_cards,
)

DIST = PROJECT_ROOT / "dist"
STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="内容生成系统")

# 内存任务表（自用单机够）：task_id -> {status, logs, result, error}
TASKS: dict[str, dict] = {}
MAX_TASKS = 60  # 任务表上限，超出后清理最旧的已完成/出错任务，避免长期运行内存无限涨。


def _gc_tasks() -> None:
    if len(TASKS) <= MAX_TASKS:
        return
    # dict 保持插入顺序，最旧的在前；只清理已结束的任务，不动 running/pending。
    removable = [tid for tid, task in TASKS.items() if task.get("status") in {"done", "error"}]
    for tid in removable[: max(0, len(TASKS) - MAX_TASKS)]:
        TASKS.pop(tid, None)


# config.yaml 按 mtime 缓存，省掉每个请求重复读盘 + YAML 解析。
# 注意：app.py 里的调用方都只读取 config，不做修改，可安全共享同一份。
_CONFIG_CACHE: dict = {}


def _config() -> dict:
    path = PROJECT_ROOT / "config.yaml"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return load_config(path)
    if _CONFIG_CACHE.get("mtime") != mtime:
        _CONFIG_CACHE["mtime"] = mtime
        _CONFIG_CACHE["data"] = load_config(path)
    return _CONFIG_CACHE["data"]


# --------------------------------------------------------------------------- #
# 密钥设置：持久化到 .env，浏览器里填一次永久保存、即时生效，免得每次开会话重 export。
# --------------------------------------------------------------------------- #
ENV_PATH = PROJECT_ROOT / ".env"

SETTINGS_KEYS = [
    {"key": "DEEPSEEK_API_KEY", "label": "DeepSeek API Key", "hint": "卡片文案、图文正文、选题灵感的文本大模型"},
    {"key": "GOOGLE_API_KEY", "label": "Google / Vertex API Key", "hint": "Nano Banana 图像模型出图"},
    {"key": "GOOGLE_CLOUD_PROJECT", "label": "GCP 项目 ID（可选）", "hint": "用 Vertex 完整身份认证时填，与上面的 API Key 二选一"},
    {"key": "TELEGRAM_BOT_TOKEN", "label": "Telegram Bot Token（可选）", "hint": "生成后推送到手机"},
    {"key": "TELEGRAM_CHAT_ID", "label": "Telegram Chat ID（可选）", "hint": "生成后推送到手机"},
]


def _mask_secret(value: str) -> str:
    v = str(value or "").strip()
    if not v:
        return ""
    if len(v) <= 4:
        return "····"
    return f"{v[:4]}····{v[-4:]}"


def _settings_snapshot() -> dict:
    file_vals = dotenv_values(ENV_PATH) if ENV_PATH.exists() else {}
    items = []
    for meta in SETTINGS_KEYS:
        key = meta["key"]
        value = os.getenv(key) or file_vals.get(key) or ""
        items.append({**meta, "set": bool(value), "preview": _mask_secret(value)})
    return {"keys": items, "env_path": str(ENV_PATH)}


class SettingsUpdate(BaseModel):
    values: dict[str, str]


@app.get("/api/settings")
def api_get_settings() -> dict:
    return _settings_snapshot()


@app.put("/api/settings")
def api_put_settings(req: SettingsUpdate) -> dict:
    """把密钥写入 .env 并即时更新本进程环境变量（无需重启）。留空表示删除该项。"""
    allowed = {meta["key"] for meta in SETTINGS_KEYS}
    ENV_PATH.touch(exist_ok=True)
    for key, raw in (req.values or {}).items():
        if key not in allowed:
            continue
        value = str(raw or "").strip()
        if value:
            set_key(str(ENV_PATH), key, value)
            os.environ[key] = value
        else:
            unset_key(str(ENV_PATH), key)
            os.environ.pop(key, None)
    return _settings_snapshot()


# --------------------------------------------------------------------------- #
# 爆款研究：拆解（analyzer skill 提示词）+ 搜索（openclaw MCP skill 脚本）
# --------------------------------------------------------------------------- #
SKILLS_DIR = PROJECT_ROOT.parent / ".agents" / "skills"
ANALYZER_SKILL_MD = SKILLS_DIR / "xiaohongshu-note-analyzer" / "SKILL.md"
XHS_SCRIPTS = SKILLS_DIR / "xiaohongshu" / "scripts"
_ANALYZER_FRAMEWORK: dict = {}


def _analyzer_framework() -> str:
    try:
        mtime = ANALYZER_SKILL_MD.stat().st_mtime
    except OSError:
        return ""
    if _ANALYZER_FRAMEWORK.get("mtime") != mtime:
        _ANALYZER_FRAMEWORK["mtime"] = mtime
        _ANALYZER_FRAMEWORK["text"] = ANALYZER_SKILL_MD.read_text(encoding="utf-8")
    return _ANALYZER_FRAMEWORK.get("text", "")


def _llm_for(config: dict) -> tuple[dict, str]:
    """取文本 LLM 配置 + key；缺任一项抛 HTTPException，前端能给出明确指引。"""
    llm = config.get("copy_llm") or config.get("creative_llm") or {}
    if not llm.get("enabled"):
        raise HTTPException(status_code=400, detail="未启用文本大模型（config 的 copy_llm/creative_llm）。")
    api_key = os.getenv(str(llm.get("api_key_env", "DEEPSEEK_API_KEY")))
    if not api_key:
        raise HTTPException(status_code=400, detail="未配置 DeepSeek API Key，先点右上「⚙️ 密钥设置」填一下。")
    return llm, api_key


class AnalyzeRequest(BaseModel):
    text: str


@app.post("/api/analyze")
def api_analyze(req: AnalyzeRequest) -> dict:
    """把一篇爆款笔记按 analyzer skill 的框架做 6 维拆解。"""
    text = (req.text or "").strip()
    if len(text) < 10:
        raise HTTPException(status_code=400, detail="先粘贴一篇小红书笔记内容（标题 + 正文 + 标签）。")
    framework = _analyzer_framework()
    if not framework:
        raise HTTPException(status_code=404, detail="未找到 analyzer skill（.agents/skills/xiaohongshu-note-analyzer）。")
    llm, api_key = _llm_for(_config())
    system = "你是资深小红书内容分析师。严格按用户给的分析框架，对笔记做全维度拆解，输出框架要求的 Markdown 报告，结论具体、可执行。"
    user = f"【分析框架】\n{framework}\n\n【待分析的小红书笔记】\n{text}\n\n请严格按框架的「输出格式」产出完整分析报告。"
    try:
        report = _post_chat(
            base_url=str(llm.get("base_url", "https://api.deepseek.com")),
            api_key=api_key,
            payload={
                "model": llm.get("model", "deepseek-chat"),
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "temperature": 0.4,
                "stream": False,
            },
            timeout=float(llm.get("timeout_seconds", 60)),
        )
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"分析失败：{exc}") from exc
    return {"report": report}


def _run_xhs(script: str, *args: str, timeout: float = 40) -> dict:
    """调用 openclaw-xhs skill 自带脚本（复用其 MCP 封装，不自己实现协议）。"""
    path = XHS_SCRIPTS / script
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"未找到小红书 skill 脚本：{script}（先安装 openclaw-xhs skill）。")
    try:
        proc = subprocess.run(
            ["bash", str(path), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(XHS_SCRIPTS),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": "", "error": "调用超时，确认 MCP 服务已启动（scripts/start-mcp.sh）。"}
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    ok = proc.returncode == 0 and "错误" not in out and "Connection refused" not in (out + err)
    return {"ok": ok, "output": out, "error": err}


def _mcp_port() -> int:
    url = os.getenv("MCP_URL", "")
    match = re.search(r":(\d+)", url)
    return int(match.group(1)) if match else 18060


def _mcp_running() -> bool:
    import socket

    try:
        with socket.create_connection(("127.0.0.1", _mcp_port()), timeout=1.0):
            return True
    except OSError:
        return False


@app.get("/api/xhs/status")
def api_xhs_status() -> dict:
    """MCP 是否在跑 + 是否已登录。以端口监听为权威信号，未启动时不报错，回明确状态供前端引导。"""
    if not XHS_SCRIPTS.exists():
        return {"installed": False, "running": False, "logged_in": False, "message": "未安装 openclaw-xhs skill。"}
    if not _mcp_running():
        return {"installed": True, "running": False, "logged_in": False, "message": f"MCP 服务（端口 {_mcp_port()}）未监听。"}
    res = _run_xhs("status.sh", timeout=15)
    text = res["output"] + res["error"]
    logged_in = res["ok"] and ("已登录" in text or '"true"' in text.lower() or "logged_in" in text.lower())
    return {"installed": True, "running": True, "logged_in": logged_in, "message": res["output"] or res["error"]}


class XhsSearchRequest(BaseModel):
    keyword: str


@app.post("/api/xhs/search")
def api_xhs_search(req: XhsSearchRequest) -> dict:
    keyword = (req.keyword or "").strip()
    if not keyword:
        raise HTTPException(status_code=400, detail="先输入要搜的关键词。")
    if not _mcp_running():
        raise HTTPException(status_code=503, detail=f"MCP 服务（端口 {_mcp_port()}）未启动，先在终端跑 scripts/start-mcp.sh。")
    res = _run_xhs("search.sh", keyword, timeout=60)
    if not res["ok"]:
        raise HTTPException(status_code=502, detail=res["error"] or res["output"] or "搜索失败，确认 MCP 已启动并登录。")
    return {"keyword": keyword, "output": res["output"]}


@app.post("/api/xhs/login-qr")
def api_xhs_login_qr() -> dict:
    res = _run_xhs("mcp-call.sh", "get_login_qrcode", timeout=30)
    return {"ok": res["ok"], "output": res["output"] or res["error"]}


def _initial_progress(message: str = "等待开始…") -> dict:
    return {"stage": "pending", "percent": 0, "message": message, "done": 0, "total": 0}


def _new_task(message: str = "等待开始…") -> dict:
    return {"status": "pending", "logs": [], "result": None, "error": None, "progress": _initial_progress(message)}


class GenerateRequest(BaseModel):
    topic: str
    copy_text: str = ""
    style: Optional[str] = None
    mode: str = "background"  # background=固定排版正式出图 / local=草稿省配额 / direct=实验整卡
    extra_brief: str = ""
    push: bool = False


class InspireRequest(BaseModel):
    target: str = "both"  # topic | copy | both
    direction: str = ""
    topic: str = ""
    copy_text: str = ""


class RegenRequest(BaseModel):
    extra_brief: str = ""
    metaphor: str = ""


class CardsUpdateRequest(BaseModel):
    cards: list[dict]


class DocsUpdateRequest(BaseModel):
    xhs: Optional[str] = None
    wechat: Optional[str] = None


class RerenderRequest(BaseModel):
    mode: str = "background"
    extra_brief: str = ""
    refresh_style: bool = False
    push: bool = False


def _run_task(task_id: str, params: dict) -> None:
    task = TASKS[task_id]
    task["status"] = "running"
    task["progress"] = _initial_progress("任务已开始…")

    def log(msg: object) -> None:
        task["logs"].append(str(msg))

    def progress(payload: dict) -> None:
        task["progress"] = {**task.get("progress", {}), **payload}

    try:
        task["result"] = generate_package(log=log, progress=progress, **params)
        task["status"] = "done"
        task["progress"] = {**task.get("progress", {}), "stage": "done", "percent": 100, "message": "生成完成。"}
    except Exception as exc:  # noqa: BLE001 - 回传给前端展示
        task["error"] = str(exc)
        task["status"] = "error"
        task["progress"] = {**task.get("progress", {}), "stage": "error", "message": str(exc)}
        task["logs"].append(f"出错：{exc}")


def _safe_pkg(name: str) -> Path:
    pkg = (DIST / name).resolve()
    if DIST.resolve() not in pkg.parents or not pkg.is_dir():
        raise HTTPException(status_code=404, detail="发布包不存在")
    return pkg


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 历史文件坏了也不要让页面空掉
        return {}
    return data if isinstance(data, dict) else {}


def _topic_from_package(pkg: Path, name: str) -> str:
    checklist = pkg / "发布清单.md"
    if checklist.exists():
        first_line = checklist.read_text(encoding="utf-8", errors="ignore").splitlines()[0:1]
        if first_line and first_line[0].startswith("# 发布清单："):
            return first_line[0].replace("# 发布清单：", "", 1).strip()
    return re.sub(r"^\d{4}-\d{2}-\d{2}_", "", name).strip()


def _normalize_mode(mode: object) -> str:
    value = str(mode or "").strip()
    if value in {"direct", "direct_full_card"}:
        return "direct"
    if value in {"background", "background_ai"}:
        return "background"
    return "local" if not value else value


def _recover_inputs(pkg: Path, name: str) -> dict:
    """兼容早期没有 inputs.json 的发布包，尽量恢复可复用的表单信息。"""
    style_plan = _read_json(pkg / "style_plan.json")
    preset = style_plan.get("style_preset") if isinstance(style_plan.get("style_preset"), dict) else {}
    meta = _read_json(pkg / "generation_meta.json")
    mode = meta.get("mode")
    if not mode and isinstance(meta.get("cards"), list) and meta["cards"]:
        first_card = meta["cards"][0] if isinstance(meta["cards"][0], dict) else {}
        mode = first_card.get("mode")
    return {
        "timestamp": "",
        "topic": _topic_from_package(pkg, name),
        "copy_text": "",
        "style": preset.get("key") or "",
        "mode": _normalize_mode(mode),
        "extra_brief": "",
        "push": False,
        "recovered": True,
        "recovered_notice": "这个历史包生成时还没有保存原始输入，已从发布包信息恢复可复用字段。",
    }


def _normalize_inputs(data: dict, pkg: Path, name: str) -> dict:
    return {
        **data,
        "topic": str(data.get("topic") or _topic_from_package(pkg, name)),
        "copy_text": str(data.get("copy_text") or ""),
        "style": str(data.get("style") or ""),
        "mode": _normalize_mode(data.get("mode")),
        "extra_brief": str(data.get("extra_brief") or ""),
        "push": bool(data.get("push", False)),
    }


def _inspiration_seed(topic: str, copy_text: str, direction: str = "") -> str:
    seed = "\n".join([direction.strip(), topic.strip(), copy_text.strip()]).strip()
    if not seed:
        return "交易 Agent、复盘、自动化、判断留痕"
    seed = re.sub(r"\s+", " ", seed.replace("#", " ")).strip()
    return seed[:260]


def _fallback_inspiration(topic: str, copy_text: str, direction: str = "") -> dict:
    seed = _inspiration_seed(topic, copy_text, direction)
    short = seed[:34].rstrip("，。；、 ")
    if len(seed) > 34:
        short += "…"
    subject = short or "这个想法"
    topics = [
        {
            "title": f"我为什么开始认真处理「{subject}」",
            "angle": "从一个具体痛点切入，解释为什么现在必须把它系统化。",
            "hook": "真正困住人的，往往不是工具不够多，而是每天重复出现的问题没有留下痕迹。",
        },
        {
            "title": f"「{subject}」真正卡人的，不是方法，是流程",
            "angle": "把注意力从工具/技巧转到流程、边界和复盘。",
            "hook": "方法越多，越需要一条能每天执行的路径。",
        },
        {
            "title": f"我用「{subject}」给自己加了一道刹车",
            "angle": "强调它如何降低冲动、减少临场拍脑袋。",
            "hook": "好的系统不是让人更快行动，而是让人行动前多看一眼证据。",
        },
        {
            "title": f"先别做大系统，先把「{subject}」跑起来",
            "angle": "适合做成小红书组图：从第一版、最小闭环、下一步迭代展开。",
            "hook": "第一版不追求宏大，只解决一个每天都会发生的小问题。",
        },
    ]
    materials = [
        {
            "title": "从问题出发的素材草稿",
            "draft": (
                f"我最近在想「{subject}」。真正让我卡住的不是想法本身，而是它每天都会重复出现，"
                "但复盘时又很难说清当时到底发生了什么。与其继续找更多工具，不如先把问题拆小："
                "它在哪个环节出现、触发了什么判断、最后有没有留下可回看的记录。"
            ),
            "bullets": ["先讲一个真实卡点", "再说旧做法为什么不稳定", "最后收束到一个小闭环"],
        },
        {
            "title": "从流程出发的素材草稿",
            "draft": (
                f"如果把「{subject}」当成一个流程，而不是一个灵感，它就会变得清楚很多。"
                "输入是什么，判断依据是什么，什么时候该停下来，哪些结果要被记录，下一次怎么复盘，"
                "这些问题比单次结果更重要。"
            ),
            "bullets": ["输入：今天关注什么", "判断：证据和反证分别是什么", "复盘：哪里失真，规则怎么改"],
        },
        {
            "title": "从反差出发的素材草稿",
            "draft": (
                f"很多人以为「{subject}」的价值是让人更快、更自动化。"
                "但我现在更在意相反的一面：它能不能让人慢下来，能不能把边界写清楚，"
                "能不能在冲动出现之前，把理由、风险和反证都摆到桌面上。"
            ),
            "bullets": ["先打破一个常见误解", "强调边界和责任", "给出下一步可执行动作"],
        },
    ]
    return {"source": "local", "topics": topics, "materials": materials}


def _parse_inspiration(content: str) -> dict | None:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    topics = data.get("topics")
    materials = data.get("materials")
    if not isinstance(topics, list) or not isinstance(materials, list):
        return None
    return {
        "source": "ai",
        "topics": [item for item in topics if isinstance(item, dict)][:5],
        "materials": [item for item in materials if isinstance(item, dict)][:4],
    }


def _ai_inspiration(topic: str, copy_text: str, target: str, direction: str, config: dict) -> dict | None:
    llm_cfg = config.get("creative_llm") or {}
    if not llm_cfg.get("enabled"):
        return None
    api_key = os.getenv(str(llm_cfg.get("api_key_env", "DEEPSEEK_API_KEY")))
    if not api_key:
        return None
    seed = _inspiration_seed(topic, copy_text, direction)
    system = (
        "你是一个内容选题教练，帮助创作者把关键词或一段模糊想法，变成可发布的小红书图文选题和素材草稿。"
        "你不写空泛鸡汤，不夸张承诺，不给投资建议。输出要具体、可执行、适合做 7 张图文卡。"
    )
    user = (
        f"用户输入目标：{target}\n"
        f"用户给灵感的关键词/方向：{direction.strip() or '未填写'}\n"
        f"用户已有选题：{topic.strip() or '未填写'}\n"
        f"用户已有素材/关键词：{copy_text.strip() or '未填写'}\n\n"
        "请基于上述内容给灵感。严格只输出 JSON 对象，格式如下：\n"
        "{\n"
        '  "topics": [{"title": "选题标题", "angle": "切入角度", "hook": "开头钩子"}],\n'
        '  "materials": [{"title": "素材方向", "draft": "180-260字中文素材草稿", "bullets": ["要点1","要点2","要点3"]}]\n'
        "}\n"
        "要求：topics 给 4 个，materials 给 3 个；标题不要超过 28 个中文字符；"
        "素材草稿要保留个人表达，不要像营销号；交易相关内容必须避免收益承诺，并保留风险/边界意识。\n\n"
        f"用户输入整理：{seed}"
    )
    payload = {
        "model": llm_cfg.get("model", "deepseek-chat"),
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": min(1.0, float(llm_cfg.get("temperature", 0.85))),
        "stream": False,
    }
    try:
        content = _post_chat(
            base_url=str(llm_cfg.get("base_url", "https://api.deepseek.com")),
            api_key=api_key,
            payload=payload,
            timeout=min(25.0, float(llm_cfg.get("timeout_seconds", 60))),
        )
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError):
        return None
    return _parse_inspiration(content)


@app.get("/api/presets")
def api_presets() -> list[dict]:
    config = _config()
    presets = config.get("style_presets") or {}
    default = (config.get("image_model") or {}).get("default_style")
    items = [{"key": k, "name": (v or {}).get("name", k)} for k, v in presets.items()]
    return [{"key": "", "name": f"自动选择（默认 {default or '—'}）"}, *items]


@app.post("/api/inspire")
def api_inspire(req: InspireRequest) -> dict:
    if not req.direction.strip() and not req.topic.strip() and not req.copy_text.strip():
        raise HTTPException(status_code=400, detail="先输入关键词、选题或简要素材")
    config = _config()
    ai_result = _ai_inspiration(req.topic, req.copy_text, req.target, req.direction, config)
    return ai_result or _fallback_inspiration(req.topic, req.copy_text, req.direction)


@app.post("/api/generate")
def api_generate(req: GenerateRequest) -> dict:
    if not req.topic.strip():
        raise HTTPException(status_code=400, detail="选题不能为空")
    task_id = uuid.uuid4().hex[:12]
    TASKS[task_id] = _new_task("已提交，等待生成…")
    _gc_tasks()
    params = {
        "topic": req.topic.strip(),
        "copy_text": req.copy_text,
        "style": req.style or None,
        "mode": req.mode,
        "extra_brief": req.extra_brief,
        "push": req.push,
    }
    threading.Thread(target=_run_task, args=(task_id, params), daemon=True).start()
    return {"task_id": task_id}


@app.get("/api/tasks/{task_id}")
def api_task(task_id: str) -> dict:
    task = TASKS.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@app.get("/api/packages")
def api_packages() -> list[dict]:
    if not DIST.exists():
        return []
    packages = sorted((p for p in DIST.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True)
    return [
        {"name": p.name, "cards": sorted(q.name for q in p.glob("card_*.png"))}
        for p in packages
    ]


@app.get("/api/packages/{name}/cards/{file}")
def api_card_image(name: str, file: str) -> FileResponse:
    pkg = _safe_pkg(name)
    img = (pkg / file).resolve()
    if pkg not in img.parents or not img.is_file():
        raise HTTPException(status_code=404, detail="图片不存在")
    return FileResponse(img)


@app.get("/api/packages/{name}/docs")
def api_docs(name: str) -> dict:
    pkg = _safe_pkg(name)

    def _read(doc: str) -> str:
        path = pkg / doc
        return path.read_text(encoding="utf-8") if path.exists() else ""

    return {"xhs": _read("小红书正文.md"), "wechat": _read("公众号文章.md")}


@app.put("/api/packages/{name}/docs")
def api_update_docs(name: str, req: DocsUpdateRequest) -> dict:
    pkg = _safe_pkg(name)
    if req.xhs is None and req.wechat is None:
        raise HTTPException(status_code=400, detail="没有可保存的文案")

    xhs_path = pkg / "小红书正文.md"
    wechat_path = pkg / "公众号文章.md"
    if req.xhs is not None:
        xhs_path.write_text(req.xhs, encoding="utf-8")
    if req.wechat is not None:
        wechat_path.write_text(req.wechat, encoding="utf-8")

    xhs = xhs_path.read_text(encoding="utf-8") if xhs_path.exists() else ""
    wechat = wechat_path.read_text(encoding="utf-8") if wechat_path.exists() else ""
    cards_path = pkg / "cards_used.json"
    payload = json.loads(cards_path.read_text(encoding="utf-8")) if cards_path.exists() else {}
    cards = payload.get("cards", payload) if isinstance(payload, dict) else payload
    cards = cards if isinstance(cards, list) else []
    inputs_path = pkg / "inputs.json"
    inputs = _normalize_inputs(_read_json(inputs_path) or _recover_inputs(pkg, name), pkg, name)
    inputs["docs_edited_from_web"] = True
    inputs_path.write_text(json.dumps(inputs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    review = build_publish_review(inputs["topic"], cards, xhs, wechat, _config())
    return {"xhs": xhs, "wechat": wechat, "review": review, "inputs": inputs}


@app.get("/api/packages/{name}/cards-data")
def api_cards_data(name: str) -> dict:
    pkg = _safe_pkg(name)

    path = pkg / "cards_used.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="卡片结构不存在")
    payload = json.loads(path.read_text(encoding="utf-8"))
    cards = payload.get("cards", payload) if isinstance(payload, dict) else payload
    return {"cards": cards}


@app.put("/api/packages/{name}/cards-data")
def api_update_cards_data(name: str, req: CardsUpdateRequest) -> dict:
    _safe_pkg(name)
    if not req.cards:
        raise HTTPException(status_code=400, detail="卡片不能为空")
    for index, card in enumerate(req.cards, start=1):
        if not isinstance(card, dict):
            raise HTTPException(status_code=400, detail=f"第 {index} 张卡格式不正确")
        if not str(card.get("title", "")).strip():
            raise HTTPException(status_code=400, detail=f"第 {index} 张卡缺少标题")
    try:
        return update_package_cards(name, req.cards)
    except Exception as exc:  # noqa: BLE001 - 回传给前端展示
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/packages/{name}/inputs")
def api_inputs(name: str) -> dict:
    pkg = _safe_pkg(name)
    path = pkg / "inputs.json"
    data = _read_json(path)
    if data:
        return _normalize_inputs(data, pkg, name)
    return _recover_inputs(pkg, name)


@app.get("/api/prompts")
def api_prompts() -> list[dict]:
    """全局提示词日志（最近在前），供复盘优化。"""

    log_path = PROJECT_ROOT / "prompts_log.jsonl"
    if not log_path.exists():
        return []
    rows = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return list(reversed(rows))


@app.get("/api/packages/{name}/cards/{index}/prompt")
def api_card_prompt(name: str, index: int) -> dict:
    """返回这张卡当前的画面提示词，供前端预填编辑。"""
    pkg = _safe_pkg(name)

    cards = json.loads((pkg / "cards_used.json").read_text(encoding="utf-8")).get("cards", [])
    if not 1 <= index <= len(cards):
        raise HTTPException(status_code=404, detail="卡片不存在")
    card = cards[index - 1]
    vs = card.get("visual_style", {}) or {}

    def _clean(value: object) -> str:
        return str(value or "").strip()

    bullets = card.get("bullets") or card.get("body") or []
    if isinstance(bullets, str):
        bullets = [item.strip() for item in bullets.splitlines() if item.strip()]
    if not isinstance(bullets, list):
        bullets = []

    text_parts = []
    for label, key in [
        ("标题", "title"),
        ("副标题", "subtitle"),
        ("核心判断", "highlight"),
        ("底部结论", "note"),
    ]:
        value = _clean(card.get(key))
        if value:
            text_parts.append(f"{label}：{value}")
    if bullets:
        text_parts.append("要点：" + " / ".join(_clean(item) for item in bullets[:3] if _clean(item)))

    parts = [
        "基于本页文字来生成画面，不要过度抽象。",
        "用户遮住文字只看图，也应该大致猜到这页在讲什么。",
        "",
        "本页文字依据：",
        *text_parts,
        "",
        "画面建议：",
    ]
    if vs.get("metaphor"):
        parts.append("主视觉：" + str(vs["metaphor"]))
    else:
        parts.append("主视觉：从标题和要点里提取最具体的物件、动作、流程或关系。")
    if vs.get("composition"):
        parts.append("构图：" + str(vs["composition"]))
    if vs.get("details"):
        parts.append("细节：" + str(vs["details"]))
    parts.append("约束：贴近本页文案；不要纯抽象符号；不要泛科技装置；不要股票/K线/机器人/芯片/代码屏。")
    return {"prompt": "\n".join(parts)}


@app.get("/api/packages/{name}/review")
def api_review(name: str) -> dict:
    pkg = _safe_pkg(name)

    cards_path = pkg / "cards_used.json"
    if not cards_path.exists():
        raise HTTPException(status_code=404, detail="卡片结构不存在")
    payload = json.loads(cards_path.read_text(encoding="utf-8"))
    cards = payload.get("cards", payload) if isinstance(payload, dict) else payload
    inputs_path = pkg / "inputs.json"
    inputs = json.loads(inputs_path.read_text(encoding="utf-8")) if inputs_path.exists() else {}
    topic = str(inputs.get("topic") or name)
    xhs = (pkg / "小红书正文.md").read_text(encoding="utf-8") if (pkg / "小红书正文.md").exists() else ""
    wechat = (pkg / "公众号文章.md").read_text(encoding="utf-8") if (pkg / "公众号文章.md").exists() else ""
    config = _config()
    return build_publish_review(topic, cards, xhs, wechat, config)


@app.post("/api/packages/{name}/cards/{index}/regenerate")
def api_regenerate(name: str, index: int, req: RegenRequest) -> dict:
    _safe_pkg(name)  # 校验包存在
    task_id = uuid.uuid4().hex[:12]
    TASKS[task_id] = _new_task(f"等待重生成第 {index} 张…")
    _gc_tasks()

    def run() -> None:
        task = TASKS[task_id]
        task["status"] = "running"
        task["progress"] = {"stage": "render", "percent": 5, "message": f"正在重生成第 {index} 张…", "done": 0, "total": 1}

        def log(msg: object) -> None:
            task["logs"].append(str(msg))

        try:
            task["result"] = regenerate_card(
                name, index, extra_brief=req.extra_brief, metaphor=req.metaphor, log=log
            )
            task["status"] = "done"
            task["progress"] = {"stage": "done", "percent": 100, "message": "单张图片已更新。", "done": 1, "total": 1}
        except Exception as exc:  # noqa: BLE001 - 回传给前端
            task["error"] = str(exc)
            task["status"] = "error"
            task["progress"] = {"stage": "error", "percent": 0, "message": str(exc), "done": 0, "total": 1}
            task["logs"].append(f"出错：{exc}")

    threading.Thread(target=run, daemon=True).start()
    return {"task_id": task_id}


@app.post("/api/packages/{name}/rerender")
def api_rerender(name: str, req: RerenderRequest) -> dict:
    _safe_pkg(name)
    if req.mode not in {"local", "direct", "background"}:
        raise HTTPException(status_code=400, detail="未知出图模式")
    task_id = uuid.uuid4().hex[:12]
    TASKS[task_id] = _new_task("已提交，等待重渲染…")
    _gc_tasks()

    def run() -> None:
        task = TASKS[task_id]
        task["status"] = "running"
        task["progress"] = _initial_progress("重渲染任务已开始…")

        def log(msg: object) -> None:
            task["logs"].append(str(msg))

        def progress(payload: dict) -> None:
            task["progress"] = {**task.get("progress", {}), **payload}

        try:
            task["result"] = rebuild_package(
                name,
                mode=req.mode,
                extra_brief=req.extra_brief,
                refresh_style=req.refresh_style,
                push=req.push,
                log=log,
                progress=progress,
            )
            task["status"] = "done"
            task["progress"] = {**task.get("progress", {}), "stage": "done", "percent": 100, "message": "更新完成。"}
        except Exception as exc:  # noqa: BLE001 - 回传给前端
            task["error"] = str(exc)
            task["status"] = "error"
            task["progress"] = {**task.get("progress", {}), "stage": "error", "message": str(exc)}
            task["logs"].append(f"出错：{exc}")

    threading.Thread(target=run, daemon=True).start()
    return {"task_id": task_id}


@app.post("/api/packages/{name}/push")
def api_push(name: str) -> dict:
    pkg = _safe_pkg(name)
    config = _config()
    logs: list[str] = []
    pushed = maybe_push_telegram(config, pkg, no_push=False, log=logs.append)
    return {"pushed": pushed, "logs": logs}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
