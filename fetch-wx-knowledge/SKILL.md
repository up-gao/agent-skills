---
name: fetch-wx-knowledge
description: >
  采集微信公众号（mp.weixin.qq.com）文章，提取文章主体内容并转换为格式清晰的 Markdown 文件。
  当用户提供微信公众号文章链接时，无论说的是"采集"、"下载"、"保存"、"提取"、"导出"、
  "转换"，还是"收藏公众号文章"、"把微信文章保存到本地"，都必须触发此技能。
  支持单篇文章采集、URL 列表批量采集、以及专栏/合集（mp.appmsgalbum）全部文章采集。
  输出 Markdown 文件带原文链接、图片使用绝对路径、
  图片自动重命名为 image-YYYYMMDDHHmmssSSS.ext 格式（时间戳命名）。
  也适用于任何 mp.weixin.qq.com 域名的文章链接。
---

# 微信公众号文章采集

将微信公众号文章转换为本地 Markdown 文件，图片全部下载到本地，路径使用绝对路径。


## Step 0: 识别链接类型

在处理之前，先判断用户提供的 URL 类型：

| URL 特征 | 类型 | 处理流程 |
|----------|------|----------|
| `mp.weixin.qq.com/s/...` | 单篇文章 | 走 Step 1-7 单篇流程 |
| `mp.appmsgalbum?...` 或含 `action=getalbum` | 专栏/合集 | 走下方「专栏采集」流程 |
| 多个 URL | 批量 | 逐篇走单篇流程，共享图片目录 |

如果用户既有专栏链接又有单篇链接，先采集专栏全部文章，再采集单篇。

## 单篇文章采集

按顺序执行。遇到问题及时向用户报告。

### Step 1: 收集参数

- **URL 或 URL 列表**（必填）— 微信公众号文章链接（`mp.weixin.qq.com`）
- **输出目录** — Markdown 文件保存位置。默认：当前目录
- **图片目录** — 静态资源存放位置。默认：输出目录下的 `images/`

用户只给了链接就用默认值，无需多问，确认后直接开始。

### Step 2: 抓取页面

微信文章需要用移动端 User-Agent 才能正确抓取（否则可能被重定向到空白页）：

```bash
curl -sL --max-time 30 \
  -H 'User-Agent: Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15' \
  -o /tmp/wx_page.html \
  '<URL>'
```

对于 URL 列表，逐个抓取，成功/失败都汇报。

### Step 3: 微信页面预处理

微信文章和普通网页有几个关键不同：标题在 `og:title` meta 标签里、正文在 `rich_media_content` / `js_content` 容器中、图片用 `data-src` 懒加载。使用预处理脚本清理：

```bash
python3 <skill-base>/scripts/wx_preprocess.py \
  --input /tmp/wx_page.html \
  --output /tmp/wx_clean.html
```

脚本自动完成：
- 提取 `og:title` 作为文章标题
- 提取 `rich_media_content` 或 `js_content` 中的正文
- 将 `data-src` 转换为 `src`（微信懒加载图片的原始链接是 `data-src`）
- 修复协议相对 URL（`//` → `https://`）
- 剥离微信页面的大量 JS/CSS 代码

输出标题格式如 `"xxx文章标题"`，这个标题用于后续步骤。

### Step 4: 转换为 Markdown

复用 `fetch-knowledge` 技能的转换脚本：

```bash
python3 <fetch-knowledge-base>/scripts/html_to_md.py \
  --input /tmp/wx_clean.html \
  --output <output-dir>/<slug>.md \
  --title "<标题>" \
  --base-url "https://mp.weixin.qq.com" \
  --source-url "<原文URL>"
```

> `<fetch-knowledge-base>` 路径：`/Users/gaofeng/.claude/skills/fetch-knowledge`

文件命名：用标题做 slug（小写、连字符、去特殊符号）。中文标题保留中文即可。

### Step 5: 下载图片

微信图片托管在 `mmbiz.qpic.cn`，下载后重新命名为 `image-YYYYMMDDHHmmssSSS.ext` 格式：

```python
import re, os, time, json, subprocess
from urllib.parse import urlparse, unquote

md_file = '<output>.md'
static_dir = '<图片目录>'

os.makedirs(static_dir, exist_ok=True)

with open(md_file, 'r') as f:
    content = f.read()

image_urls = list(dict.fromkeys(re.findall(r'!\[.*?\]\(([^)]+)\)', content)))
print(f"Found {len(image_urls)} unique images\n")

url_map = {}
for i, url in enumerate(image_urls):
    if not url.startswith('http'):
        full_url = 'https://mp.weixin.qq.com' + url if url.startswith('/') else url
    else:
        full_url = url

    parsed = urlparse(full_url)
    path = unquote(parsed.path)
    ext = os.path.splitext(path)[1].lower()

    # WeChat uses wx_fmt parameter to indicate format
    fmt_match = re.search(r'wx_fmt=(\w+)', full_url)
    if fmt_match:
        fmt = fmt_match.group(1).lower()
        ext = f'.{fmt}' if fmt in ('jpeg', 'png', 'gif', 'webp', 'jpg') else ext

    if not ext or ext not in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp'):
        ext = '.jpeg'  # WeChat default

    # Generate timestamp: YYYYMMDDHHmmss + milliseconds(3 digits)
    # e.g., image-20260528104203839.png
    from datetime import datetime
    now = datetime.now()
    ts = now.strftime('%Y%m%d%H%M%S') + f'{now.microsecond // 1000:03d}'
    new_filename = f"image-{ts}{ext}"

    result = subprocess.run(
        ['curl', '-sL', '--max-time', '15',
         '-H', 'User-Agent: Mozilla/5.0 (compatible; WeChat/1.0)',
         '-o', os.path.join(static_dir, new_filename), full_url],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        url_map[url] = new_filename
        print(f"OK [{i+1}/{len(image_urls)}]: {new_filename}")
    else:
        print(f"FAIL [{i+1}/{len(image_urls)}]: {full_url[:80]}")

print(f"\nDownloaded: {len(url_map)}/{len(image_urls)}")

with open('/tmp/wx_url_map.json', 'w') as f:
    json.dump(url_map, f)
```

重点：
- 微信图片通过 `wx_fmt` 参数指定格式（jpeg/png/gif/webp），以此确定扩展名
- 下载时带上微信 Referer UA 避免被拦截
- 时间戳 `YYYYMMDDHHmmss` + 3 位毫秒 = 17 位数字，如 `image-20260528104203839.png`

### Step 6: 替换为绝对路径

```python
import re, json, os

md_file = '<output>.md'
static_dir = os.path.abspath('<图片目录>')

with open('/tmp/wx_url_map.json', 'r') as f:
    url_map = json.load(f)

with open(md_file, 'r') as f:
    content = f.read()

def replace_img(match):
    alt = match.group(1)
    url = match.group(2)
    if url in url_map:
        abs_path = os.path.join(static_dir, url_map[url])
        return f'![{alt}]({abs_path})'
    if not url.startswith('http') and not url.startswith('/'):
        return match.group(0)
    print(f"WARNING: {url[:60]} not downloaded")
    return match.group(0)

content = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', replace_img, content)

with open(md_file, 'w') as f:
    f.write(content)

print('Image paths → absolute paths done.')
```

### Step 7: 验证并报告

```bash
echo "=== Markdown 统计 ==="
echo "文件: <output>.md ($(wc -c < <output>.md) bytes)"
echo "图片引用: $(grep -c '!\[.*\](.*)' <output>.md)"
echo "图片文件: $(ls <图片目录> | wc -l)"
echo ""

# 破损检查
broken=0
while IFS= read -r ref; do
  path=$(echo "$ref" | grep -oP '(?<=\().*(?=\))')
  if [ ! -f "$path" ]; then echo "BROKEN: $ref"; ((broken++)); fi
done < <(grep -oP '!\[.*?\]\(([^)]+)\)' <output>.md)
echo "破损: $broken"

# 清理临时文件
rm -f /tmp/wx_page.html /tmp/wx_clean.html /tmp/wx_url_map.json
```

向用户报告：
- 文章标题
- 输出文件路径和大小
- 图片数量和总大小
- 原文链接（已写在标题下方）
- 任何失败的图片

## 输出示例

```
输出目录/
├── 多平台内容自动上传工具.md     ← Markdown 文件
└── images/
    ├── image-20260528104203839.jpeg
    ├── image-20260528104203848.png
    └── ...

# 文章标题

> 原文链接：https://mp.weixin.qq.com/s/xxxxx

正文内容...

![图片描述](/绝对路径/images/image-20260528104203839.png)
```

## 微信文章特点

了解这些有助于排查问题：

- **图片懒加载**：微信用 `data-src` 存放真实图片链接，`src` 指向空白占位图。预处理脚本会自动交换
- **图片格式**：通过 URL 参数 `wx_fmt=jpeg|png|gif|webp` 指定，下载时优先以此确定扩展名
- **手机版 UA**：必须用移动端 UA 抓取，否则可能返回空白页
- **标题来源**：`<title>` 标签通常为空，标题在 `<meta property="og:title">` 中
- **正文容器**：`<div id="js_content">` 或 `<div class="rich_media_content">`
- **反爬**：微信有基本反爬策略，多篇文章建议间隔 2-3 秒

## 批量采集

用户提供多个单篇文章 URL 时，循环处理每个链接：
- 每个链接间隔 2-3 秒避免触发反爬
- 同一批次的文章共享图片目录（图片去重）
- 每篇文章独立一个 `.md` 文件
- 最后汇总报告：成功/失败数量、总图片数

## 专栏/合集采集

当用户提供的是专栏链接（`mp.appmsgalbum` 或包含 `action=getalbum`），需要先提取专栏内所有文章 URL，再逐篇采集。

### 专栏 Step 1: 抓取专栏页面

```bash
curl -sL --max-time 30 \
  -H 'User-Agent: Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15' \
  -o /tmp/wx_album.html \
  '<专栏URL>'
```

### 专栏 Step 2: 提取文章列表

使用封装的提取脚本：

```bash
python3 <skill-base>/scripts/wx_album_extract.py \
  --input /tmp/wx_album.html \
  --album-url '<专栏URL>' \
  --output /tmp/wx_album_articles.json
```

输出示例：
```
Album: Go后端面试题合集
Articles: 10
  [1] https://mp.weixin.qq.com/s?__biz=...&mid=...&sn=...
  [2] https://mp.weixin.qq.com/s?__biz=...&mid=...&sn=...
  ...
```

### 专栏 Step 3: 读取文章列表并逐篇采集

```python
import json

with open('/tmp/wx_album_articles.json', 'r') as f:
    album = json.load(f)

print(f"专栏: {album['album_name']}")
print(f"文章数: {album['article_count']}")

# 在输出目录下创建以专栏名命名的子目录
album_dir = os.path.join('<输出目录>', slugify(album['album_name'] or 'album'))
os.makedirs(album_dir, exist_ok=True)

for i, article_url in enumerate(album['article_urls']):
    print(f"\n[{i+1}/{album['article_count']}] {article_url}")
    # 对每篇文章执行 Step 2-7（单篇采集流程）
    # ...
    # 每篇文章间隔 2-3 秒
    time.sleep(2)
```

### 专栏 Step 4: 汇总报告

```
专栏采集完成: Go后端面试题合集
─────────────────────────────
成功: 10/10 篇
总图片: 156 张, 45 MB
输出目录: /output/Go后端面试题合集/
```

## 依赖

- `curl` — 抓取页面和图片
- `python3` — 预处理和路径替换
- `fetch-knowledge` skill 的 `html_to_md.py` — HTML 转 Markdown
