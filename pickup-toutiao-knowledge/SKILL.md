---
name: pickup-toutiao-knowledge
description: >
  抓取并保存今日头条文章为本地 Markdown 文件，包含图片下载。当用户提供头条文章链接时触发 —
  无论是说"保存这篇文章"、"下载头条文章"、"爬取这篇文章"、"保存今日头条文章"、
  "把链接转成markdown"、"save toutiao link as markdown"，还是直接粘贴
  https://www.toutiao.com/article/... 或 https://www.toutiao.com/a... 链接并要求
  抓取内容时，都使用此技能。此技能覆盖完整流程：无头浏览器渲染 JS 页面、提取标题和正文、
  转换为 Markdown、下载图片、以文章标题为文件名保存。
---

# 今日头条文章知识保存器

将今日头条文章保存为本地 Markdown 文件，并保留所有图片。

## 为什么需要这个技能

今日头条文章页面是**完全由 JavaScript 渲染的** — 普通的 HTTP 请求只会返回一个 JS 启动壳，
不包含实际文章内容。此技能内置了一个 Python 脚本，使用 **Playwright**（无头 Chromium）
像真实浏览器一样渲染页面，然后将文章文本和图片提取为干净的 Markdown。

## 技能功能

- 启动无头 Chromium 浏览器渲染今日头条文章页面
- 提取文章的**标题**、**描述**和**正文内容**
- 将 HTML 正文转换为清晰可读的 **Markdown** 格式
- 将文章中的**所有图片**下载到本地 `images/` 目录
- 以 `<文章标题>.md` 为文件名保存最终 Markdown 文件

## 工作原理

内置的 Python 脚本（`scripts/scrape_toutiao.py`）完成核心工作：

1. **渲染页面**：使用 Playwright 无头 Chromium 渲染页面，等待 JavaScript 执行完毕后文章内容出现。
2. **解析渲染后的 HTML**，尝试多种提取策略：
   - JSON-LD / schema.org `Article` 结构化标记
   - Open Graph / Twitter Card `<meta>` 标签
   - 页面中嵌入的 JavaScript 数据对象
   - 结构选择器（`.article-content`、`<article>`、`.rich_media_content`）
   - 可读性回退策略（页面上最大的文本块）
3. **转换为 Markdown**，处理标题、段落、列表、引用、代码块和图片。
4. **下载图片**：从头条 CDN 下载到 `<output_dir>/images/`，使用图片 URL 文件名加短哈希以避免冲突。
5. **写入** `<处理后的标题>.md` 到输出目录。

## 使用方法

### 第一步：一次性环境配置 — 安装依赖

```bash
pip install playwright requests beautifulsoup4
playwright install chromium
```

`playwright install chromium` 会下载 Chromium 浏览器（约 150MB），仅需执行一次。

### 第二步：运行脚本

```bash
python <skill-root>/scripts/scrape_toutiao.py "<文章链接>" [输出目录]
```

- **`<文章链接>`**（必填）：完整的今日头条文章 URL。
  支持以下格式：
  - `https://www.toutiao.com/article/<id>/`
  - `https://www.toutiao.com/a<id>/`
  - `https://mp.toutiao.com/profile_v4/...`
- **`输出目录`**（可选）：保存 `.md` 文件和 `images/` 的位置。
  默认为当前目录。

在命令末尾加 `--no-playwright` 可跳过浏览器渲染、仅使用普通 HTTP 请求
（速度更快但无法获取 JS 渲染的页面内容）。

### 第三步：输出结果

```
<输出目录>/
├── <文章标题>.md    # 完整的 Markdown 文章
└── images/           # 文章中的所有图片
    ├── xxx_1a2b.jpg
    └── yyy_3c4d.png
```

Markdown 中的图片使用**相对路径**引用，因此整个文件夹可以移动或分享，图片仍能正常显示。

## 重要注意事项

### 脚本路径

脚本位于技能目录内部。当 Claude 加载此技能后，需要相对于技能根目录解析路径：

```bash
python <skill-root>/scripts/scrape_toutiao.py "<链接>" "<输出目录>"
```

### Playwright 不可或缺

没有 Playwright 时，脚本会回退到普通 `requests` 请求，这**无法**获取今日头条的文章内容
（页面返回的只是 JS 启动壳）。务必在运行前确保 Playwright + Chromium 已安装。

### 如果 Playwright 运行失败

有时 Playwright 可能会超时或页面结构发生了变化。此时：

1. **尝试增加等待时间**：脚本会等待文章内容出现；如果网络较慢，可能会超时。
   重新运行通常可以解决。

2. **尝试移动版 URL**：部分今日头条文章在移动版上有更好的渲染效果。
   将 `www.toutiao.com` 替换为 `m.toutiao.com` 试试。

3. **手动回退方案**：使用 `curl` 或 `wget` 带上浏览器头部信息，保存 HTML 后
   检查其中是否嵌入了 JSON 数据或文章文本。

### 图片下载

- 今日头条图片来自 CDN 域名，如 `p*.toutiaoimg.com`、`p*.byteimg.com` 等。
- 懒加载图片使用 `data-src` 或 `data-original` 属性 — 脚本会检查所有这些属性。
- 图片下载失败不会中断流程；失败的图片会在 Markdown 中保留原始 CDN 链接作为回退。

### 标题处理

中文字符完整保留。仅文件系统不允许的字符
（`\`、`/`、`:`、`*`、`?`、`"`、`<`、`>`、`|`）会被替换为下划线。
过长的标题会截断至 200 个字符。

## 常见使用场景

**示例一：保存单篇文章**
```
用户：帮我把这篇文章保存下来 https://www.toutiao.com/article/7667029267172655626/
操作步骤：
  1. pip install playwright requests beautifulsoup4（仅首次）
  2. playwright install chromium（仅首次）
  3. python <skill-root>/scripts/scrape_toutiao.py \
       "https://www.toutiao.com/article/7667029267172655626/" .
```

**示例二：保存到指定目录**
```
用户：保存到 ~/Documents/toutiao-articles/ 这篇文章
      https://www.toutiao.com/article/7667029267172655626/
操作步骤：python <skill-root>/scripts/scrape_toutiao.py \
          "https://www.toutiao.com/article/7667029267172655626/" \
          ~/Documents/toutiao-articles/
```

**示例三：多篇文章**
```
用户：保存这些头条文章：
      https://www.toutiao.com/article/123456/
      https://www.toutiao.com/article/789012/
操作步骤：对每个 URL 分别运行脚本，使用相同的输出目录。
```

**示例四：带跟踪参数的 URL**
```
用户：https://www.toutiao.com/article/7667029267172655626/?log_from=xxx
操作步骤：跟踪参数不影响脚本运行 — 直接传入完整 URL 即可。
```
