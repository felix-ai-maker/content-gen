# content-gen

一个本地的「小红书 + 公众号内容自动生产线」，可在 Mac 上作为 Web App 使用。

输入选题和素材，自动生成 **小红书正文 + 公众号文章 + 7 张配图卡片**，并能一键推送到 Telegram。

## 主要能力

- **AI 出图**：Nano Banana 2（Vertex Gemini 图像模型）直接生成整张成品卡，含中文排版与主题插画。
- **DeepSeek 创意层**：为每一页量身创意「视觉 brief」（隐喻 / 构图 / 细节），再交给图像模型；失败自动回退规则隐喻库。
- **8 套视觉风格模板**：杂志精致 / 爆款封面 / 暖色编辑 / 国潮东方 / 极简 ins / 柔和 3D / 胶片摄影 / 极简科技蓝；可按选题自动选或手动指定。
- **本地 Web App**：浏览器里生成、预览、**单张改提示词重生成**、文案一键复制、推送 Telegram、历史回看。
- **提示词记录**：每次输入自动存档（包内 `inputs.json` + 全局日志），供后续优化复盘。

## 快速开始

```bash
cd xhs-agent-lab
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 方式一：命令行生成
python main.py --topic "你的选题" --direct-ai-card

# 方式二：启动本地 Web App（推荐）
./run_webapp.sh        # 浏览器打开 http://localhost:8765
```

## 凭据（走环境变量，不入库）

```bash
export DEEPSEEK_API_KEY="..."      # 画面/文案创意层（没有则回退规则库）
export TELEGRAM_BOT_TOKEN="..."    # 推送到手机 Telegram
export TELEGRAM_CHAT_ID="..."
# 出图用 Vertex AI：gcloud ADC 登录，或设 GOOGLE_API_KEY
```

## 项目结构

主项目在 [`xhs-agent-lab/`](xhs-agent-lab/README.md)（详细文档见其 README）：

- `pipeline.py` / `main.py` — 生成流程核心（库 + CLI 入口）
- `webapp/` — 本地 Web App（FastAPI 后端 + 单页前端）
- `style_director.py` — 风格模板与隐喻库
- `creative_director.py` — DeepSeek 文本创意层
- `direct_card_renderer.py` / `card_renderer.py` — 出图渲染
- `push_telegram.py` / `prepare_publish.py` — 发布 / 打包

> `dist/`、`outbox/`、`prompts_log.jsonl`、`.venv/` 均不入库（生成产物与个人数据留在本地）。

---

🤖 部分功能借助 [Claude Code](https://claude.com/claude-code) 协助开发。
