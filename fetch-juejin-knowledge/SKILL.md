---
name: fetch-juejin-knowledge
description: 将掘金（juejin.cn）文章/专栏/用户文章列表转换为本地 Markdown 文件，图片全部下载到本地。当用户提到掘金、juejin、掘金文章、掘金专栏、掘金博主文章时使用此技能。
---

# 掘金文章采集

将掘金（juejin.cn）文章转换为本地 Markdown 文件，图片全部下载到本地，路径使用绝对路径。

## Step 0: 识别链接类型

在处理之前，先判断用户提供的 URL 或输入类型：

| URL 特征 | 类型 | 处理流程 |
|----------|------|----------|
| `juejin.cn/post/:id` | 单篇文章 | 走 Step 1-7 单篇流程 |
| `juejin.cn/column/:id` | 专栏 | 走下方「专栏采集」流程 |
| `juejin.cn/user/:id` | 用户主页 | 走下方「用户文章列表采集」流程 |
| 多个 URL | 批量 | 逐篇走单篇流程，共享图片目录 |

如果用户既有专栏链接又有单篇链接，先采集专栏全部文章，再采集单篇。

## 单篇文章采集

按顺序执行。遇到问题及时向用户报告。

### Step 1: 收集参数

- **URL 或 URL 列表**（必填）— 掘金文章链接（`juejin.cn/post/:id`），支持多个
- **输出目录** — Markdown 文件保存位置。默认：当前目录
- **图片目录** — 静态资源存放位置。默认：输出目录下的 `images/`

用户只给了链接就用默认值，无需多问，确认后直接开始。

### Step 2: 从 URL 提取文章 ID

掘金文章 URL 格式为 `https://juejin.cn/post/:article_id`，提取最后的数字 ID：

```bash
ARTICLE_ID=$(echo '<URL>' | grep -oP 'post/\K\d+')
```

对于多个 URL，逐个提取，用空格或换行分隔。

### Step 3: 调用 API 获取文章详情

使用掘金公开的内容 API 获取文章完整数据（标题、正文 Markdown、元数据、图片列表）：

```bash
ARTICLE_ID="<article_id>"
curl -sL --max-time 30 \
  -H 'Content-Type: application/json' \
  'https://api.juejin.cn/content_api/v1/article/detail?aid=2608&uuid=&spider=0' \
  -d "{\"article_id\":\"$ARTICLE_ID\",\"client_type\":2608}" \
  -o /tmp/juejin_article.json
```

API 返回 JSON，关键字段：
- `data.article_info.title` — 文章标题
- `data.article_info.mark_content` — 文章正文（已是 Markdown 格式）
- `data.article_info.brief_content` — 文章摘要
- `data.article_info.ctime` — 创建时间（Unix 时间戳）
- `data.article_info.view_count` — 阅读数
- `data.article_info.digg_count` — 点赞数
- `data.article_info.collect_count` — 收藏数
- `data.article_info.comment_count` — 评论数
- `data.article_info.tag_ids` — 标签 ID 列表
- `data.article_info.pics[]` — 图片列表（`pic_url` / `pic_backup_url`）
- `data.author_user_info.user_name` — 作者名
- `data.author_user_info.user_id` — 作者 ID

如果 `err_no` 不为 0，报告错误并终止。

### Step 4: 组装 Markdown 文件

使用 Python 脚本组装输出文件。从 API 返回的 JSON 中提取数据，生成带元数据头的 Markdown：

```python
import json, os, re
from datetime import datetime

api_json = '/tmp/juejin_article.json'
output_dir = '<输出目录>'
source_url = '<原文URL>'

with open(api_json, 'r') as f:
    data = json.load(f)

if data.get('err_no') != 0:
    print(f"API 错误: [{data['err_no']}] {data['err_msg']}")
    exit(1)

detail = data['data']
info = detail['article_info']
author = detail['author_user_info']

title = info['title']
mark_content = info.get('mark_content', '') or info.get('brief_content', '')
ctime = datetime.fromtimestamp(int(info['ctime'])).strftime('%Y-%m-%d %H:%M:%S')
tags = ', '.join([str(t) for t in info.get('tag_ids', [])])
view_count = info.get('view_count', 0)
digg_count = info.get('digg_count', 0)
collect_count = info.get('collect_count', 0)
comment_count = info.get('comment_count', 0)
author_name = author.get('user_name', '未知')

# 生成文件名：使用标题做 slug
def slugify(s):
    s = re.sub(r'[^\w\s-]', '', s)
    s = re.sub(r'[-\s]+', '-', s)
    return s.strip('-')[:80]

filename = slugify(title) or 'untitled'

md_content = f"""# {title}

> 原文链接：{source_url}
> 作者：{author_name} | 发布时间：{ctime}
> 标签：{tags}
> 阅读：{view_count} | 点赞：{digg_count} | 收藏：{collect_count} | 评论：{comment_count}

{mark_content}
"""

output_path = os.path.join(output_dir, f'{filename}.md')
os.makedirs(output_dir, exist_ok=True)
with open(output_path, 'w', encoding='utf-8') as f:
    f.write(md_content)

print(f'Markdown 已保存: {output_path}')
print(f'文章标题: {title}')
print(f'正文字符数: {len(mark_content)}')
print(f'图片数: {mark_content.count("![")} ')
```

### Step 5: 下载图片

从生成的 Markdown 中提取所有图片 URL，下载到本地。使用时间戳命名确保唯一性：

```python
import re, os, time, json, subprocess
from urllib.parse import urlparse, unquote
from datetime import datetime

md_file = '<output>.md'
static_dir = '<图片目录>'
os.makedirs(static_dir, exist_ok=True)

with open(md_file, 'r', encoding='utf-8') as f:
    content = f.read()

# 提取所有图片 URL（去重）
image_urls = list(dict.fromkeys(re.findall(r'!\[.*?\]\(([^)]+)\)', content)))
print(f"发现 {len(image_urls)} 张图片\n")

url_map = {}
for i, url in enumerate(image_urls):
    # 跳过已下载的本地路径
    if not url.startswith('http'):
        continue

    parsed = urlparse(url)
    path = unquote(parsed.path)
    ext = os.path.splitext(path)[1].lower()

    # 掘金图片可能是 webp/awebp 格式，保留原扩展名
    if not ext or ext not in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp', '.awebp'):
        ext = '.png'  # 默认

    # 生成时间戳文件名: image-YYYYMMDDHHmmssSSS.ext
    now = datetime.now()
    ts = now.strftime('%Y%m%d%H%M%S') + f'{now.microsecond // 1000:03d}'
    new_filename = f"image-{ts}{ext}"

    result = subprocess.run(
        ['curl', '-sL', '--max-time', '15',
         '-H', 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
         '-o', os.path.join(static_dir, new_filename), url],
        capture_output=True, text=True
    )

    if result.returncode == 0:
        # 验证文件非空
        filepath = os.path.join(static_dir, new_filename)
        if os.path.getsize(filepath) > 0:
            url_map[url] = new_filename
            print(f"OK [{i+1}/{len(image_urls)}]: {new_filename}")
        else:
            os.remove(filepath)
            print(f"EMPTY [{i+1}/{len(image_urls)}]: {url[:80]}")
    else:
        print(f"FAIL [{i+1}/{len(image_urls)}]: {url[:80]}")

print(f"\n下载成功: {len(url_map)}/{len(image_urls)}")

with open('/tmp/juejin_url_map.json', 'w') as f:
    json.dump(url_map, f)
```

要点：
- 掘金图片托管在 `p*-xtjj-sign.byteimg.com`，使用标准 curl 即可下载
- 图片可能为 `.awebp` 格式，保留原始扩展名
- 下载失败时保留原始 URL，继续处理下一张

### Step 6: 替换图片路径为绝对路径

```python
import re, json, os

md_file = '<output>.md'
static_dir = os.path.abspath('<图片目录>')

with open('/tmp/juejin_url_map.json', 'r') as f:
    url_map = json.load(f)

with open(md_file, 'r', encoding='utf-8') as f:
    content = f.read()

def replace_img(match):
    alt = match.group(1)
    url = match.group(2)
    if url in url_map:
        abs_path = os.path.join(static_dir, url_map[url])
        return f'![{alt}]({abs_path})'
    # 已经是本地路径，保留
    if not url.startswith('http'):
        return match.group(0)
    print(f"WARNING: 未下载的图片: {url[:80]}")
    return match.group(0)

content = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', replace_img, content)

with open(md_file, 'w', encoding='utf-8') as f:
    f.write(content)

print('图片路径已替换为绝对路径')
```

**为什么用绝对路径？** 绝对路径确保 Markdown 文件在任意位置打开时图片都能正确显示，不会因为文件移动导致图片引用失效。

### Step 7: 验证并报告

```bash
echo "=== 采集报告 ==="
echo "输出文件: <output>.md ($(wc -c < <output>.md) bytes)"
echo "图片引用数: $(grep -c '!\[.*\](.*)' <output>.md)"
echo "图片文件数: $(ls <图片目录> 2>/dev/null | wc -l)"
echo ""

# 破损检查
broken=0
while IFS= read -r ref; do
  path=$(echo "$ref" | grep -oP '(?<=\().*(?=\))')
  if [ ! -f "$path" ]; then
    echo "BROKEN: $ref"
    ((broken++))
  fi
done < <(grep -oP '!\[.*?\]\(([^)]+)\)' <output>.md)
echo "破损引用: $broken"

# 清理临时文件
rm -f /tmp/juejin_article.json /tmp/juejin_url_map.json
```

向用户报告：
- 文章标题和作者
- 输出文件路径和大小
- 下载图片数量和总大小
- 原文链接（已在 Markdown 标题下方）
- 任何失败的图片 URL

## 输出示例

```
输出目录/
├── 腾讯一面-Agent开发面试题.md
└── images/
    ├── image-20260802150432001.png
    ├── image-20260802150432002.awebp
    └── ...

# 文章标题

> 原文链接：https://juejin.cn/post/xxx
> 作者：xxx | 发布时间：2024-01-15 10:30:00
> 标签：123456, 789012
> 阅读：1000 | 点赞：50 | 收藏：20 | 评论：10

正文内容...

![图片描述](/绝对路径/images/image-20260802150432001.png)
```

## 批量采集

用户提供多个单篇文章 URL 时，循环处理每个链接：
- 每个链接间隔 2 秒避免触发限流
- 同一批次的文章共享图片目录（图片自动去重）
- 每篇文章独立一个 `.md` 文件
- 最后汇总报告：成功/失败数量、总图片数、失败列表

失败的文章 URL 和错误原因记录到 `<输出目录>/fetch_errors.log`。

## 用户文章列表采集

当用户提供掘金用户主页链接（`juejin.cn/user/:id`）时，拉取该用户全部已发布文章。

### 用户 Step 1: 提取用户 ID

```bash
USER_ID=$(echo '<URL>' | grep -oP 'user/\K\d+')
```

### 用户 Step 2: 分页拉取全部文章

使用 `query_list` API，自动处理分页：

```python
import json, time, subprocess, os

user_id = '<USER_ID>'
output_dir = '<输出目录>'
all_articles = []
cursor = '0'

while True:
    result = subprocess.run([
        'curl', '-sL', '--max-time', '30',
        '-H', 'Content-Type: application/json',
        f'https://api.juejin.cn/content_api/v1/article/query_list?aid=2608&uuid=&spider=0',
        '-d', json.dumps({
            'user_id': user_id,
            'cursor': cursor,
            'sort_type': 2,
            'client_type': 2608
        })
    ], capture_output=True, text=True)

    data = json.loads(result.stdout)
    if data.get('err_no') != 0:
        print(f"API 错误: {data.get('err_msg')}")
        break

    articles = data.get('data', [])
    if not articles:
        break

    for a in articles:
        all_articles.append(f"https://juejin.cn/post/{a['article_id']}")

    cursor = str(data.get('cursor', ''))
    has_more = data.get('has_more', False)
    print(f"已获取 {len(all_articles)} 篇文章...")

    if not has_more or not cursor or cursor == '0':
        break
    time.sleep(1)  # 分页间隔

print(f"\n共找到 {len(all_articles)} 篇文章")

# 保存到临时文件，供批量采集使用
with open('/tmp/juejin_article_urls.txt', 'w') as f:
    f.write('\n'.join(all_articles))
```

### 用户 Step 3: 批量采集

读取 `/tmp/juejin_article_urls.txt`，对每篇文章执行 Step 2-7（单篇采集流程），间隔 2 秒。

### 用户 Step 4: 汇总报告

```
用户文章采集完成: xxx
─────────────────────────────
成功: 10/10 篇
总图片: 45 张
输出目录: /output/xxx/
```

## 专栏采集

当用户提供掘金专栏链接（`juejin.cn/column/:id`）时，尝试获取专栏下全部文章。

### 专栏 Step 1: 提取专栏 ID

```bash
COLUMN_ID=$(echo '<URL>' | grep -oP 'column/\K\d+')
```

### 专栏 Step 2: 尝试获取专栏文章列表

掘金没有公开的专栏 API。按以下顺序尝试：

**方案 A — 从专栏页面提取 SSR 数据（优先）：**

```bash
curl -sL --max-time 30 \
  -H 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36' \
  -H 'Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8' \
  -H 'Accept-Language: zh-CN,zh;q=0.9' \
  -o /tmp/juejin_column.html \
  'https://juejin.cn/column/<COLUMN_ID>'
```

然后从 HTML 中提取 `window.__NUXT__` 状态中的文章列表。如果页面返回"找不到页面"，则说明被反爬拦截。

**方案 B — 转为用户文章列表采集（备用）：**

如果方案 A 失败，告诉用户专栏页面无法直接访问，请提供专栏作者的用户主页链接（`juejin.cn/user/:id`），转为用户文章列表采集流程。

如果用户提供了专栏作者的 user_id，直接使用用户文章列表采集流程。

### 专栏 Step 3: 批量采集

获取文章 URL 列表后，对每篇文章执行 Step 2-7，间隔 2 秒。

### 专栏 Step 4: 汇总报告

格式同用户文章列表采集。

## 掘金 API 特点

了解这些有助于排查问题：

- **文章正文格式**：API 返回的 `mark_content` 字段已是 Markdown 格式，无需额外转换
- **图片格式**：掘金图片通常为 `.awebp` 格式（AVIF 的 WebP 变体），部分预览器可能不支持，但保留原格式
- **图片托管**：CDN 域名为 `p*-xtjj-sign.byteimg.com`（`p1`, `p3`, `p6`, `p9` 等），图片 URL 含有签名和过期时间
- **API 无需登录**：文章详情和列表 API 均不需要 Cookie，公共参数 `aid=2608&uuid=&spider=0` 即可
- **反爬机制**：掘金页面（HTML）有反爬保护，curl 直接访问文章/专栏页面会返回"找不到页面"，但 API 接口正常
- **标签信息**：API 返回 `tag_ids` 是标签 ID 数组，如需标签名称需额外调用标签接口

## 限速策略

- 单篇文章内图片下载无间隔
- 批量采集中文章之间间隔 **2 秒**
- 用户文章列表分页之间间隔 **1 秒**
- 如果遇到 API 错误，等待 5 秒后重试，最多 3 次

## 失败处理

失败的 URL 和原因记录到 `<输出目录>/fetch_errors.log`：

```
2026-08-02 15:04:32 | FAIL | https://juejin.cn/post/xxx | API error: [404] 内容为空
2026-08-02 15:04:45 | FAIL | https://juejin.cn/post/yyy | 图片下载失败: https://p3-xtjj-sign.byteimg.com/...
```

批量采集结束后，向用户展示失败统计。如果全部成功则无需展示日志。

## 依赖

- `curl` — 调用 API 和下载图片
- `python3` — 数据处理、Markdown 组装、路径替换
