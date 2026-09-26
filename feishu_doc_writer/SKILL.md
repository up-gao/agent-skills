---
name: feishu-doc-writer
description: >
  用 Python 调用飞书开放平台接口，把本地 Markdown 文件及配图发布到飞书云文档或知识库。
  当用户说"把这篇 Markdown 发到飞书"、"Markdown 转飞书文档"、"上传文档和图片到云文档"、
  "写入飞书知识库"、"创建飞书文档并写入内容"、给出 .md 文件或 wiki 链接要求发布/写入时，使用此技能。
  覆盖完整流程：解析 Markdown（正文/表格/图片分离）→ 定位或创建文档 → 写正文 → 补表格 →
  上传图片 → 回读校验。用 Python + tenant_access_token 直连开放平台，不依赖 OpenClaw 插件。
---

# 飞书云文档 / 知识库发布（Python 接口版）

用 Python 直接调飞书开放平台接口，把本地 Markdown 连同图片、表格**按原顺序**
发布到飞书云文档或知识库。

## 何时使用

| 用户意图 | 做法 |
|---|---|
| 本地 `.md` → 新建云文档 | `publish.py <file> --title "标题"` |
| 本地 `.md` → 覆盖已有文档 | `publish.py <file> --doc-id <token>` |
| 本地 `.md` → 写入知识库文档 | `publish.py <file> --wiki <wiki链接>` |
| 在知识库新建节点并写入 | `publish.py <file> --space <id> --title "标题"` |
| 先看会做什么 | `publish.py <file> --dry-run` |

## 核心原则：转换接口不保证顺序

**这是本技能存在的最主要原因。**

飞书的 Markdown → 块转换接口**不保证输出数组顺序**。实测：

```
输入：'# 标题\n\n> 引用1\n\n> 引用2\n\n第一段\n\n第二段'
输出：[第二段, 标题, 引用1, 引用2, 第一段]     ← 接口自己就乱了
```

后果：直接用转换结果的数组顺序写入，文章段落会整体错位；
图片、表格、引用块位置全部不对。

**解决方法：每个语义段单独转换。**

单段转换稳定输出 1 个块，顺序就完全由代码循环控制：

```
1. parse_markdown() 把源文件拆成有序语义段（标题/段落/引用/列表/代码块/表格/图片）
2. 每个语义段单独调 convert_blocks()   → 稳定 1 块
3. 按 index 递增逐块插入
```

已实测：145 段的长文，全文顺序零错误。

> 反例警示：曾用「一次性转换 + 逐块按 index 插入」，仍会乱序 ——
> 因为喂进去的数组本身已是乱的。小样本（如 5 个短段）可能恰好不乱，
> 容易误判为“已修复”。验证必须用真实长文。

## 快速开始

```bash
cd ~/.openclaw/wks/kg_fetcher/skills/feishu_doc_writer/scripts

# 预览（会列出段落顺序，确认无误再写）
python3 publish.py article.md --dry-run

# 发布到新文档
python3 publish.py article.md --title "文章标题"

# 覆盖已有文档
python3 publish.py article.md --doc-id <token>

# 写入知识库已有文档
python3 publish.py article.md --wiki https://xxx.feishu.cn/wiki/XXXX

# 在知识库新建节点并写入
python3 publish.py article.md --space 7689120845356092370 --title "标题"
```

成功输出：

```
元素        : 145 个（Markdown 片段 142 / 表格 1 / 图片 2）
[1/4] 使用已有文档 La2udhGgYoL3gaxKCcAc058tnlf
[2/4] 清空原有 146 块
[3/4] 完成：块 142 / 表格 1 / 图片 2，耗时 95s
[4/4] 校验：共 218 块，图片块 2（空 0），表格块 1

✓ 完成：https://feishu.cn/docx/La2udhGgYoL3gaxKCcAc058tnlf
```

## 凭据

按优先级自动解析，**不打印 appSecret**：

1. 环境变量 `FEISHU_APP_ID` / `FEISHU_APP_SECRET`
2. `~/.openclaw/openclaw.json` → `channels.feishu.accounts.<account>`
   （默认账号 `kg_fetcher_robot`，可用 `--account` 或 `FEISHU_ACCOUNT` 指定）

## 其余接口陷阱

### 单次写入上限 50 块

```
99992402 field validation failed
field_violations: [{"field": "children", "description": "the max len is 50"}]
```

`blocks/{doc_id}/children` 单次最多 50 块。本技能逐块写入，自然不越界。

> 用 OpenClaw 的 `feishu_doc` 插件时，这个错会被吞成裸的 `status code 400`，
> 无法二分定位。这是当时排查困难的根因。

### 图片必须三步锁定同一个块

`upload_all` 的 `parent_node` **必须是承载这张图的图片块 `block_id`，不是 `document_id`**。

传 `document_id` 会报 `1770013 relation mismatch`。正确顺序：

```
1. 在目标 index 生成空图片块
2. 上传素材，parent_node = 该块 block_id
3. replace_image 把 token 填回该块
```

图片块**不能**在创建时直接带 token（报 `1770001 invalid param`）。

### wiki 链接的 token 不是文档 token

```python
obj, otype, node_token, space_id = client.resolve_wiki_doc(wiki_token)
```

知识库 URL 里的是 `node_token`，写正文要用 `obj_token`。

### 表格行列数必须显式传

漏传 `row_size` / `column_size` 会 400。单元格按「行优先」，
从表格块声明的 `cells` 数组取顺序。

### 429 限流

逐块写入请求数多，会触发限流。客户端已内置退避重试（尊重 `Retry-After`，
否则指数退避），不要绕过。

## 校验为什么看 token 而不是 `images_processed`

飞书转换接口返回的 `images_processed` 字段**恒为 0**，不反映真实情况。
唯一可靠的判断是回读块列表，检查每个 `block_type: 27` 的 `image.token` 是否非空。

## 知识库能力（全部实测通过）

| 操作 | 权限层级 | 结果 |
|---|---|---|
| 解析 wiki 链接 | 节点级 | ✅ |
| 读取知识库文档 | 节点级 | ✅ |
| 写入知识库已有文档 | 节点级 | ✅ |
| 新建节点 `create_wiki_node` | 空间级 | ✅ |
| 列空间列表 `list_wiki_spaces` | 空间级 | ✅ |

### 权限刚开通时可能报 131006

```
131006 permission denied: wiki space permission denied, tenant needs edit permission.
```

**如果刚开完 `wiki:wiki` 权限并发布版本，这个错可能持续几分钟** ——
飞书的权限变更需要时间同步到各接入点。

**不要据此判断为产品限制。** 实测：同一套代码和凭据，在权限同步完成后立刻成功，
无需任何额外配置。遇到 `131006` 先等几分钟再试，而不是改代码。

同理，`list_wiki_spaces` 返回空列表也可能是同步未完成。

## 常见失败

| 现象 | 错误码 | 处理 |
|---|---|---|
| **段落/图表整体乱序** | – | **转换接口不保序，必须逐段转换** |
| 图片空占位框 | – | `parent_node` 传错，必须传 block_id |
| 图片关系不匹配 | `1770013` | `parent_node` 必须传 block_id |
| 图片参数非法 | `1770001` | 不要在建块时带 token |
| 上传参数错误 | `1061002` | 缺 `file_name` / `size` |
| 上传被拒 | `1061004` | 缺 `parent_node` |
| 写入被拒 | `99992402` | 超 50 块，或 `content` 为空 |
| 写入被限流 | `429` | 客户端自动退避重试 |
| 知识库建节点被拒 | `131006` | **权限同步中，等几分钟重试** |
| 缺 scope | `99991672` | 开放平台补开并**发布应用版本** |
| token 过期 | `99991663` | 客户端自动刷新重试一次 |
| 同目录并发建文档 | `1770036` | `folder locked`，同文件夹下串行调用 |

## 已知限制

### 长文发布耗时

逐段转换 + 逐块插入，请求数为 `2 × 段落数`。实测 145 段约 95 秒。
这是保序的代价，请预期等待。若需提速，只能牺牲顺序保证。

### 表格单元格写入仍逐格调用

一篇 6×6 表格 = 36 次请求。大表格会明显变慢。

## 验证顺序是否真的正确

**块数正确 ≠ 顺序正确。** 必须程序化比对，不能目测抽样：

```python
# 读回文档顶层块，与 parse_markdown() 的结果逐项比对
# 归一化空白后比较前 30 字符，检查类型（md/table/image）是否对应
```

实测曾出现：块数完全正确，但段落顺序整体错位。

## 文件说明

```
feishu_doc_writer/
├── SKILL.md                  # 本文件
├── readme.md                 # 给人看的说明
└── scripts/
    ├── feishu_client.py      # API 客户端（鉴权/文档/图片/表格/知识库/权限）
    └── publish.py            # 命令行入口，解析与按序写入
```

## 依赖

Python 3.8+ 与 `requests`。无需其他第三方库。

## 与插件的区别

本技能走**开放平台 HTTP 接口**，不依赖 OpenClaw 的 `feishu_doc` 插件工具：

- 好处：可离线跑、可进定时任务、**错误码与 `field_violations` 完整可见**、顺序可控
- 代价：需自己维护鉴权与分页，请求数多于插件
- 选择：交互式写短文档用插件更省事；长文/批量/需要保序时用本技能

