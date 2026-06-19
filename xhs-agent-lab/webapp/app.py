"""本地 Web App 后端：把 pipeline.generate_package 包成浏览器界面用的 API。

启动：
    uvicorn webapp.app:app --port 8765
然后浏览器打开 http://localhost:8765
"""
from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pipeline import PROJECT_ROOT, generate_package, load_config, maybe_push_telegram, regenerate_card

DIST = PROJECT_ROOT / "dist"
STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="内容生成系统")

# 内存任务表（自用单机够）：task_id -> {status, logs, result, error}
TASKS: dict[str, dict] = {}


class GenerateRequest(BaseModel):
    topic: str
    copy_text: str = ""
    style: Optional[str] = None
    mode: str = "local"  # local=草稿省配额 / direct=正式整卡 / background=本地排版+AI背景
    extra_brief: str = ""
    push: bool = False


class RegenRequest(BaseModel):
    extra_brief: str = ""


def _run_task(task_id: str, params: dict) -> None:
    task = TASKS[task_id]
    task["status"] = "running"

    def log(msg: object) -> None:
        task["logs"].append(str(msg))

    try:
        task["result"] = generate_package(log=log, **params)
        task["status"] = "done"
    except Exception as exc:  # noqa: BLE001 - 回传给前端展示
        task["error"] = str(exc)
        task["status"] = "error"
        task["logs"].append(f"出错：{exc}")


def _safe_pkg(name: str) -> Path:
    pkg = (DIST / name).resolve()
    if DIST.resolve() not in pkg.parents or not pkg.is_dir():
        raise HTTPException(status_code=404, detail="发布包不存在")
    return pkg


@app.get("/api/presets")
def api_presets() -> list[dict]:
    config = load_config(PROJECT_ROOT / "config.yaml")
    presets = config.get("style_presets") or {}
    default = (config.get("image_model") or {}).get("default_style")
    items = [{"key": k, "name": (v or {}).get("name", k)} for k, v in presets.items()]
    return [{"key": "", "name": f"自动选择（默认 {default or '—'}）"}, *items]


@app.post("/api/generate")
def api_generate(req: GenerateRequest) -> dict:
    if not req.topic.strip():
        raise HTTPException(status_code=400, detail="选题不能为空")
    task_id = uuid.uuid4().hex[:12]
    TASKS[task_id] = {"status": "pending", "logs": [], "result": None, "error": None}
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


@app.get("/api/packages/{name}/inputs")
def api_inputs(name: str) -> dict:
    pkg = _safe_pkg(name)
    path = pkg / "inputs.json"
    if not path.exists():
        return {}
    import json as _json

    return _json.loads(path.read_text(encoding="utf-8"))


@app.get("/api/prompts")
def api_prompts() -> list[dict]:
    """全局提示词日志（最近在前），供复盘优化。"""
    import json as _json

    log_path = PROJECT_ROOT / "prompts_log.jsonl"
    if not log_path.exists():
        return []
    rows = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(_json.loads(line))
            except ValueError:
                continue
    return list(reversed(rows))


@app.post("/api/packages/{name}/cards/{index}/regenerate")
def api_regenerate(name: str, index: int, req: RegenRequest) -> dict:
    _safe_pkg(name)  # 校验包存在
    task_id = uuid.uuid4().hex[:12]
    TASKS[task_id] = {"status": "pending", "logs": [], "result": None, "error": None}

    def run() -> None:
        task = TASKS[task_id]
        task["status"] = "running"

        def log(msg: object) -> None:
            task["logs"].append(str(msg))

        try:
            task["result"] = regenerate_card(name, index, extra_brief=req.extra_brief, log=log)
            task["status"] = "done"
        except Exception as exc:  # noqa: BLE001 - 回传给前端
            task["error"] = str(exc)
            task["status"] = "error"
            task["logs"].append(f"出错：{exc}")

    threading.Thread(target=run, daemon=True).start()
    return {"task_id": task_id}


@app.post("/api/packages/{name}/push")
def api_push(name: str) -> dict:
    pkg = _safe_pkg(name)
    config = load_config(PROJECT_ROOT / "config.yaml")
    logs: list[str] = []
    pushed = maybe_push_telegram(config, pkg, no_push=False, log=logs.append)
    return {"pushed": pushed, "logs": logs}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
