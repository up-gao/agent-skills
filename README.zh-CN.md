# agent-skills

一个 [Claude Code / agent](https://claude.com/claude-code) 技能合集，用于内容采集、Markdown 转换、图片本地化，以及发布到飞书知识库。

项目覆盖一条完整链路：**从各平台采集文章 → 转换为清晰的 Markdown → 下载并本地化图片 → 按原顺序发布到飞书知识库 / 云文档**。

## 技能列表

### 内容采集

| 技能 | 说明 | 关键依赖 |
| --- | --- | --- |
| [`fetch-wx-knowledge`](./fetch-wx-knowledge) | 采集微信公众号文章（`mp.weixin.qq.com`）为本地 Markdown。支持单篇、批量 URL、专栏/合集采集。 | `curl`、Python 3 |
| [`fetch-juejin-knowledge`](./fetch-juejin-knowledge) | 通过掘金公开内容 API 采集掘金（juejin.cn）文章、专栏和用户文章列表。 | `curl`、Python 3 |
| [`fetch-toutiao-knowledge`](./fetch-toutiao-knowledge) | 采集今日头条文章。页面完全由 JS 渲染，需要无头浏览器。 | Playwright + Chromium |
| [`fetch-infoq-knowledge`](./fetch-infoq-knowledge) | 采集 InfoQ 中国站（`infoq.cn`）文章。`www.infoq.cn` 服务端已渲染；`xie.infoq.cn`（写作社区）需 Playwright。`infoq.com` 不支持。 | `curl`、Playwright（仅 xie） |
| [`fetch-csdn-knowledge`](./fetch-csdn-knowledge) | 采集 CSDN 博客文章为本地 Markdown，图片本地化。 | `curl`、Python 3 |
| [`fetch-anthropic-knowledge`](./fetch-anthropic-knowledge) | 采集 Anthropic / Claude 博客文章（`claude.com/blog`、`anthropic.com`）为本地 Markdown。Webflow 静态页，无需浏览器。 | `curl`、Python 3 |
| [`fetch-golang-knowledge`](./fetch-golang-knowledge) | 通用网页 → 清晰 Markdown 转换器（用于 `golangstar.cn` 等博客）。保留代码块、表格，并下载图片。 | `curl`、Python 3 |
| [`github-trending`](./github-trending) | 抓取 GitHub Trending 榜单（今日 / 本周 / 本月），输出三张 Markdown 表格。 | `curl`、Python 3 |

### 发布

| 技能 | 说明 | 关键依赖 |
| --- | --- | --- |
| [`feishu_doc_writer`](./feishu_doc_writer) | 通过飞书开放平台接口，把本地 Markdown（含图片、表格）按原顺序发布到飞书云文档 / 知识库。 | Python `requests` |
| [`import_article_to_wiki`](./import_article_to_wiki) | 编排器：一条命令完成「采集文章链接（微信 / 掘金 / 头条 / InfoQ / CSDN / Anthropic）+ 图片本地化 + 写入飞书知识库」。 | `requests`、Playwright（部分来源） |

## 工作原理

多数技能遵循同一套流程：

1. **抓取**页面（用 `curl`，或对 JS 渲染页面用 Playwright）。
2. **抽取**标题、作者、发布时间和正文容器。
3. 用自带的 `scripts/html_to_md.py` 将 HTML 正文**转换**为 Markdown。
4. 将图片**下载**到本地 `images/` 目录，按时间戳格式 `image-YYYYMMDDHHmmssSSS.ext` 重命名。
5. 将图片引用**改写**为本地（相对或绝对）路径。
6. **校验**结果，并在末行输出 JSON 供上游编排解析。

`import_article_to_wiki` 把上述采集技能串起来，并调用 `feishu_doc_writer` 将结果发布到飞书知识库 —— 整条链路已全脚本化，一次 `python3 save_to_wiki.py` 调用即可完成，无需模型逐步现场编写代码。

## 环境要求

- Python `>= 3.12`（见 [`pyproject.toml`](./pyproject.toml)）
- `curl`
- Python `requests`（用于飞书发布）
- `playwright` + Chromium —— 仅 `fetch-toutiao-knowledge` 和 `xie.infoq.cn`（见 `fetch-infoq-knowledge`）需要

## 项目结构

```
agent-skills/
├── feishu_doc_writer/          # 发布 Markdown → 飞书云文档 / 知识库
├── fetch-anthropic-knowledge/  # Anthropic / Claude 博客采集
├── fetch-csdn-knowledge/       # CSDN 博客采集
├── fetch-golang-knowledge/     # 通用网页 → Markdown 转换器
├── fetch-infoq-knowledge/      # InfoQ 中国站采集
├── fetch-juejin-knowledge/     # 掘金采集
├── fetch-toutiao-knowledge/    # 今日头条采集（Playwright）
├── fetch-wx-knowledge/         # 微信公众号采集
├── github-trending/            # GitHub Trending 榜单
├── import_article_to_wiki/     # 编排器：采集 + 发布到飞书知识库
├── pyproject.toml
└── README.md
```

每个技能目录包含一个 `SKILL.md`（面向 agent 的执行说明，带 YAML frontmatter）、各自的 `scripts/`，以及（部分技能）一个给人看的 `README.md` / `readme.md`。

## 文档

- 英文：`README.md`
- 中文：本文件（`README.zh-CN.md`）
