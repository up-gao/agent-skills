---
name: import-article-to-wiki
description: >
  采集微信公众号、掘金、今日头条、InfoQ、CSDN、Anthropic/Claude 博客的文章链接（图文），转成 Markdown 并上传到飞书知识库/云文档。
  当用户说"把这篇文章保存到飞书知识库"、"链接转飞书文档"、"采集到飞书"、
  "微信/掘金/头条/InfoQ/CSDN 文章存到知识库"、给出 mp.weixin.qq.com、juejin.cn、toutiao.com、
  infoq.cn（含 xie.infoq.cn）、blog.csdn.net 或 claude.com/blog 链接并要求写入飞书时，使用此技能。也支持把已有本地 Markdown 上传到知识库。
  覆盖完整链路：识别来源 → 采集技能产出完整 Markdown（图片本地化，失败自动重试）
  → 统一图片相对路径 → feishu_doc_writer 按序写入节点并回读校验 → 汇总缺图情况。
---

# 链接 / Markdown → 飞书知识库（微信 / 掘金 / 头条 / InfoQ / CSDN / Anthropic）

一条命令完成「采集文章 + 上传飞书知识库」。

**本技能已全脚本化：整条链路只需一次 `python3 save_to_wiki.py` 调用，
不要逐个 skill、逐段地手工执行或编写 Python 片段。**

## 铁律：跑脚本，不要写代码

上游 `fetch-wx-knowledge` / `fetch-juejin-knowledge` 的 SKILL.md 里，
多步（图片下载、路径替换、Markdown 组装）写成了**需要模型现场编写并执行的代码块**。
照那种方式做，一次采集要十几轮模型往返，还容易漏步（踩过：整步图片下载被漏掉，
文档里图全是空的）。

本技能把这些都固化成了脚本。**遇到本技能范围内的任务，一律直接调脚本：**

| 需求 | 一次调用 |
|---|---|
| 链接 → 飞书知识库（微信/掘金/头条/InfoQ/CSDN/Anthropic） | `save_to_wiki.py --url ... --space ...` |
| 只采集微信单篇到本地 | `fetch_wx_article.py <url> --output-dir <dir>` |
| 下载 md 里的图片并转相对路径 | `download_images.py <md> --images <dir>` |

只有当上游技能**本技能未覆盖**的场景（如掘金专栏、用户主页全量、InfoQ 作者文章列表）
才回退到按上游 SKILL.md 手工执行。

## 何时使用

| 用户意图 | 命令 |
|---|---|
| 微信/掘金/头条/InfoQ/CSDN 文章 → 知识库新建节点 | `save_to_wiki.py --url "<链接>" --space <space_id>` |
| 多篇一次上传 | 重复传 `--url` |
| 已有本地 Markdown → 知识库 | `--md /path/a.md --space <id>` |
| 写入知识库**已有**文档 | `--wiki https://xxx.feishu.cn/wiki/XXXX` |
| 只采集不发布 | `--collect-only` |

## 快速开始

```bash
SKILL=~/.openclaw/wks/kg_fetcher/skills/import_article_to_wiki
python3 $SKILL/scripts/save_to_wiki.py \
  --url "https://mp.weixin.qq.com/s/xxxxx" \
  --space 7689120845356092370
```

重复 H1 默认已移除，无需额外加 `--strip-h1`。来源自动识别，无需指定平台。

成功输出（每篇）：

```
[1/2] wx · https://mp.weixin.qq.com/s/xxxxx
  ✓ 采集完成：文章标题.md
============================================================
发布 [1/1] 文章标题.md
  已移除重复 H1：文章标题
  [1/4] 知识库新建节点 KDxsw... → 文档 CjFGd...
  [3/4] 完成：块 109 / 表格 2 / 图片 4，耗时 67s
  [4/4] 校验：共 156 块，图片块 4（空 0），表格块 2
  ✓ 完成（知识库）：https://feishu.cn/wiki/KDxswXIA2iVVx4k2QgVcBU0pnPh

完成：1/1 篇写入飞书知识库
```

**把 `✓ 完成（知识库）` 那一行的链接原样回报给用户**，它是最终交付物。

## 参数

| 参数 | 说明 |
|---|---|
| `--url` | 文章链接，可重复传入多篇 |
| `--md` | 已有本地 Markdown，跳过采集 |
| `--space` | 知识空间 ID，在其下**新建**节点 |
| `--wiki` | 知识库**已有**文档链接/node_token，覆盖写入 |
| `--parent-node` | 新节点的父节点 token（可选，默认空间根目录） |
| `--title` | 文档标题，**仅单篇有效**；默认取 Markdown 首个 H1 |
| `--workdir` | 采集工作目录，默认 `/tmp/feishu-wiki-<时间戳>` |
| `--continue-on-error` | 某篇失败继续后续；默认遇错终止 |
| `--account` | 飞书账号，默认 `kg_fetcher_robot` |
| `--collect-only` | 只采集不发布 |

退出码：`0` 全部成功；`1` 有失败或缺图；`2` 参数/依赖错误。

`--space` 与 `--wiki` 至少给一个（除非 `--collect-only`）。
**不知道空间 ID 时不要瞎猜**，一条命令列出：

```bash
python3 -c "
import sys; sys.path.insert(0,'$SKILL/../feishu_doc_writer/scripts')
from feishu_client import FeishuClient
for s in FeishuClient().list_wiki_spaces().get('items', []):
    print(s['space_id'], s['name'])
"
```

## 工作流程

```
1. detect_source(url)：mp.weixin.qq.com → wx；juejin.cn → juejin；toutiao.com → toutiao；
   infoq.cn（含 xie.infoq.cn）→ infoq；blog.csdn.net → csdn；claude.com / anthropic.com → anthropic
2. 采集（各来源一次子进程调用）：
     wx       fetch_wx_article.py   抓页→预处理→转md→下图 全在脚本内
     juejin   fetch_juejin.py article --id <id> --output <workdir>
     toutiao  scrape_toutiao.py <url> <workdir>
     infoq    xie.infoq.cn  → fetch-infoq-knowledge/scripts/fetch_xie_article.py（渲染+下图一步到位）
              www.infoq.cn  → curl 抓页 → infoq_extract.py 抽正文 → html_to_md.py 转 md
     csdn     fetch-csdn-knowledge/scripts/fetch_csdn_article.py（抓页+抽正文+下图一步到位）
     anthropic fetch-anthropic-knowledge/scripts/fetch_anthropic_article.py（同上，curl 直取无需浏览器）
3. normalize_image_paths()：图片引用统一改成相对 md 的相对路径
4. 发布前把关：统计仍为远程 URL 的图片数，日志显式告警
5. publish.py 逐段转换、逐块按 index 写入 → 回读块列表校验
6. 写 summary.json；缺图即退出码 1，不把「缺图」当成功
```

## 七个采集器的已知特性

### 微信
`fetch_wx_article.py` 已封装全部 4 步（含图片下载 + 重试），末行输出 JSON：
`{"ok","title","md_path","images_failed","images_failed_urls","source_url"}`。

上游 `html_to_md.py` 必须传 `--source-url`，否则报错终止（刻意设计，
防止把本地路径当原文链接落盘）——脚本已内置。

### 掘金
- 只支持**单篇** `juejin.cn/post/:id`。专栏(`/column/`)、用户主页(`/user/`) 未接入，
  直接报「不是掘金文章链接」；需要时改走 `fetch-juejin-knowledge` 的手工流程。
- 图片是 `.awebp`，`fetch_juejin.py` 落盘的是**绝对路径**，由 Step 3 转相对路径。
- 耗时较长（20 张图约 1-2 分钟）。若跑一半被中断，多半是**外层命令超时被截断**，
  重跑即可（已下载的图片会复用）。

### 今日头条
- 必须有 Playwright + Chromium。用 `exec` 调用时给足超时（建议
  `timeoutSeconds: 300`，`yieldMs: 120000`），否则易被截断。
- 脚本无法从输出可靠判断成败，因此用「工作目录中是否新增 .md」判定。
- 渲染超时直接重试。

### InfoQ
- **两个域名行为不同**：`www.infoq.cn` 正文服务端已渲染，curl 即可（快）；
  `xie.infoq.cn`（写作社区）正文靠 JS 渲染，必须 Playwright（单篇约 1-2 分钟）。
- `xie.infoq.cn` 分支委托 `fetch-infoq-knowledge/scripts/fetch_xie_article.py`，
  图片本地化已内置，**必须显式传 `--output-dir <workdir>`**，否则会落到该技能的
  默认目录 `wk_data/infoq`。
- **重跑同一篇时不能用「新增 .md」判断成败**：文件名相同、文件已存在，
  集合相减会得到空而误判为失败。`collect_infoq` 因此**优先采用脚本 JSON 的
  `md_path`**，集合相减仅作兜底。（此 bug 曾在 `--collect-only` 的干净目录里被掩盖，
  真实发布时才暴露。）
- `www.infoq.cn` 有**速率限制**：短时间内连续请求会返回 403（页面仅 ~555 字节、
  标题 `403 Forbidden`）。**这不是「该文不可采」**——实测同一命令连续执行、参数完全一致，
  结果先 200 后 403 再 200，证明是临时限流，冷却几秒即恢复。
  因此 403 属**可重试**，`collect_infoq` 已内置退避重试（3 次、5s→15s），
  3 次仍失败才报错并说明是限流而非死链。
- `infoq.com`（英文站）返回 405 反爬页，**不支持**。
- 如需「InfoQ 作者全部文章清单」，用 `fetch-infoq-knowledge/scripts/fetch_xie_list.py`
  （单次上限 50 条），本编排器不管列表。

## 缺图是唯一的常见降级

图片下载失败**不中断流程**，但会让飞书文档出现空白。三层处理：

1. `localize_images()` 自带**重试**（默认 2 轮，成功过的不会重复下载）
2. 发布前打印 `! 仍有 N 张图片是远程链接`
3. 汇总打印 `⚠ 共 N 张图片未能本地化`，写入 `summary.json` 的 `missing_images`，
   **退出码 1**

汇报时必须主动说明「N 张图片未本地化」，不要让用户自己在文档里发现空位。

## 注意事项

### `--wiki` 覆盖写入不可逆
会**清空该文档原有全部块**再写入。文档里有人工补充内容时先确认再执行。

### 写入耗时
逐块写保顺序，145 段约 95 秒，两篇约 2 分钟。作为前台命令跑时，
用 `exec` 的 `yieldMs` 交后台，再用 `process` 的 `poll` 取结果，不要反复轮询。

### 权限同步延迟
知识库建节点报 `131006` 是权限同步中，**等几分钟重试**，不是代码问题；
`list_wiki_spaces` 返回空也可能是同一原因。

### 批量
`--url` 可重复传入，串行处理。需要「专栏全部文章」「掘金用户全部文章」时，
先用上游技能拿 URL 列表再分批调用；批量时显式指定稳定的 `--workdir`。

## 失败排查速查

| 现象 | 原因 | 处理 |
|---|---|---|
| `无法识别来源` | 域名不在支持列表 | 确认链接，或先本地采集再用 `--md` |
| 微信采集失败 | 反爬/链接失效 | 脚本已含重试；仍失败则换链接 |
| **飞书文档里图片是空白** | 图片未本地化 | 看日志「图片本地化：N 张失败」；重跑一次 |
| 头条未生成 md | Playwright 未装或渲染超时 | 装 Chromium；加大超时；重跑 |
| 掘金未生成 md | 图片下载耗时被外层超时截断 | 提高命令超时后重跑 |
| **CSDN 未取到正文** | `div#content_views` 未命中（页面结构变更/非文章页） | 确认是 `blog.csdn.net/.../article/details/<id>` 形式；结构变更需同步 `fetch_csdn_article.py` |
| **Anthropic 未取到正文** | `div.blog_post_content_wrap` 未命中 | 该站为 Webflow 静态页，正常无需浏览器；结构变更需同步 `fetch_anthropic_article.py` |
| **InfoQ 报「采集失败」但日志显示已采集** | 重跑同名文件，被「新增 .md」判断误伤 | 已修：优先用 JSON 的 `md_path`；确认脚本已更新 |
| InfoQ 主站未取到正文 | 该站**速率限制**（403 临时拦截） | 已内置退避重试 3 次；仍失败则稍等几分钟重试，非死链 |
| `131006` | 知识库权限同步中 | 等几分钟重试 |
| `99991672` | 缺 scope | 开放平台补权限并**发布应用版本** |
| 文档段落整体乱序 | 绕过了 publish.py 的逐段转换 | 必须走 `publish.py` |
| 429 | 逐块写入触发限流 | `publish.py` 已内置退避，勿绕过 |

## 依赖

- `curl`、`python3`、`requests`（发布侧）
- `playwright` + Chromium（头条采集、InfoQ 写作社区采集需要）
- 同目录技能：`fetch-wx-knowledge`、`fetch-toutiao-knowledge`、
  `fetch-juejin-knowledge`、`fetch-infoq-knowledge`、`fetch-csdn-knowledge`、
  `fetch-anthropic-knowledge`、`feishu_doc_writer`

## 文件说明

```
import_article_to_wiki/
└── scripts/
    ├── save_to_wiki.py       # 编排入口（唯一需要直接调的命令）
    ├── fetch_wx_article.py   # 微信单篇采集：抓页→预处理→转md→下图
    └── download_images.py    # 图片下载 + 相对路径替换（微信必需）
```
