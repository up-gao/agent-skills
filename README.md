# agent-skills

A collection of [Claude Code / agent](https://claude.com/claude-code) skills for content acquisition, Markdown conversion, image localization, and publishing to Feishu (Lark) Wiki.

The project covers a complete pipeline: **fetch articles from various platforms → convert to clean Markdown → download and localize images → publish to Feishu Wiki / Cloud Docs in the original order**.

## Skills

### Content acquisition

| Skill | Description | Key dependency |
| --- | --- | --- |
| [`fetch-wx-knowledge`](./fetch-wx-knowledge) | Fetch WeChat Official Account articles (`mp.weixin.qq.com`) as local Markdown. Supports single article, batch URLs, and album/collection crawling. | `curl`, Python 3 |
| [`fetch-juejin-knowledge`](./fetch-juejin-knowledge) | Fetch Juejin (juejin.cn) articles, columns, and user article lists via the public content API. | `curl`, Python 3 |
| [`fetch-toutiao-knowledge`](./fetch-toutiao-knowledge) | Fetch Toutiao (今日头条) articles. Pages are fully JS-rendered, so a headless browser is required. | Playwright + Chromium |
| [`fetch-infoq-knowledge`](./fetch-infoq-knowledge) | Fetch InfoQ China (`infoq.cn`) articles. `www.infoq.cn` is server-rendered; `xie.infoq.cn` (writing community) needs Playwright. `infoq.com` is unsupported. | `curl`, Playwright (xie only) |
| [`fetch-csdn-knowledge`](./fetch-csdn-knowledge) | Fetch CSDN blog articles as local Markdown with localized images. | `curl`, Python 3 |
| [`fetch-anthropic-knowledge`](./fetch-anthropic-knowledge) | Fetch Anthropic / Claude blog articles (`claude.com/blog`, `anthropic.com`) as local Markdown. Static Webflow pages, no browser needed. | `curl`, Python 3 |
| [`fetch-golang-knowledge`](./fetch-golang-knowledge) | Generic web page → clean Markdown converter (used for `golangstar.cn` and other blogs). Preserves code blocks, tables, and downloads images. | `curl`, Python 3 |
| [`github-trending`](./github-trending) | Fetch the GitHub Trending leaderboard (daily / weekly / monthly) and render three Markdown tables. | `curl`, Python 3 |

### Publishing

| Skill | Description | Key dependency |
| --- | --- | --- |
| [`feishu_doc_writer`](./feishu_doc_writer) | Publish a local Markdown file (with images and tables) to Feishu Cloud Docs / Wiki via the Feishu Open Platform API, preserving the original order. | Python `requests` |
| [`import_article_to_wiki`](./import_article_to_wiki) | Orchestrator that collects an article link (WeChat / Juejin / Toutiao / InfoQ / CSDN / Anthropic), localizes images, then writes it into Feishu Wiki with a single command. | `requests`, Playwright (for some sources) |

## How it works

Most skills follow the same pattern:

1. **Fetch** the page (via `curl` or Playwright for JS-rendered pages).
2. **Extract** the title, author, publish date, and main content container.
3. **Convert** the HTML body to Markdown using a bundled `scripts/html_to_md.py`.
4. **Download** images to a local `images/` directory, renamed with a timestamp format `image-YYYYMMDDHHmmssSSS.ext`.
5. **Rewrite** image references to local (relative or absolute) paths.
6. **Verify** the result and print a final JSON line for upstream orchestration.

The `import_article_to_wiki` skill chains these acquisition skills together and calls `feishu_doc_writer` to publish the result to Feishu Wiki — the whole pipeline is fully scripted, so a single `python3 save_to_wiki.py` invocation completes the job without model-written per-step code.

## Requirements

- Python `>= 3.12` (see [`pyproject.toml`](./pyproject.toml))
- `curl`
- Python `requests` (for Feishu publishing)
- `playwright` + Chromium — only required for `fetch-toutiao-knowledge` and `xie.infoq.cn` (see `fetch-infoq-knowledge`)

## Project structure

```
agent-skills/
├── feishu_doc_writer/          # Publish Markdown → Feishu Cloud Docs / Wiki
├── fetch-anthropic-knowledge/  # Anthropic / Claude blog fetcher
├── fetch-csdn-knowledge/       # CSDN blog fetcher
├── fetch-golang-knowledge/     # Generic web page → Markdown converter
├── fetch-infoq-knowledge/      # InfoQ China fetcher
├── fetch-juejin-knowledge/     # Juejin fetcher
├── fetch-toutiao-knowledge/    # Toutiao fetcher (Playwright)
├── fetch-wx-knowledge/         # WeChat Official Account fetcher
├── github-trending/            # GitHub Trending leaderboard
├── import_article_to_wiki/     # Orchestrator: fetch + publish to Feishu Wiki
├── pyproject.toml
└── README.md
```

Each skill directory contains a `SKILL.md` (the agent-facing instructions with YAML frontmatter) and its own `scripts/`, plus a `README.md` (human-readable notes) where applicable.

## Documentation

- English: this file (`README.md`)
- 中文: [`README.zh-CN.md`](./README.zh-CN.md)
