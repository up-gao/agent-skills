---
name: fetch-infoq-knowledge
description: >
  采集 InfoQ 中国站文章为本地 Markdown。当用户提供 infoq.cn / www.infoq.cn / xie.infoq.cn
  的文章链接，说"采集 InfoQ"、"下载 InfoQ 文章"、"保存 InfoQ 这篇"、"InfoQ 转 Markdown"、
  "InfoQ 存到本地"时使用；当用户要求"列出某作者的文章列表"（给 InfoQ 作者主页或其任一文章）时
  也使用。输出 Markdown 默认存到 wk_data/infoq/，图片下载到本地并用相对路径引用。
  仅支持 infoq.cn；infoq.com（英文站）有反爬拦截，明确不支持。
---

# InfoQ 中国站文章采集

按来源分派脚本，**直接调脚本，不要手工逐段写抓取代码**。
各脚本的完整用法、参数、输出格式与限制见同目录 `README.md`。

## 分派表

| 用户给的链接 / 意图 | 用哪个脚本 |
|---|---|
| `www.infoq.cn/article/<id>` | `scripts/infoq_extract.py`（curl 抓页后抽取，快） |
| `xie.infoq.cn/article/<uuid>` | `scripts/fetch_xie_article.py`（Playwright 渲染，一步到 Markdown+图片） |
| 列某作者的文章列表 | `scripts/fetch_xie_list.py`（给 `--user-url` 或 `--article-url`） |
| 链接 → **飞书知识库/云文档** | 走 `import_article_to_wiki` 的 `save_to_wiki.py --url <链接> --space <id>`；本技能负责其中 infoq.cn 的采集部分 |
| `infoq.com`（英文站） | **不要执行采集**，直接告知不支持（405 反爬页） |

## 默认行为

- **Markdown 保存到当前 agent 工作空间的 `wk_data/infoq/`**，图片在其下 `images/`，
  Markdown 内用相对路径引用。用户未指定目录时用这个默认值，不要问。
- 图片命名 `image-YYYYMMDDHHmmssSSS.ext`。
- 批量：多个链接逐篇执行，每篇间隔 2-3 秒。
- 需要导入飞书时**不要在本技能里手工发文档**，改走
  `import_article_to_wiki/scripts/save_to_wiki.py --url <链接> --space <id>`，
  它已接入本技能的采集分支。

## 必须验证的点

脚本已完成校验，但汇报前要确认：

1. `fetch_xie_article.py` 末行 JSON 的 `images_failed` 为 0（有失败必须逐条列出 URL）。
2. Markdown 里不应残留 `https://static001` 开头的远程图片链接（应为 0）。
3. `fetch_xie_list.py` 若 `truncated: true`，汇报时说明"只取到前 50 篇，非全量"。

## 常见坑（写脚本时已处理，排查时先看这里）

- `xie.infoq.cn` 正文靠 JS 渲染，纯 curl 只能拿到空壳 → 必须 Playwright 渲染后取
  `div.ProseMirror` 的 **outerHTML**（从整页 HTML 切会少一个 `</div>`，正文被截断）。
- 本技能自带 `scripts/html_to_md.py`（解耦合，不依赖其他技能）；它按 `<article>` 外壳
  或第一个 `</h1>` 切正文，传入片段时注意保留正文容器标记。
- 列表接口是 **POST**（GET 会 404）；作者 ID 用 `ucode` 字段，不是 `uid`。
- `authorInfo` 埋点日志请求的 URL 也含 `authorInfo`，拦截时须按 `method == POST` 过滤。
- `www.infoq.cn` 有**速率限制**：短时间内连续请求会返回 403（页面仅 ~555 字节、
  标题为 `403 Forbidden`）。**这不是「该文章不可采」**，冷却几秒后同一请求即可成功
  （实测：同一命令连续执行，先 200 后 403 再 200，参数完全一致）。
  因此 403 属**可重试**，应退避重试（`import_article_to_wiki` 已内置 3 次、5s→15s）。
  注意：403 页面的 curl 返回码仍是 0，**不能凭返回码判断成败**，必须检查是否拿到正文容器。

## 依赖

- `curl`、`python3`
- `playwright` + Chromium（仅 `xie.infoq.cn` 需要）
- 本技能自带 `scripts/html_to_md.py`（无需其他技能）
