#!/usr/bin/env python3
"""
Pre-process WeChat public account article HTML.

Handles WeChat-specific quirks:
- Extracts title from og:title meta tag
- Extracts content from rich_media_content / js_content div
- Converts data-src → src on img tags (WeChat lazy loading)
- Removes WeChat JS/CSS bloat, keeping only the article body
- Outputs a clean, self-contained HTML fragment ready for markdown conversion
"""

import re
import sys
import argparse
from html import unescape


def preprocess(html, title_override=None):
    """Pre-process WeChat article HTML.

    Returns (title, clean_html_fragment, meta) where meta holds:
      author        — account nickname (var nickname), fallback to meta author
      publish_time  — 'YYYY-MM-DD HH:MM' from var createTime
    """

    # Step 1: Extract title
    title = title_override or ''
    if not title:
        # Try og:title meta
        m = re.search(r'<meta\s[^>]*property="og:title"[^>]*content="([^"]*)"', html, re.I)
        if m:
            title = unescape(m.group(1)).strip()
        # Fallback: any title tag
        if not title:
            m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
            if m and m.group(1).strip():
                title = m.group(1).strip()

    # Step 1b: Extract author and publish time (surfaced in the markdown header)
    meta = {'author': '', 'publish_time': ''}
    m = re.search(r'var\s+nickname\s*=\s*htmlDecode\("([^"]*)"\)', html)
    if not m:
        m = re.search(r'<meta\s[^>]*name="author"[^>]*content="([^"]*)"', html, re.I)
    if m:
        meta['author'] = unescape(m.group(1)).strip()
    m = re.search(r"var\s+createTime\s*=\s*'([^']+)'", html)
    if m:
        meta['publish_time'] = m.group(1).strip()

    # Step 2: Extract main content
    # WeChat articles store content in these containers:
    content_selectors = [
        r'<div[^>]*id="js_content"[^>]*>(.*?)</div>\s*<div[^>]+rich_media_area_extra',
        r'<div[^>]*class="[^"]*rich_media_content[^"]*"[^>]*>(.*?)</div>\s*<div[^>]+rich_media_area_extra',
        r'<div[^>]*id="js_content"[^>]*>(.*?)</div>',
        r'<div[^>]*class="[^"]*rich_media_content[^"]*"[^>]*>(.*?)</div>',
    ]

    content_html = ''
    for pattern in content_selectors:
        m = re.search(pattern, html, re.I | re.DOTALL)
        if m:
            content_html = m.group(1)
            break

    if not content_html:
        # Fallback: use body
        m = re.search(r'<body[^>]*>(.*?)</body>', html, re.I | re.DOTALL)
        content_html = m.group(1) if m else html

    # Step 3: Convert data-src → src on img tags
    # WeChat uses data-src for lazy loading; the real image URL is in data-src.
    # The regex src="([^"]*)" can falsely match inside data-src="...", so we
    # explicitly handle data-src first, then ensure a proper standalone src exists.
    def fix_img_src(match):
        tag = match.group(0)

        # Extract data-src (WeChat's real image URL) — match the full attribute
        data_src_m = re.search(r'\bdata-src="([^"]*)"', tag, re.I)
        # Extract standalone src (NOT data-src). Use a negative lookbehind for data-
        standalone_src_m = re.search(r'(?<!data-)src="([^"]*)"', tag, re.I)

        data_src_url = data_src_m.group(1) if data_src_m else ''
        src_url = standalone_src_m.group(1) if standalone_src_m else ''
        src_full_match = standalone_src_m.group(0) if standalone_src_m else ''  # e.g. src="//blank.gif"

        # Determine the real image URL
        real_url = ''
        if data_src_url and data_src_url.startswith('http'):
            # Skip JS file URLs (res.wx.qq.com without mmbiz)
            if 'mmbiz' not in data_src_url and 'res.wx.qq.com' in data_src_url:
                real_url = src_url  # keep existing src
            else:
                real_url = data_src_url  # use data-src
        elif src_url and src_url.startswith('http') and 'mmbiz' in src_url:
            real_url = src_url
        elif src_url:
            real_url = src_url

        # Fix protocol-relative URLs
        if real_url.startswith('//'):
            real_url = 'https:' + real_url

        if not real_url:
            return tag  # No usable image URL found

        # Rebuild img tag: remove data-src and set correct src
        # Remove data-src attribute
        tag = re.sub(r'\s*data-src="[^"]*"', '', tag, flags=re.I)
        # Remove old src if it was inside data-src (false match)
        tag = re.sub(r'\s*data-src="[^"]*"', '', tag, flags=re.I)  # double-ensure

        if standalone_src_m:
            # Replace existing src with real URL
            tag = tag.replace(src_full_match, f'src="{real_url}"')
        else:
            # Add src attribute before the closing >
            tag = tag.replace('/>', f' src="{real_url}" />').replace(' >', f' src="{real_url}">')
            if 'src=' not in tag:
                tag = tag.replace('<img', f'<img src="{real_url}"')

        return tag

    content_html = re.sub(r'<img[^>]+>', fix_img_src, content_html, flags=re.I)

    # Step 4: Remove inline scripts and styles
    content_html = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', content_html, flags=re.I | re.DOTALL)

    # Step 4b: Strip WeChat trailing junk so it never reaches the markdown
    #   - hidden helper tags such as <mp-style-type data-value="3"></mp-style-type>
    #   - hidden UI text such as 预览时标签不可点
    #   - the trailing rich_media_area_extra / tool / tag blocks at the end of the body
    content_html = re.sub(r'<mp-[^>]*>.*?</mp-[^>]*>', '', content_html, flags=re.I | re.DOTALL)
    content_html = re.sub(r'<mp-[^>]*/?>', '', content_html, flags=re.I)
    content_html = re.sub(r'<div[^>]*id="js_tags_preview_toast"[^>]*>.*?</div>', '', content_html, flags=re.I | re.DOTALL)
    content_html = re.sub(r'<div[^>]*rich_media_area_extra[^>]*>.*$', '', content_html, flags=re.I | re.DOTALL)
    content_html = re.sub(r'<div[^>]*id="(content_bottom_area|font_pannel_area|js_temp_bottom_area)"[^>]*>.*$', '', content_html, flags=re.I | re.DOTALL)

    # Step 4c: Rebuild code blocks line by line. WeChat splits code into a <code>
    # element per line and uses &nbsp;/spans for indentation; joined naively the
    # layout collapses. Emit one line per <code> with indentation preserved.
    def fix_code_block(m):
        pre_tag = m.group(1)
        inner = m.group(2)
        lang_m = re.search(r'data-lang="([^"]*)"', pre_tag, re.I)
        lang = lang_m.group(1).strip() if lang_m else ''
        lines = []
        for line_html in re.findall(r'<code[^>]*>(.*?)</code>', inner, re.I | re.S):
            line = re.sub(r'<br\s*/?>', '\n', line_html, flags=re.I)
            line = re.sub(r'<[^>]+>', '', line)
            line = unescape(line).replace('\xa0', ' ').replace('\u200b', '')
            line = line.rstrip()
            lines.append(line)
        # Drop leading/trailing empty lines but keep internal blank lines
        while lines and not lines[0]:
            lines.pop(0)
        while lines and not lines[-1]:
            lines.pop()
        code = '\n'.join(lines)
        return f'<pre data-lang="{lang}"><code>{code}</code></pre>'

    content_html = re.sub(
        r'(<pre[^>]*>)(.*?)</pre>',
        lambda m: fix_code_block(m),
        content_html, flags=re.I | re.S)

    # Step 5: Build clean HTML document
    clean_html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>{title}</title></head>
<body>
<article data-author="{meta['author']}" data-publish-time="{meta['publish_time']}">
<h1>{title}</h1>
{content_html}
</article>
</body>
</html>"""

    return title, clean_html, meta


def main():
    parser = argparse.ArgumentParser(description='Pre-process WeChat article HTML')
    parser.add_argument('--input', '-i', required=True, help='Input HTML file (raw WeChat page)')
    parser.add_argument('--output', '-o', required=True, help='Output clean HTML file')
    parser.add_argument('--title', '-t', default='', help='Override title')
    args = parser.parse_args()

    with open(args.input, 'r', encoding='utf-8') as f:
        html = f.read()

    title, clean_html, meta = preprocess(html, args.title)

    with open(args.output, 'w', encoding='utf-8') as f:
        f.write(clean_html)

    print(f'Title: {title}')
    if meta.get('author'):
        print(f'Author: {meta["author"]}')
    if meta.get('publish_time'):
        print(f'Publish time: {meta["publish_time"]}')
    print(f'Clean HTML: {len(clean_html)} chars')
    print(f'Output: {args.output}')


if __name__ == '__main__':
    main()
