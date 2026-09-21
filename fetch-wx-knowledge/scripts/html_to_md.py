#!/usr/bin/env python3
"""
Convert clean article HTML (from wx_preprocess.py) to Markdown.

Designed for the self-contained HTML fragment that wx_preprocess.py emits:
a single <h1> title followed by the article body inside <article>.

Preserves:
- headings, paragraphs, lists, blockquotes, code blocks
- images as standalone ![alt](url) blocks
- tables rendered as simple pipe tables

Usage:
  python3 html_to_md.py \
    --input /tmp/wx_clean.html \
    --output out/article.md \
    --title "文章标题" \
    --base-url "https://mp.weixin.qq.com" \
    --source-url "https://mp.weixin.qq.com/s/xxxx"
"""

import argparse
import re
import os
from html import unescape


def _text(fragment):
    """Strip tags and unescape entities from an inline fragment."""
    fragment = re.sub(r'<[^>]+>', '', fragment)
    return unescape(fragment)


def _abs_url(url, base_url):
    """Resolve protocol-relative and root-relative URLs against base_url."""
    url = unescape(url).strip()
    if not url:
        return url
    if url.startswith('//'):
        return 'https:' + url
    if url.startswith('/'):
        return base_url.rstrip('/') + url
    if not re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:', url):
        return base_url.rstrip('/') + '/' + url.lstrip('/')
    return url


def _list_items(block, base_url):
    """Render a <ul>/<ol> block to Markdown list lines."""
    ordered = block.lstrip().lower().startswith('<ol')
    items = re.findall(r'<li[^>]*>(.*?)</li>', block, re.I | re.S)
    lines = []
    for i, item in enumerate(items, 1):
        item = _inline(item, base_url).strip()
        item = re.sub(r'\s*\n\s*', ' ', item)
        bullet = f'{i}.' if ordered else '-'
        lines.append(f'{bullet} {item}')
    return '\n'.join(lines)


def _table(block, base_url):
    """Render a simple <table> to a pipe table."""
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', block, re.I | re.S)
    parsed = []
    for row in rows:
        cells = re.findall(r'<t[hd][^>]*>(.*?)</t[hd]>', row, re.I | re.S)
        parsed.append([re.sub(r'\s+', ' ', _inline(c, base_url)).strip() for c in cells])
    if not parsed:
        return ''
    width = max(len(r) for r in parsed)
    out = []
    for idx, r in enumerate(parsed):
        r = r + [''] * (width - len(r))
        out.append('| ' + ' | '.join(r) + ' |')
        if idx == 0:
            out.append('| ' + ' | '.join(['---'] * width) + ' |')
    return '\n'.join(out)


def _inline(fragment, base_url):
    """Convert inline HTML (links, images, emphasis, code) to Markdown."""
    # Images first (before link handling, since img has no closing tag)
    def img_sub(m):
        attrs = m.group(0)
        src_m = re.search(r'src="([^"]*)"', attrs, re.I)
        alt_m = re.search(r'alt="([^"]*)"', attrs, re.I)
        src = _abs_url(src_m.group(1), base_url) if src_m else ''
        alt = unescape(alt_m.group(1)) if alt_m else ''
        return f'\n\n![{alt}]({src})\n\n' if src else ''

    fragment = re.sub(r'<img[^>]*>', img_sub, fragment, flags=re.I)

    # Links
    fragment = re.sub(
        r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
        lambda m: f'[{_text(m.group(2))}]({_abs_url(m.group(1), base_url)})',
        fragment, flags=re.I | re.S)

    # Emphasis / code
    fragment = re.sub(r'<(strong|b)>(.*?)</\1>', r'**\2**', fragment, flags=re.I | re.S)
    fragment = re.sub(r'<(em|i)>(.*?)</\2>', r'*\2*', fragment, flags=re.I | re.S)
    fragment = re.sub(r'<code[^>]*>(.*?)</code>', r'`\1`', fragment, flags=re.I | re.S)

    fragment = re.sub(r'<br\s*/?>', '\n', fragment, flags=re.I)
    fragment = re.sub(r'</?(span|font|section|div|p|tbody|thead|tr|td|th|ul|ol|li)[^>]*>', '', fragment, flags=re.I)
    return unescape(fragment)


def html_to_md(html, title=None, base_url='https://mp.weixin.qq.com', source_url=None):
    """Convert the article HTML produced by wx_preprocess.py to Markdown."""

    # Title: prefer explicit override, else first <h1>
    if not title:
        m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.I | re.S)
        if m:
            title = _text(m.group(1)).strip()
    title = title or ''

    # Body: everything after the leading <h1>, before </article>
    body = html
    if '<article' in body.lower():
        body = re.split(r'<article[^>]*>', body, maxsplit=1, flags=re.I)[-1]
        body = re.split(r'</article>', body, maxsplit=1)[0]
    else:
        body = re.sub(r'^.*?</h1>', '', body, count=1, flags=re.I | re.S) if '<h1' in body.lower() else body
    # Drop a duplicated leading <h1> if present
    body = re.sub(r'^\s*<h1[^>]*>.*?</h1>', '', body, flags=re.I | re.S)

    # Block-level: tables
    body = re.sub(r'<table[^>]*>.*?</table>',
                  lambda m: '\n\n' + _table(m.group(0), base_url) + '\n\n',
                  body, flags=re.I | re.S)
    # Lists
    body = re.sub(r'<(ul|ol)[^>]*>.*?</\1>',
                  lambda m: '\n\n' + _list_items(m.group(0), base_url) + '\n\n',
                  body, flags=re.I | re.S)
    # Headings
    body = re.sub(r'<h([1-6])[^>]*>(.*?)</h\1>',
                  lambda m: '\n\n' + '#' * int(m.group(1)) + ' ' + _inline(m.group(2), base_url).strip() + '\n\n',
                  body, flags=re.I | re.S)
    # Blockquotes
    body = re.sub(r'<blockquote[^>]*>(.*?)</blockquote>',
                  lambda m: '\n\n> ' + _inline(m.group(1), base_url).strip().replace('\n', '\n> ') + '\n\n',
                  body, flags=re.I | re.S)
    # Code blocks
    body = re.sub(r'<pre[^>]*>(.*?)</pre>',
                  lambda m: '\n\n```\n' + _text(m.group(1)).strip('\n') + '\n```\n\n',
                  body, flags=re.I | re.S)
    # Paragraphs
    body = re.sub(r'</p>', '\n\n', body, flags=re.I)
    body = re.sub(r'<p[^>]*>', '', body, flags=re.I)

    # Inline pass
    body = _inline(body, base_url)

    # Normalize whitespace
    body = re.sub(r'[ \t]+\n', '\n', body)
    body = re.sub(r'\n{3,}', '\n\n', body).strip()

    parts = [f'# {title}']
    if source_url:
        parts.append(f'> 原文链接：{source_url}')
    if body:
        parts.append(body)
    return '\n\n'.join(parts) + '\n'


def main():
    parser = argparse.ArgumentParser(description='Convert clean article HTML to Markdown')
    parser.add_argument('--input', '-i', required=True, help='Input clean HTML file')
    parser.add_argument('--output', '-o', required=True, help='Output Markdown file')
    parser.add_argument('--title', '-t', default='', help='Override title')
    parser.add_argument('--base-url', default='https://mp.weixin.qq.com', help='Base URL for relative links')
    parser.add_argument('--source-url', default='', help='Original article URL to cite')
    args = parser.parse_args()

    with open(args.input, 'r', encoding='utf-8') as f:
        html = f.read()

    md = html_to_md(html, args.title, args.base_url, args.source_url)

    outdir = os.path.dirname(os.path.abspath(args.output))
    if outdir:
        os.makedirs(outdir, exist_ok=True)
    with open(args.output, 'w', encoding='utf-8') as f:
        f.write(md)

    images = len(re.findall(r'!\[[^\]]*\]\([^)]+\)', md))
    print(f'Markdown: {len(md)} chars, {images} images')
    print(f'Output: {args.output}')


if __name__ == '__main__':
    main()
