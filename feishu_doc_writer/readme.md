# feishu_doc_writer

用 Python 调用飞书开放平台接口，把本地 Markdown 文件及配图发布到飞书云文档或知识库。

## 这个技能解决什么问题

**飞书的块转换接口不是通用 Markdown 解析器，而且不保证输出顺序。**
本技能把「逐段解析 → 按序写入 → 补表格 → 传图片 → 校验」固化成一条命令，
保证文档版面与源文件一致。

四类最容易踩的问题：

- **转换接口输出乱序**（最严重）—— 段落、图片、表格位置全错，必须逐段转换
- **单次写入上限 50 块** —— 超了整体失败，且报错不告诉你真正原因
- **表格退化成纯文本** —— 转换接口不认 Markdown 表格语法
- **图片变成空占位块** —— 必须三步锁定同一个块，`parent_node` 传错就 `relation mismatch`
- **wiki 链接 token ≠ 文档 token** —— 知识库要先用 `node_token` 换 `obj_token`

## 为什么用 Python 而不是插件

OpenClaw 有 `feishu_doc` 插件工具，交互式写文档更省事。这个技能走开放平台 HTTP 接口，
适合另外几类场景：

- 批量处理大量 Markdown 文件
- 进定时任务 / CI 流程
- **需要看到飞书原始错误码和 `field_violations`**（插件只回一句 `status code 400`）
- 需要版本管理和可复现的执行记录

第三条是实测得来的重要差异 —— 排查时插件把 `the max len is 50` 这个关键信息吞掉了，
导致长时间无法定位。

## 快速开始

```bash
cd ~/.openclaw/wks/kg_fetcher/skills/feishu_doc_writer/scripts

# 先预览，不写入
python3 publish.py article.md --dry-run

# 发布到新文档
python3 publish.py article.md --title "文章标题"

# 写入知识库已有文档
python3 publish.py article.md --wiki https://xxx.feishu.cn/wiki/BLZxwpQLNiBefYkwcDEcDrTanlb
```

## 凭据来源

按优先级自动解析，**绝不打印 appSecret**：

1. 环境变量 `FEISHU_APP_ID` / `FEISHU_APP_SECRET`
2. `~/.openclaw/openclaw.json` → `channels.feishu.accounts.<account>`

默认账号 `kg_fetcher_robot`，可用 `--account` 或 `FEISHU_ACCOUNT` 指定。

## 完整流程

| 步骤 | 做了什么 | 完成标准 |
|---|---|---|
| 1 解析 | 把源文件拆成有序语义段 | `--dry-run` 列出的顺序与源文件一致 |
| 2 定位/创建 | 新建文档 / doc-id / wiki 链接 / 新建节点 | 拿到 `document_id` |
| 3 清空 | 覆盖模式先删原有子块 | 返回删除块数 |
| 4 按序写入 | **逐段转换 + 逐块 index 插入** | 块数匹配，顺序正确 |
| 5 校验 | 回读块列表 | 空图片块为 0，段落顺序无误 |

**第 1 步的粒度是关键。** `parse_markdown()` 必须把源文件切成**独立语义段**
（标题、单个段落、单条引用、列表块、代码块、表格、图片各算一段），
而不是按空行合并成大块 —— 块越大，转换后乱序风险越高。

实测：同一篇文章，粗粒度切分得到 7 个大块会乱序；细粒度 145 段则零错误。

## 核心：转换接口不保证顺序

飞书的 Markdown → 块转换接口**不保证输出数组顺序**：

```
输入：'# 标题\n\n> 引用1\n\n> 引用2\n\n第一段\n\n第二段'
输出：[第二段, 标题, 引用1, 引用2, 第一段]     ← 接口自己就乱了
```

后果：直接用转换结果的数组顺序写入，文章段落整体错位，引用块、图片、表格
全部不在正确位置。这是「版面乱序」的根本原因。

**解决方法：每个语义段单独转换。** 单段稳定输出 1 个块，顺序完全由代码控制。

已实测：145 段长文，全文顺序零错误。

> **反例警示**：曾用「一次性转换 + 逐块按 index 插入」，仍会乱序 ——
> 因为喂进去的数组本身已是乱的。小样本（如 5 个短段）可能恰好不乱，
> 容易误判为「已修复」。验证必须用真实长文，且要程序化比对全文，不能目测抽样。

## 四个接口陷阱（实测）

### 1. 转换接口不保序（最重要）

见上文。必须逐段转换，不能一次性转换整篇。

### 2. 单次写入上限 50 块

```
99992402 field validation failed
field_violations: [{"field": "children", "description": "the max len is 50"}]
```

`blocks/{doc_id}/children` 单次最多 50 块，超出整体失败。
`append_markdown()` 已自动分批，不要绕过。

### 2. 图片三步必须锁定同一个块

`upload_all` 的 `parent_node` **必须是图片块 `block_id`，不是 `document_id`**：

```
parent_node = 图片块 block_id  → ✅
parent_node = document_id      → ❌ 1770013 relation mismatch
```

正确顺序：

```
1. append_markdown('![](name.png)')      生成空图片块
2. list_blocks 找到 token 为空的 block_id
3. upload_image(path, parent_node=块id)  上传
4. set_image_token(doc_id, 块id, token)  填 token
```

图片块**不能**在创建时直接带 token（报 `1770001 invalid param`）。

### 3. wiki 链接的 token 不是文档 token

```python
obj, otype, node_token, space_id = client.resolve_wiki_doc(wiki_token)
```

知识库 URL 里的是 `node_token`，写正文要用 `obj_token`。

### 4. 表格行列数必须显式传

漏传 `row_size` / `column_size` 会 400。填单元格按「行优先」，
从表格块声明的 `cells` 数组取顺序。

### 校验为什么看 token 而不是 `images_processed`

飞书转换接口返回的 `images_processed` 字段**恒为 0**，不反映真实情况。
唯一可靠的判断是回读块列表，检查每个 `block_type: 27` 的 `image.token` 是否非空。

## 知识库能力（全部实测通过）

| 操作 | 权限层级 | 结果 |
|---|---|---|
| 解析 wiki 链接 | 节点级 | ✅ |
| 读取知识库文档 | 节点级 | ✅ |
| 写入知识库已有文档 | 节点级 | ✅ |
| 新建节点 | 空间级 | ✅ |
| 列空间列表 | 空间级 | ✅ |

### 权限刚开通时可能报 131006

```
131006 permission denied: wiki space permission denied, tenant needs edit permission.
```

若刚开完 `wiki:wiki` 权限并发布版本，**这个错可能在几分钟内持续存在**，
因为飞书权限变更需要时间同步。

**不要据此判断为产品限制。** 实测：同一套代码和凭据，在权限同步完成后立刻成功，
无需额外配置。遇到 `131006` 先等几分钟再试。

同理，`list_wiki_spaces` 返回空也可能是同步未完成。

### 两种写入方式

```bash
# 已有文档（用户提供链接）
python3 publish.py article.md --wiki https://xxx.feishu.cn/wiki/XXXX

# 在指定空间新建节点
python3 publish.py article.md --space 7689120845356092370 --title "标题"
```

## 已知限制

**图片块和表格块只能追加到文档末尾**，无法插入正文中间。

两者都要通过 `blocks/{doc_id}/children` 创建，该接口是末尾追加；
转换接口虽能在正文生成占位块，但占位块无内容、无法填值。
所以含图表的文章，最终文末会集中出现图表。需要精确位置只能写完后手工拖动。

## 常见失败

| 现象 | 错误码 | 处理 |
|---|---|---|
| 写入无差别失败 | `99992402` | 块数超 50，检查是否绕过分批 |
| 图片空占位框 | – | `parent_node` 传错 |
| 图片关系不匹配 | `1770013` | `parent_node` 必须传 block_id |
| 图片参数非法 | `1770001` | 不要在建块时带 token |
| 上传参数错误 | `1061002` | 缺 `file_name` / `size` |
| 上传被拒 | `1061004` | 缺 `parent_node` |
| 知识库建节点被拒 | `131006` | **权限同步中，等几分钟重试** |
| 缺 scope | `99991672` | 开放平台补开并**发布应用版本** |
| token 过期 | `99991663` | 客户端自动刷新重试一次 |
| 同目录并发建文档 | `1770036` | `folder locked`，同文件夹下串行调用 |

## 环境排查

若出现 **创建/读取正常但写入无差别失败、且与内容规模无关**，先跑探针：

```bash
python3 -c "
import sys; sys.path.insert(0,'.')
from feishu_client import FeishuClient
c = FeishuClient()
d = c.create_document('probe')
print('创建:', d['document_id'])
print('写入:', c.append_markdown(d['document_id'], '# 探针\n\n一段文本。'))
"
```

创建成功而写入失败 → 检查应用后台的 scope 与发布状态。排查完删掉探针文档。

## 目录结构

```
feishu_doc_writer/
├── SKILL.md                  # Agent 执行时读取的主流程
├── readme.md                 # 本文件，给人看的说明
└── scripts/
    ├── feishu_client.py      # API 客户端：鉴权/文档/图片/表格/知识库/权限
    └── publish.py            # 命令行入口，编排完整流程
```

## 依赖

Python 3.8+ 与 `requests`，无其他第三方库。

## 实测记录

### 2026-09-24 全链路验证通过

源文件：`wk_data/wechat/推荐系统架构（2）-算法的工程演进.md`（295 行）

| 能力 | 结果 |
|---|---|
| 解析（正文 142 块 / 表格 1 个 / 图片 2 张） | ✅ |
| 创建文档 | ✅ |
| 写入正文（3 批，自动分批） | ✅ 142 块 |
| 写表格（6×6） | ✅ 36 单元格 |
| 上传并插入图片 | ✅ 2/2，空图片块 0 |
| 回读校验 | ✅ 218 块 |
| 解析 wiki 链接 | ✅ |
| 读取知识库文档 | ✅ |
| 写入知识库文档 | ✅ |
| 在知识库新建节点 | ✅ |

> 注：`131006` 曾一度被误判为飞书产品限制。实际是权限变更需要时间同步 ——
> 同一套代码在同步完成后立即成功。教训：**错误码不变，不等于问题性质不变**。

## 维护

- 飞书接口路径变更时，同步更新 `feishu_client.py`
- 新的错误码补进 `ERROR_HINTS` 与「常见失败」表
- 新实测结论追加到「实测记录」，标注日期
