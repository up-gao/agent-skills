# github-trending

抓取 GitHub Trending 三档榜单（今日 / 本周 / 本月），输出三张 Markdown 表格，每档默认取 **Top 20**。

## 快速开始

```bash
# 默认：Top 20，输出三张 Markdown 表格
python3 scripts/fetch_trending.py --out /tmp/gh_trending --format markdown
```

## 命令参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--out DIR` | `/tmp/gh_trending` | 输出目录，保存 HTML 快照与 JSON 结果 |
| `--format {markdown,json}` | `markdown` | 输出格式：三张表格 或 结构化 JSON |
| `--top N` | `20` | 每档保留前 N 个仓库；`0` 表示全量（通常 17–25 条） |
| `--ip IP[,IP...]` | 自动探测 | 手动指定 `github.com` 的 IP；不指定则自动探测可用 IP |
| `--debug` | 关闭 | 打印每条记录的解析摘要，便于排查改版 |

## 输出内容

三张表格（今日 / 本周 / 本月），列定义：

| 列 | 含义 |
| --- | --- |
| 排名 | 榜单序号 |
| 仓库名称 | `owner/repo` |
| 仓库地址 | GitHub 链接 |
| 内容介绍 | 仓库描述（缺失显示 `—`） |
| 星级变化 | 该周期**新增** star（`stars today` / `this week` / `this month`） |
| 总星数 | 累计 star |
| 语言 | 主语言 |

同时落盘：

- `trending_daily.html` / `trending_weekly.html` / `trending_monthly.html` — 原始页面快照
- `trending.json` — 结构化解析结果（含 `repo/desc/delta/total/lang/url`）

## 常用示例

```bash
# 只要前 10 名
python3 scripts/fetch_trending.py --top 10

# 全量抓取
python3 scripts/fetch_trending.py --top 0

# 指定可用 IP（网络环境异常时）
python3 scripts/fetch_trending.py --ip 20.27.177.113

# 输出 JSON 供二次处理
python3 scripts/fetch_trending.py --format json
```

## 依赖

- Python 3.8+（无第三方库）
- 系统 `curl`

## 工作原理

1. 抓取 `https://github.com/trending?since={daily|weekly|monthly}` 三页 HTML。
2. 用正则解析每条 `<article class="Box-row">`，提取仓库名、描述、累计星、周期新增星、语言。
3. 按 `--top` 截断，生成 Markdown 表格或 JSON。

**网络回退机制**：`github.com` 的 DNS 有时解析到不可达 IP。脚本会先直连，失败后自动探测
候选 IP 池（优先 `20.27.177.113`），记住首个可用 IP 并复用于后续请求。

## 故障排查

| 现象 | 处理 |
| --- | --- |
| `fetch failed: daily` | 多为网络/GFW 波动，稍后重试；或 `--ip` 指定其他 IP |
| 表格行数异常少 | GitHub 可能改版，用 `--debug` 查看解析摘要并调整脚本正则 |
| 描述列大量 `—` | 正常现象，部分仓库未填写描述 |

## 注意

- 只读抓取，无需登录、不写任何远端数据。
- 抓取频率保持克制，同一周期不要循环刷。
- 不伪造数据：抓取失败会明确报错，不会输出示例数据。
- 飞书等渠道不渲染 Markdown 表格，分享时建议改为精简列表。
