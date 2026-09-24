---
name: github-trending
description: GitHub 趋势榜/排行 (GitHub trending ranking). Use when the user asks for GitHub 每日/本周/当月 趋势、热榜、trending repos, 星级变化, or a trending leaderboard table. Produces three Markdown tables (今日/本周/本月) with 仓库名称、内容介绍、星级变化.
---

# GitHub Trending 榜单

抓取 GitHub Trending 三档榜单（daily / weekly / monthly），输出三张 Markdown 表格：
**仓库名称 | 内容介绍 | 星级变化**（附总星数与语言作参考列）。

## 何时使用

- "GitHub 今日/本周/本月趋势榜、热榜、排行"
- "trending repos"、"星级变化"、"star 增长榜"

## 步骤

1. 抓取三页 HTML（daily / weekly / monthly）：

   ```bash
   python3 scripts/fetch_trending.py --out /tmp/gh_trending
   ```

   脚本输出 `<out>/trending_{daily,weekly,monthly}.html` 与合并结果 `<out>/trending.json`。

2. 解析为表格并打印：

   ```bash
   python3 scripts/fetch_trending.py --out /tmp/gh_trending --format markdown
   ```

   得到三张 Markdown 表格，直接可用。

3. 验证：每张表行数 ≥ 10（正常约 17–25 行），且 **星级变化** 列无空值。若某张表为空或行数过少，重试一次；仍失败则报告该档抓取失败，不要伪造数据。

4. 若用户要"传播/分享"版式：飞书渠道避免 Markdown 表格，改为每档精简列表（`N. owner/repo — 介绍 — ⭐总星 +增长`），只保留 Top 10–15。

## 数据字段说明

| 列 | 含义 | 来源 |
| --- | --- | --- |
| 仓库名称 | `owner/repo` | `<h2><a href="/owner/repo">` |
| 内容介绍 | 仓库描述 | `<p class="col-9...">`（可为空，显示 `—`） |
| 星级变化 | 该周期新增 star | `N stars today / this week / this month` |
| 总星数 | 累计 star | `stargazers` 链接内数字 |
| 语言 | 主语言 | `itemprop="programmingLanguage"` |

注意：`since=daily` 对应文案 `stars today`；`weekly` → `stars this week`；`monthly` → `stars this month`。
描述缺省是正常现象（部分仓库未填描述），保留该行即可。

## 故障排查

- **超时/`http=000`**：`github.com` 解析到 `20.205.243.166` 时不可达。脚本会自动回退到可用 IP
  （`140.82.113.3`、`140.82.114.3`）并用 `--resolve` 指定；也可用 `--ip` 手动指定。
- **行数少/结构变化**：GitHub 偶尔改版。运行 `python3 scripts/fetch_trending.py --debug`
  打印每条 `Box-row` 的解析摘要，据此调整 `scripts/fetch_trending.py` 中的正则。
- **仅需 API 数据**：`api.github.com` 通常可达，但不提供 trending 接口；trending 只能抓 HTML。
  若要"真实新增 star"，可用 `/repos/{owner}/{repo}` 的 `stargazers_count` 对比历史快照（可选，非必需）。

## 边界

- 只读抓取，不登录、不做任何写操作。
- 抓取频率克制：单个周期一次即可，不要循环刷。
- 不编造数据；抓取失败就明确说明哪一档失败。
