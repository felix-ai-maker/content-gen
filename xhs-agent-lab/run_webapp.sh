#!/bin/bash
# 启动本地内容生成系统，浏览器打开 http://localhost:8765
cd "$(dirname "$0")" || exit 1
exec .venv/bin/uvicorn webapp.app:app --host 127.0.0.1 --port 8765
