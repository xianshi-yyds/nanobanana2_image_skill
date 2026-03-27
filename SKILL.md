---
name: nanobanana-image-gen
description: 调用nanobanana2 API，支持文生图（text-to-image）和图生图（image-to-image）功能，包含多比例选择、用户自定义提示词，以及单张/多张图片参考生成。
---

# nanobanana2生成技能

## 概览

本技能支持nanobanana2生成官方稳定版 API，包含：

| 功能 | 模式 | 说明 |
|------|------|------|
| 文生图 | `text` | 根据文字提示词生成图片，支持多种比例和分辨率 |
| 图生图 | `image` | 上传一张或多张参考图，结合提示词生成新图片 |

---

## 前置条件

### 1. 配置 API Key

在技能目录根部创建 `.env` 文件（不要提交到版本控制）：

```bash
cp .env.example .env
# 编辑 .env 填入你的真实 API Key
```

`.env` 文件格式：

```
NANOBANANA_API_KEY=your_api_key_here
```

### 2. 安装依赖

本技能仅需 Python 标准库 + `requests`：

```bash
pip install requests python-dotenv
```

---

## 使用方法

所有调用均通过 `scripts/generate.py` 执行。

### 文生图（Text-to-Image）

```bash
python scripts/generate.py text \
  --prompt "一只在樱花树下打盹的橘猫，吉卜力风格，柔和光线" \
  --aspect-ratio "16:9" \
  --resolution "1k"
```

**参数说明：**

| 参数 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `--prompt` | ✅ | — | 图片描述提示词（中英文均支持）|
| `--aspect-ratio` | ❌ | `1:1` | 宽高比。文生图必填（默认1:1），图生图建议不填（自动遵循原图） |
| `--resolution` | ❌ | `1k` | 分辨率，`1k` 或 `2k` |
| `--output` | ❌ | 当前目录 | 图片保存路径/目录 |

**支持的 `--aspect-ratio` 值（手动指定时）：**

```
1:1   | 16:9  | 9:16
4:3   | 3:4   | 3:2
2:3   | 21:9  | 9:21
```

---

### **图生图比例自适应测试（以 16:9 原图为参考，不指定比例）：**
![auto-aspect-ratio: 保持 16:9](/Users/xianshi/.gemini/antigravity/brain/ea504a65-5a35-46ff-87c9-7405c91e0097/img2img_auto_ratio.jpg)

---
### 图生图（Image-to-Image）

#### 单张图片参考

```bash
python scripts/generate.py image \
  --images "https://example.com/reference.jpg" \
  --prompt "将这张图改为赛博朋克风格，霓虹灯光效果" \
  --aspect-ratio "9:16"
```

#### 多张图片参考

```bash
python scripts/generate.py image \
  --images "https://example.com/ref1.jpg" "https://example.com/ref2.jpg" \
  --prompt "融合这两张图的风格，生成一张新作品" \
  --aspect-ratio "1:1" \
  --resolution "2k"
```

**参数说明：**

| 参数 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `--images` | ✅ | — | 参考图 URL 或 **本地文件路径**（可传多个，空格分隔）|
| `--prompt` | ✅ | — | 图片处理或生成提示词 |
| `--aspect-ratio` | ❌ | `1:1` | 输出图片宽高比（图生图建议不填，自动遵循原图） |
| `--resolution` | ❌ | `1k` | 分辨率 |
| `--output` | ❌ | 当前目录 | 图片保存路径/目录 |

---

## 工作流程

```
1. 发送生成请求  →  获得 taskId
2. 轮询任务状态  →  /openapi/v2/query (使用 POST)
3. 状态变为 SUCCESS  →  解析结果 URL
4. 下载图片到本地
```

轮询间隔：3 秒，最大等待：5 分钟（可在 `scripts/generate.py` 中调整 `MAX_WAIT_SECONDS`）。

---

## AI 调用指引

当用户需要生图时，按如下方式决策：

1. **纯文字描述图片** → 使用 `text` 模式
2. **提供了参考图 URL** → 使用 `image` 模式，将所有 URL 传入 `--images`
### 3. 未指定比例 → 默认 `1:1`
4. **需要更高画质** → 传入 `--resolution 2k`

---

## 🎨 输出格式要求 (CRITICAL)

为了确保用户能够直接看到生成的图片，AI 在调用脚本成功后，**必须**按以下格式在回复中嵌入图片：

```markdown
![生成结果](图片URL)
```

**严禁**只返回 JSON 或仅告知“生成成功”，必须直接展示图片预览。

---

## AI 调用指引

```bash
python scripts/generate.py text \
  --prompt "<用户描述>" \
  --aspect-ratio "16:9" \
  --output "./output"
```

```bash
python scripts/generate.py image \
  --images "<url1>" "<url2>" \
  --prompt "<用户指令>" \
  --aspect-ratio "1:1" \
  --output "./output"
```

---

## 输出

成功时，脚本会：
1. 打印图片的在线 URL
2. 将图片下载并保存到 `--output` 指定路径（默认：`./output/generated_<timestamp>.png`）

失败时，脚本会打印错误信息并以非零退出码退出。
