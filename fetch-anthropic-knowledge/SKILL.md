---
name: fetch-anthropic-knowledge
description: 采集 Anthropic / Claude 博客文章为本地 Markdown（图片本地化）。当用户给出 claude.com/blog 或 anthropic.com 链接，说"下载 Anthropic 博客"、"采集 Claude 博客文章"、"保存这篇 Anthropic 文章"、"转成 Markdown"时使用。
---

# fetch-anthropic-knowledge

把 **Anthropic / Claude 博客**文章采集为本地 Markdown，图片下载到本地并改为相对路径。

## 使用

```bash
python3 scripts/fetch_anthropic_article.py "<文章URL>"
```

默认输出到 `~/.openclaw/wks/kg_fetcher/wk_data/anthropic`。
`--output-dir <dir>` 可指定其他目录。

## 流程

1. `curl` 抓取页面 HTML（该站为 Webflow 静态渲染，**无需浏览器**）
2. 抽取标题（`<h1>` / `og:title`）、作者、发布日期
3. 定位正文容器 `div.blog_post_content_wrap`，抽取 `<div data-readtime="content" class="u-rich-text-blog w-richtext">`
4. 调用自带的 `scripts/html_to_md.py` 转 Markdown（丢弃正文首部的 `<style>` 样式块）
5. 下载图片到 `images/`，改写为相对路径，校验引用是否全部本地化

## 输出

- `<标题>.md`：含 `> 原文链接` / 作者 / 发布时间 头部
- `images/`：`image-YYYYMMDDHHmmssSSS.ext`
- 末行 JSON：`{"ok":...,"title":...,"md_path":...,"images_ok":N,"images_failed":N,...}`

## 依赖

仅需 Python 3 标准库 + `curl`，**不需要 Playwright**。

## 站点特性（实测记录）

| 项目 | 实测 |
|---|---|
| 抓取 | curl 直接 HTTP 200，无风控 |
| 正文容器 | `div.blog_post_content_wrap` |
| 内层正文 | `div[data-readtime="content"].u-rich-text-blog.w-richtext` |
| 需丢弃 | 正文首部 `div.w-embed` 内的 `<style>` 共享样式块 |
| 图片域 | `cdn.prod.website-files.com` |
| 日期格式 | `Aug 21, 2026` |
| 示例规模 | 108KB HTML、92 `<p>`、13 `<pre>`、4 `<img>` |

## 注意

- 正文容器不存在时退出码 3 —— 多为页面结构变更或非文章页。
- 该技能自带 `html_to_md.py`，**不引用其他技能**（按解耦原则，各站点转换规则独立演进）。
- 详细说明见 `README.md`。
