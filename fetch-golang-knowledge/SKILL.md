---
name: fetch-golang-knowledge
description: >
  将网页内容抓取并转换为格式清晰的 Markdown 文件。
  当用户提供 URL 并说出"下载"、"保存"、"获取"、"抓取"、"转换"、"提取"等词时，使用此技能。
  也适用于用户提到保存带图片的页面、归档网页内容、或创建在线文章/知识库的本地副本等场景。
  重要：当用户提到"秀才的进阶之路"（golangstar.cn）时，无论说的是"导入知识库到本地"、
  "下载文章"、"保存到本地"、"备份文章"，还是任何关于从该网站导入/复制文章或知识的表述，
  都必须触发此技能。同样适用于用户想导入到本地的任何其他博客或知识库。
  此技能支持图片下载（自动重命名为时间戳格式）、代码块保留（含语法标记）、
  表格转换，生成带绝对路径图片引用的 Markdown 文件。
---

# Fetch Knowledge

Convert any web page into a clean, local markdown file with all images downloaded.

## Workflow

Follow these steps in order. Stop and report any blockers to the user.

### Step 1: Gather parameters

Ask the user for these three things (or infer defaults when they only provide a URL):

- **URL** (required) — the page to fetch
- **Output directory** — where to save the `.md` file. Default: current working directory.
- **Static resources directory** — where to save images. Default: `images/` inside the output directory.

If the user only gave a URL, use the defaults. Don't ask unnecessary questions — just confirm the defaults and proceed.

### Step 2: Fetch the page

Download the HTML with `curl`, which works on any domain (unlike WebFetch which may reject unverified domains):

```bash
curl -sL -o /tmp/fetch_knowledge_page.html '<URL>'
```

Set a reasonable `--max-time 30` if the site might be slow. If curl fails, try again with `-H "User-Agent: Mozilla/5.0 ..."` to bypass basic blocks.

### Step 3: Analyze the page structure

Read the downloaded HTML to understand what you're working with:

```bash
# Check the title
grep -oP '<title>[^<]+</title>' /tmp/fetch_knowledge_page.html

# List all headings to understand article structure
grep -oP '<h[1-6][^>]*>[^<]+</h[1-6]>' /tmp/fetch_knowledge_page.html

# Find all images
grep -oP '<img[^>]+src="([^"]+)"' /tmp/fetch_knowledge_page.html

# Check for code blocks (pre/code tags)
grep -oP '<(pre|code)[^>]*>' /tmp/fetch_knowledge_page.html | wc -l
```

This analysis tells you:
- The page title (for naming the output file)
- Whether there's a main content container you should target
- How many images need downloading
- Whether code blocks need special handling

### Step 4: Convert to markdown

Use the bundled `scripts/html_to_md.py` script for reliable, consistent conversion:

```bash
python3 <skill-base>/scripts/html_to_md.py \
  --input /tmp/fetch_knowledge_page.html \
  --output <output-dir>/<filename>.md \
  --title "<page-title>" \
  --base-url "<origin-of-the-page>" \
  --source-url "<full-url-of-the-page>"
```

The script handles:
- Extracting the main content area (prefers `<article>`, `<main>`, `.content`, or falls back to `<body>`)
- Converting headings, paragraphs, lists, links, bold/italic
- Preserving code blocks with language markers
- Converting tables to markdown table format
- Keeping image `src` attributes pointing to original URLs (we fix them in step 5)

If the script isn't available or fails, fall back to `pandoc`:

```bash
pandoc /tmp/fetch_knowledge_page.html -f html -t markdown --wrap=none -o <output>.md
```

### Step 5: Download images

Extract all image URLs from the generated markdown, then download each one with a timestamp-based filename. This naming convention (`image-YYYYMMDDHHmmssSSS.ext`) ensures every image has a unique, sortable, and safe filename that won't collide across different pages.

Use this Python script to download images — it handles URL decoding, extracts the correct extension, and assigns timestamp names:

```python
import re
import os
import time
from urllib.parse import unquote, urlparse

md_file = '<output>.md'
static_dir = '<static-dir>'
url_prefix = '<origin-of-the-page>'  # e.g., https://golangstar.cn

os.makedirs(static_dir, exist_ok=True)

with open(md_file, 'r') as f:
    content = f.read()

# Extract all image URLs (both absolute and relative)
image_urls = re.findall(r'!\[.*?\]\(([^)]+)\)', content)
unique_urls = []
seen = set()
for url in image_urls:
    if url not in seen:
        seen.add(url)
        unique_urls.append(url)

# Map original URL → new timestamp filename
url_map = {}

for i, url in enumerate(unique_urls):
    # Resolve relative URLs
    if url.startswith('/'):
        full_url = url_prefix.rstrip('/') + '/' + url.lstrip('/')
    elif not url.startswith('http'):
        full_url = url_prefix.rstrip('/') + '/' + url.lstrip('/')
    else:
        full_url = url

    # Determine extension from URL path (before any query string)
    parsed = urlparse(full_url)
    path = unquote(parsed.path)
    ext = os.path.splitext(path)[1].lower()  # e.g., .png, .jpeg, .gif, .jpg
    if not ext:
        ext = '.png'  # default fallback
    # Normalize .jpg → .jpeg ? No, keep original extension

    # Generate timestamp: YYYYMMDDHHmmss + milliseconds(3 digits)
    # e.g., image-20260528104203839.png
    from datetime import datetime
    now = datetime.now()
    ts = now.strftime('%Y%m%d%H%M%S') + f'{now.microsecond // 1000:03d}'
    new_filename = f"image-{ts}{ext}"

    # Download
    import subprocess
    result = subprocess.run(
        ['curl', '-sL', '--max-time', '15', '-o', os.path.join(static_dir, new_filename), full_url],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        url_map[url] = new_filename
        print(f"OK [{i+1}/{len(unique_urls)}]: {url[:60]}... → {new_filename}")
    else:
        print(f"FAIL [{i+1}/{len(unique_urls)}]: {full_url}")

print(f"\nDownloaded: {len(url_map)}/{len(unique_urls)}")

# Save the URL → filename mapping for step 6
import json
with open('/tmp/image_url_map.json', 'w') as f:
    json.dump(url_map, f)
```

For each image, report: downloaded, skipped, or failed (with the URL so the user can check).

### Step 6: Rewrite image paths with absolute paths

Update the markdown so all image references use **absolute paths** pointing to the local files. This ensures images display correctly regardless of where the markdown file is opened or moved.

```python
import re
import json
import os

md_file = '<output>.md'
static_dir = '<static-dir>'

# Make static_dir an absolute path
static_dir_abs = os.path.abspath(static_dir)

with open('/tmp/image_url_map.json', 'r') as f:
    url_map = json.load(f)

with open(md_file, 'r') as f:
    content = f.read()

def replace_img(match):
    alt = match.group(1)
    url = match.group(2)
    if url in url_map:
        new_filename = url_map[url]
        abs_path = os.path.join(static_dir_abs, new_filename)
        return f'![{alt}]({abs_path})'
    # If already a local path, leave as-is
    if not url.startswith('http') and not url.startswith('/'):
        return match.group(0)
    print(f"WARNING: No downloaded file for: {url}")
    return match.group(0)

content = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', replace_img, content)

with open(md_file, 'w') as f:
    f.write(content)

print('Image paths updated to absolute paths.')
print(f'Example: {list(url_map.values())[0] if url_map else "N/A"}')
```

**Why absolute paths?** Absolute paths guarantee the images can be found when the markdown is viewed from any location — whether opened directly in an editor, rendered in a different tool, or moved to another directory. Relative paths break when the markdown file is moved without also moving the image directory.

### Step 7: Verify and report

Run a quick sanity check:

```bash
# Count images in markdown
echo "Images in markdown: $(grep -c '!\[.*\](.*)' <output>.md)"

# Count downloaded files
echo "Files in static dir: $(ls <static-dir> | wc -l)"

# Check for broken references
grep -oP '!\[.*?\]\(([^)]+)\)' <output>.md | while read ref; do
  path=$(echo "$ref" | grep -oP '(?<=\().*(?=\))')
  if [ ! -f "$path" ]; then
    echo "BROKEN: $ref"
  fi
done
```

Report a summary to the user:
- Output markdown file path and size
- Number of images downloaded
- Any failures or skipped images
- The title and approximate reading length

## Tips for quality output

- **Naming the file**: Use the page title, slugified (lowercase, hyphens, no special chars). E.g., "Mysql面试题" → `mysql-interview.md`.
- **Code blocks**: Make sure `scripts/html_to_md.py` preserves indentation and adds language hints (e.g., ```sql) when the original HTML has `class="language-sql"`.
- **Tables**: Complex nested tables may not convert perfectly. If the page has unusually complex tables, note that in the report.
- **Large images**: If images are very large (>5MB), skip them and note the URL so the user can download manually.
- **Cleanup**: Remove `/tmp/fetch_knowledge_page.html` at the end unless the user asks to keep it.

## Edge cases

- **SPA / JavaScript-rendered pages**: curl won't execute JS. If the page looks empty or is just a skeleton, tell the user and suggest using a browser-based approach instead.
- **Authentication-required pages**: Tell the user you can't fetch them and explain why.
- **404 / redirects**: curl `-L` follows redirects, but if the final status is an error, report it clearly.
- **Relative image URLs**: The script resolves relative URLs against `--base-url`, so always pass the page's origin.
