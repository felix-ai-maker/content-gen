# xhs-agent-lab

一个本地的「小红书 + 公众号内容自动化生产线」。

每天输入一个选题，项目会生成当天发布包：

- `小红书正文.md`
- `公众号文章.md`
- `card_01.png` 到 `card_07.png`
- `cards_used.json`
- `style_plan.json`
- `发布清单.md`

卡片尺寸默认是小红书竖图 `1080 x 1440`。项目会先根据选题、原始文案和卡片内容自动生成 `style_plan.json`，再决定每张卡适合的主题、视觉隐喻、信息结构和点缀色。底部固定显示品牌名「交易 Agent 实验室」。

## 手机上怎么用 Codex 操作

这个项目推荐把 Codex 当成执行代理：你在手机上的 Codex 线程里发选题和素材，Codex 在这台 Mac 的工作区里运行脚本、检查输出、再按结果优化。

手机指令模板见 [CODEX_RUNBOOK.md](/Users/jiyunliu/Documents/content-gen/xhs-agent-lab/CODEX_RUNBOOK.md)。

Codex 生成后可以运行：

```bash
python inspect_package.py
```

它会检查最新发布包的必需文件、7 张卡片尺寸、低 AI 味评分和常见营销腔词。

草稿迭代时建议让 Codex 加上 `--local-bg`，这样不会每次都调用 Vertex/Gemini 消耗图片配额：

```bash
python main.py --topic "选题" --copy "素材" --local-bg
```

## 1. 安装依赖

```bash
cd xhs-agent-lab
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

默认使用 Vertex AI / Gemini 图像模型生成卡片背景。安装依赖后，按你的账号类型选择一种认证方式。

方式 A：完整 Vertex AI / ADC 认证：

```bash
export GOOGLE_CLOUD_PROJECT="你的 GCP 项目 ID"
export GOOGLE_CLOUD_LOCATION="global"
gcloud auth application-default login
```

如果你用服务账号 JSON：

```bash
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/service-account.json"
export GOOGLE_CLOUD_PROJECT="你的 GCP 项目 ID"
export GOOGLE_CLOUD_LOCATION="global"
```

方式 B：Vertex AI Express / API Key：

```bash
export GOOGLE_API_KEY="你的 Vertex/Gemini API Key"
```

说明：图像模型只生成无文字的高级背景，中文标题和正文仍由本地 Pillow 精确排版，避免图片模型把中文画错。

## 2. 输入素材和卡片文案

默认没有 `cards.json` 时，程序会读取 `sample_cards.json`。

要使用自己的卡片文案，在项目根目录新建 `cards.json`：

```json
{
  "cards": [
    {
      "type": "cover",
      "kicker": "交易 Agent 实验室 001",
      "title": "你的封面标题",
      "subtitle": "你的封面副标题",
      "accent": "RESEARCH / SIGNAL / REVIEW"
    },
    {
      "type": "content",
      "kicker": "01 / PROBLEM",
      "title": "内容页标题",
      "subtitle": "内容页副标题",
      "bullets": [
        "第一条要点",
        "第二条要点",
        "第三条要点"
      ],
      "note": "底部提示语"
    }
  ]
}
```

规则：

- 提供 `cards.json`：直接按 `cards.json` 生成图片。
- 提供 `--cards your_cards.json`：直接按指定文件生成图片。
- 不提供卡片文件，但提供 `--copy` 或 `--copy-file`：自动从原始文案里拆出 7 张卡。
- 不提供卡片文件，也不提供素材：使用 `sample_cards.json` 生成示例图片。
- 生成文件名按顺序输出为 `card_01.png`、`card_02.png`。

## 3. 自动风格导演

项目不会把所有选题都套成同一种固定风格。`style_director.py` 会先读取标题、正文、卡片要点和原始素材，自动判断内容更像哪类主题：

- `判断留痕`：交易、复盘、证据、反证、仓位、冲动。
- `增长与代价`：收入、成本、利润、毛利、投入、费用。
- `入口迁移`：微信、小程序、入口、调用、接入、生态。
- `自动化工作流`：输入、处理、输出、流程、API、系统。
- `风险边界`：红线、责任、不能、必须、警告、规则。
- `知识笔记`：没有明显领域时的通用知识卡。

每次生成都会输出：

```text
style_plan.json
```

里面会记录：

- 本次选择的视觉主题。
- 每张卡使用的版式类型，例如 `cover_impact`、`warning_rule`、`process_flow`、`comparison`。
- 每张卡对应的视觉隐喻，例如证据桌、反证天平、责任边界、流程模块。
- 每张卡传给图像模型的主题提示词。

如果你想人工控制某张卡，也可以在 `cards.json` 里提前写 `visual_style` 字段，程序会在此基础上补全缺失信息。

## 3.5 视觉风格模板与 DeepSeek 创意层（仅 `--direct-ai-card`）

直接出图模式（`--direct-ai-card`）在「自动风格导演」之上还有两层控制，决定卡片「长什么样」和「画面有多丰富」。

### 风格模板（style preset）

`config.yaml` 的 `style_presets` 预置了几套视觉风格，决定配色、排版倾向和隐喻取向：

- `magazine_editorial`：杂志编辑精致（米白纸 + 油墨黑 + 墨绿强调），默认。
- `bold_xhs`：小红书爆款封面（大色块、超大标题、强对比）。
- `warm_editorial`：暖色编辑插画（暖米 / 赭石 / 柔和）。
- `signature_mono`：极简科技蓝（旧风格，向后兼容）。

用法：

```bash
# 手动指定风格
python main.py --topic "选题" --direct-ai-card --style bold_xhs
```

不指定 `--style` 时，按选题关键词自动选；没命中就用 `image_model.default_style`（默认 `magazine_editorial`）。

### DeepSeek 文本创意层

开启后，程序会先用文本大模型（DeepSeek）为每页**创意生成画面 brief**（隐喻、构图、细节、配色侧重），再交给图像模型出图，让每个选题的画面量身定制、比固定隐喻库更丰富多样。

`config.yaml` 的 `creative_llm`：

```yaml
creative_llm:
  enabled: true
  provider: deepseek
  base_url: "https://api.deepseek.com"
  model: "deepseek-chat"
  api_key_env: DEEPSEEK_API_KEY
  timeout_seconds: 60
  temperature: 0.9
```

设置 API key（走环境变量，不要写进项目文件）：

```bash
export DEEPSEEK_API_KEY="你的 DeepSeek API Key"
```

说明：

- 仅 `--direct-ai-card` 模式生效。
- 任何失败（未启用 / 无 key / 网络超时 / 输出不合格）都会**自动回退**到 `style_director.py` 的规则隐喻库，不会中断出图。
- 关闭：把 `enabled` 设为 `false`，或不设置 key。
- 换文本模型：改 `model` 与 `base_url`（任何 OpenAI 兼容的 chat/completions 接口都可）。

## 4. 一键生成卡片和正文

```bash
python main.py --topic "我为什么要搭一个自己的交易 Agent？"
```

带一组文案自动拆卡：

```bash
python main.py \
  --topic "我为什么要搭一个自己的交易 Agent？" \
  --copy "我最近发现，交易里最消耗人的不是下单，而是每天信息太散。新闻、公告、财报都看了，但复盘时经常找不到当时为什么这么判断。所以我想先做一个研究助理，把输入、假设、证据、风险和复盘都留在同一个地方。"
```

长素材建议放到文件里：

```bash
python main.py \
  --topic "我为什么要搭一个自己的交易 Agent？" \
  --copy-file inputs/today.md
```

输出目录格式：

```text
dist/YYYY-MM-DD_选题名称/
```

示例：

```text
dist/2026-06-16_我为什么要搭一个自己的交易 Agent/
```

输出目录里会额外保存：

- `cards_used.json`：当天实际用于渲染的卡片结构，方便复盘和二次修改。
- `source_copy.md`：如果你传了 `--copy` 或 `--copy-file`，会保存原始素材。
- `发布清单.md`：包含低 AI 味评分、发布前人工检查项和素材来源。

## 5. 低 AI 味质检

程序会自动检查：

- 是否出现「赋能」「多维度」「深度解析」「干货满满」等常见 AI/营销腔。
- 句子是否过长。
- 是否保持 7 张小红书卡片。
- 内容页信息量是否过薄。

质检不会替你做事实判断，它只负责提醒文本味道和发布流程问题。交易相关内容发布前仍然要人工核对事实、时间、数据和具体标的。

## 6. 更换字体

打开 `config.yaml`，修改 `font.regular` 和 `font.bold`：

```yaml
font:
  regular:
    - "/System/Library/Fonts/PingFang.ttc"
  bold:
    - "/System/Library/Fonts/PingFang.ttc"
```

程序会按顺序查找字体路径，找到第一个可用字体。中文卡片建议使用：

- macOS：`/System/Library/Fonts/PingFang.ttc`
- macOS：`/System/Library/Fonts/Hiragino Sans GB.ttc`
- Linux：`/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc`

## 7. 修改品牌名和颜色

打开 `config.yaml`：

```yaml
brand:
  name: "交易 Agent 实验室"

colors:
  background: "#F7F7F5"
  text: "#111111"
  muted: "#666B73"
  accent: "#1F6FFF"
  hairline: "#DADDE2"
  grid: "#ECEEF2"
  ghost: "#E8EBF0"
```

常用改法：

- 修改 `brand.name`：更换卡片底部固定品牌名。
- 修改 `colors.accent`：更换科技蓝点缀色。
- 修改 `canvas.width` 和 `canvas.height`：更换输出尺寸。

## 高级图像模型开关

`config.yaml` 里默认启用 Vertex Gemini 图像模型背景：

```yaml
image_model:
  enabled: true
  provider: vertex_gemini
  model: gemini-3.1-flash-image
  project: gen-lang-client-0729475539
  location: global
  api_version: v1
  api_key_env: GOOGLE_API_KEY
```

如果没有可用的 Google 凭证，程序会自动退回到本地极简背景，仍然能完整生成发布包。

可选 provider：

- `vertex_gemini`：默认推荐，使用 Gemini 图像模型。
- `vertex_imagen`：使用 Imagen 4。把 `provider` 改为 `vertex_imagen`，把 `model` 改为 `imagen-4.0-generate-001` 或你项目可用的 Imagen 模型。
- `openai`：兼容旧方案。需要额外安装 `openai` 包并设置 `OPENAI_API_KEY`。

如果你的 Vertex 项目已经开通更新的 Gemini 图像模型，也可以把模型名改成例如：

```yaml
image_model:
  provider: vertex_gemini
  model: gemini-3.1-flash-image
```

如果你只想本地生成，不调用图像模型：

```yaml
image_model:
  enabled: false
```

## 7. 调整卡片背景提示词

背景提示词由 [card_renderer.py](/Users/jiyunliu/Documents/content-gen/xhs-agent-lab/card_renderer.py) 组合生成，设计语言放在 `config.yaml` 的 `design_prompt`：

```yaml
design_prompt:
  principles:
    - "editorial hierarchy, not decorative wallpaper"
    - "large negative space for Chinese typography"
    - "asymmetric composition with a quiet focal structure"
  materials:
    - "matte paper grain"
    - "frosted translucent glass planes"
    - "soft architectural shadows"
```

程序会按卡片角色自动追加不同构图提示：

- 封面：右侧/下方抽象主结构，左侧大留白。
- 问题页：碎片化信息逐渐组织起来。
- 边界页：清晰阈值和责任边界。
- 闭环页：输入、证据、行动、复盘的隐性循环。

注意：图像模型只生成背景，中文标题和正文仍由本地排版渲染。提示词里会强制禁止伪文字、数字、图表、K 线和红绿涨跌视觉。
