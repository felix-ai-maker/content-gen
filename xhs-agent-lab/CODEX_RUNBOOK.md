# Codex 手机工作流

这个项目不需要再做一个聊天网页。推荐工作方式是：手机上打开同一个 Codex 线程，把 Codex 当成执行代理，让它在这台 Mac 的工作区里运行脚本、检查结果、再做一轮优化。

## 你在手机上怎么发

复制下面模板给 Codex：

```text
用 xhs-agent-lab 生成今天的发布包。

选题：
我为什么要搭一个自己的交易 Agent？

素材：
这里粘贴今天的原始想法、聊天记录、读书笔记、行情观察、复盘记录。

要求：
1. 先用本地背景生成草稿，避免浪费 Vertex 配额。
2. 检查小红书正文、公众号文章、7 张卡片和发布清单。
3. 如果标题太像 AI 或内容太空，直接改 cards_used.json 或生成新的 cards.json，再重跑。
4. 最后告诉我发布包路径、低 AI 味评分、你改了什么。
```

如果你已经想直接用 Vertex Gemini 背景：

```text
用 xhs-agent-lab 生成今天的发布包，允许调用 Vertex Gemini 生成图片背景。

选题：
...

素材：
...
```

## Codex 应该怎么执行

进入项目：

```bash
cd /Users/jiyunliu/Documents/content-gen/xhs-agent-lab
source .venv/bin/activate
```

草稿优先使用本地背景，避免每轮迭代都消耗图片模型配额：

```bash
python main.py \
  --topic "选题" \
  --copy "素材" \
  --local-bg
```

生成后检查：

```bash
python inspect_package.py
```

如果要检查指定目录：

```bash
python inspect_package.py --dir "dist/YYYY-MM-DD_选题"
```

## Codex 优化原则

- 先看 `发布清单.md`，再看 `小红书正文.md` 和 `公众号文章.md`。
- 卡片内容不满意时，优先修改 `cards_used.json` 的文案结构，再保存为项目根目录 `cards.json` 重跑。
- 小红书封面只要一个强标题，不要解释太多。
- 公众号开头要像人说话：具体、第一人称、少口号。
- 交易内容不要写收益承诺，不要制造红绿涨跌/K 线视觉。
- 没有必要每轮都调用 Vertex。标题和正文定稿后，再允许模型生成背景。

## 手机指令示例

```text
继续优化刚才的发布包：
1. 小红书正文更像我自己写的，少一点“系统/流程”的抽象词。
2. 公众号第一段加一个具体场景：晚上复盘，发现判断散在不同窗口里。
3. 卡片 2 的标题更短，更像封面级表达。
4. 重跑并检查图片尺寸。
```

```text
把今天的内容做成更克制的版本：
不要爆款标题，不要劝人收藏，不要财经号语气。
如果需要，先生成 cards.json 再跑。
```

## 安全约定

- 不要把 Google 授权码、API Key、服务账号 JSON 写进项目文件。
- Project ID 可以写进配置；密钥和授权码只放在本机 ADC 或环境变量里。
- 如果凭据曾经贴到聊天里，用完后建议在 Google Cloud 里撤销或重新生成。
