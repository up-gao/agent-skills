# fetch-anthropic-knowledge

采集 **Anthropic / Claude 博客**文章为本地 Markdown（含正文与本地图片）。

## 何时使用

- 用户给出 `claude.com/blog/...` 或 `anthropic.com/...` 链接，要求"下载/采集/保存/转 Markdown"。
- 需要把 Anthropic 博客文章归档到本地 `wk_data/anthropic`。

## 快速开始

```bash
python3 scripts/fetch_anthropic_article.py \
  "https://claude.com/blog/the-ai-native-sdlc-playbook"
```

默认输出到 `~/.openclaw/wks/kg_fetcher/wk_data/anthropic/`，图片存到同目录 `images/`，Markdown 内用相对路径引用。

指定目录：

```bash
python3 scripts/fetch_anthropic_article.py "<url>" --output-dir /tmp/out
```

## 输出

- `<标题>.md` —— 正文 Markdown，头部含原文链接、作者、发布时间
- `images/image-YYYYMMDDHHmmssSSS.ext` —— 本地化图片
- 末行输出 JSON，便于上游编排器解析：

```json
{"ok":true,"title":"...","author":"...","publish_time":"...",
 "md_path":"...","images_ok":N,"images_failed":0,
 "images_failed_urls":[],"source_url":"..."}
```

## 站点特性（实测）

| 项目 | 实测结构 |
|---|---|
| 抓取方式 | Webflow 静态渲染，**curl 直接可取**（HTTP 200），无需浏览器 |
| 正文容器 | `<div class="blog_post_content_wrap">` |
| 内层正文 | `<div data-readtime="content" class="u-rich-text-blog w-richtext">` |
| 需丢弃 | 正文首部 `<div class="w-embed">` 内的 `<style>` 共享样式块 |
| 图片域名 | `cdn.prod.website-files.com` |
| 标题 | `<h1>`（或 `<meta property="og:title">`） |
| 发布日期 | 形如 `Aug 21, 2026` |
| 正文规模 | 示例文章约 108KB HTML、92 个 `<p>`、13 个 `<pre>` |

## 依赖

- Python 3（标准库）
- `curl`
- **无第三方 Python 包**，不需要 Playwright

## 设计

自带 `scripts/html_to_md.py`（独立实现，不引用其他技能）。按解耦原则，各站点转换规则各自演进。

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 成功（一般性缺图会在 JSON 中体现） |
| 2 | 参数错误 / 链接不属于该站点 / 依赖缺失 |
| 3 | 抓取失败 / 正文容器未找到 / 转换失败 |
