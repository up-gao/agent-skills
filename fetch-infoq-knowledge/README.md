# fetch-infoq-knowledge

采集 **InfoQ 中国站（infoq.cn）** 文章，转为本地 Markdown，图片全部下载到本地
并使用相对路径引用。

支持两类来源：

| 来源 | 页面特征 | 采集方式 |
|---|---|---|
| `www.infoq.cn/article/<id>` | 服务端已渲染正文 | `curl` 即可，快 |
| `xie.infoq.cn/article/<uuid>` | 正文由 JS 渲染 | Playwright 渲染，较慢 |

## 快速开始

```bash
# www.infoq.cn 主站文章（curl 抓取，秒级）
curl -sL --max-time 30 \
  -H 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36' \
  -H 'Referer: https://www.infoq.cn/' \
  -o /tmp/infoq_page.html '<文章URL>'

python3 scripts/infoq_extract.py \
  --input /tmp/infoq_page.html \
  --output /tmp/infoq_extract.json \
  --html-output /tmp/infoq_body.html
```

```bash
# xie.infoq.cn 写作社区文章（Playwright 渲染 + 图片本地化，一步到位）
python3 scripts/fetch_xie_article.py '<文章URL>'
```

```bash
# 列出某作者的全部文章（给主页或给任意一篇其文章都可以）
python3 scripts/fetch_xie_list.py --user-url https://xie.infoq.cn/u/wangzhongyang/
python3 scripts/fetch_xie_list.py --article-url https://xie.infoq.cn/article/<uuid>
```

## 输出位置

**Markdown 默认保存到本 agent 工作空间的 `wk_data/infoq/` 目录**，
图片放在其下的 `images/` 子目录，Markdown 中以相对路径引用
（如 `images/image-20260925165854297.png`）。

可用 `--output-dir` 覆盖：

```bash
python3 scripts/fetch_xie_article.py '<URL>' --output-dir /tmp/out
```

## 脚本说明

### scripts/fetch_xie_article.py

采集 `xie.infoq.cn` 单篇文章 → 本地 Markdown。

```bash
python3 fetch_xie_article.py <文章URL> [--output-dir <目录>] [--images-dir <目录>]
```

流程：渲染页面 → 抽标题/作者/日期 → 转 Markdown → 下载图片 → 转相对路径 → 校验。

末行输出 JSON：

```json
{"ok": true, "title": "...", "author": "...", "publish_time": "...",
 "md_path": "...", "images_ok": 31, "images_failed": 0,
 "images_failed_urls": [], "source_url": "..."}
```

退出码：`0` 成功；`2` 参数/依赖错误；`3` 抓取或抽取失败。

### scripts/fetch_xie_list.py

列出某作者的文章清单（Markdown 清单打到 stdout，同时写 JSON）。

```bash
python3 fetch_xie_list.py --user-url <作者主页>
python3 fetch_xie_list.py --article-url <该作者的任意文章>
```

- 两个参数二选一（必填其一）
- 输出默认 `wk_data/infoq/xie_article_list.json`
- `--md-output` 可另存 Markdown 清单

### scripts/infoq_extract.py

从 `www.infoq.cn` 文章页抽取标题/作者/日期/正文。

```bash
python3 infoq_extract.py --input <html> --output <json> [--html-output <body.html>]
```

退出码：`0` 成功；`2` 输入错误；`3` 未找到正文容器（多为拦截页）。

## 已知限制

- **`infoq.com`（英文站）不支持**：返回 `405 Human Verification` 反爬页，无有效正文。
  本技能只覆盖 `infoq.cn`。
- **`www.infoq.cn` 有速率限制（非“部分文章不可采”）**：短时间内连续请求会返回 403（页面仅 ~555 字节、标题 `403 Forbidden`）。
  实测：**同一命令连续执行，参数完全一致，结果先 200 后 403 再 200** —— 说明是临时限流，不是站点封禁或文章问题。
  冷却几秒后重试即可成功。因此 403 应按**可重试**处理（退避重试），不要判为死链。
  另注意：403 页面的 curl 返回码仍为 0，**不能凭返回码判断成败**，必须检查是否拿到正文容器。
- **`xie.infoq.cn` 文章列表单次最多 50 条**：服务端硬上限，且分页参数
  （`page`/`page_num`/`offset`/`pageNo`/`last_id`）实测均不生效。
  脚本会置 `truncated: true` 标记。
- **列表接口返回的不全是该作者原创**：包含转载/收录文章，脚本不做过滤。
- **`xie.infoq.cn` 较慢**：单篇约 1-2 分钟，其中 4 秒为等待正文渲染的固定开销；
  渲染失败会自动重试一轮。

## 依赖

- `curl`、`python3`
- `playwright` + Chromium（**仅 `xie.infoq.cn` 需要**）
  安装：`pip install playwright && playwright install chromium`
- 本技能自带 `scripts/html_to_md.py`，**不依赖其他技能**（转换时自行完成）

## 文件说明

```
fetch-infoq-knowledge/
├── SKILL.md
├── README.md
└── scripts/
    ├── infoq_extract.py      # www.infoq.cn：抽取标题/作者/日期/正文
    ├── fetch_xie_article.py  # xie.infoq.cn：单篇文章 → 本地 Markdown
    └── fetch_xie_list.py     # xie.infoq.cn：作者文章列表
```
