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

--source-url must always be the public article URL returned by the browser
(mini-program / mp.weixin.qq.com link), never a local path such as
/tmp/wx_clean.html or an absolute filesystem path.
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
        # WeChat adds many empty <li></li> placeholders (e.g. before code blocks);
        # drop them so they do not turn into stray "-" lines.
        if not item or re.fullmatch(r'[-*+]', item):
            continue
        bullet = f'{i}.' if ordered else '-'
        lines.append(f'{bullet} {item}')
    return '\n'.join(lines)


def _table(block, base_url):
    """Render a simple <table> to a pipe table."""
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', block, re.I | re.S)
    parsed = []
    for row in rows:
        cells = re.findall(r'<t[hd][^>]*>(.*?)</t[hd]>', row, re.I | re.S)
        cleaned = []
        for c in cells:
            # WeChat wraps cell text in <section>/<p>/<div>; flatten to one line
            # so the pipe table does not get split across lines.
            c = re.sub(r'<br\s*/?>', ' ', c, flags=re.I)
            text = _inline(c, base_url)
            text = re.sub(r'\s+', ' ', text).strip()
            cleaned.append(text)
        parsed.append(cleaned)
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
    # Strip remaining wrappers. Block wrappers (section/div/p) are converted to
    # paragraph breaks in the block pass first; if any survive into a table cell
    # or inline context they must be removed here, not left as literal text.
    fragment = re.sub(r'</?(span|font|strong|em|b|i|code|a|label|leaf|section|div|p|article|figure|figcaption)[^>]*>', '', fragment, flags=re.I)
    return unescape(fragment)


def _cn_date(value):
    """Format 'YYYY-MM-DD HH:MM' as 'YYYY年M月D日'."""
    m = re.match(r'(\d{4})-(\d{1,2})-(\d{1,2})', value or '')
    if m:
        return f'{m.group(1)}年{int(m.group(2))}月{int(m.group(3))}日'
    return value or ''


def _meta_from_html(html):
    """Read author / publish time embedded by wx_preprocess.py on <article>."""
    author = publish = ''
    m = re.search(r'<article[^>]*data-author="([^"]*)"', html, re.I)
    if m:
        author = unescape(m.group(1)).strip()
    m = re.search(r'<article[^>]*data-publish-time="([^"]*)"', html, re.I)
    if m:
        publish = unescape(m.group(1)).strip()
    return author, publish


def html_to_md(html, title=None, base_url='https://mp.weixin.qq.com', source_url=None,
               author='', publish_time=''):
    """Convert the article HTML produced by wx_preprocess.py to Markdown."""

    # Author / publish time: explicit args win, else read from <article> attrs
    if not author or not publish_time:
        a, pt = _meta_from_html(html)
        author = author or a
        publish_time = publish_time or pt

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

    # Block-level: tables. Must run before the paragraph pass below, otherwise
    # the <section> inside each cell turns into newlines and splits the table.
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
    # Code blocks — keep every line, preserding indentation, of the rebuilt <pre>
    def code_block(m):
        pre_tag = m.group(1)
        inner = m.group(2)
        lang_m = re.search(r'data-lang="([^"]*)"', pre_tag, re.I)
        lang = (lang_m.group(1).strip() if lang_m else '') or ''
        code = inner
        code = re.sub(r'</?code[^>]*>', '', code, flags=re.I)
        code = re.sub(r'<br\s*/?>', '\n', code, flags=re.I)
        code = _text(code)
        code = code.replace('\xa0', ' ').replace('\u200b', '')
        code = code.replace('\r\n', '\n').replace('\r', '\n')
        code = '\n'.join(line.rstrip() for line in code.split('\n')).strip('\n')
        return f'\n\n```{lang}\n{code}\n```\n\n'

    body = re.sub(r'(<pre[^>]*>)(.*?)</pre>', code_block, body, flags=re.I | re.S)
    # Paragraphs — WeChat wraps each paragraph in <section> (or <p>/<div>);
    # convert every closing block tag into a blank line so paragraphs stay split.
    body = re.sub(r'</(p|section|div|article|blockquote|figure|figcaption)>', '\n\n', body, flags=re.I)
    body = re.sub(r'<(p|section|div|article|figure|figcaption)[^>]*>', '', body, flags=re.I)
    # A block element directly followed by another starts a new paragraph too.
    body = re.sub(r'<(h[1-6])[^>]*>', r'\n\n<\1>', body, flags=re.I)

    # Inline pass
    body = _inline(body, base_url)

    # Normalize whitespace
    body = re.sub(r'[ \t]+\n', '\n', body)
    # Drop stray list-marker-only lines produced by empty <li> elements.
    body = re.sub(r'(?m)^[ \t]*[-*+][ \t]*$\n', '', body)
    body = re.sub(r'\n{3,}', '\n\n', body).strip()

    parts = [f'# {title}']
    if source_url:
        # Source line must be a browser-accessible URL, never a local file path.
        if not re.match(r'^https?://', source_url):
            raise ValueError(
                f'--source-url must be an http(s) URL a browser can open, got: {source_url!r}'
            )
        parts.append(f'> 原文链接：{source_url}')
    # Author and publish time, e.g. "作者:飞叔杂谈  时间 2026年8月9日"
    author_line = []
    if author:
        author_line.append(f'作者:{author}')
    if publish_time:
        author_line.append(f'时间 {_cn_date(publish_time)}')
    if author_line:
        parts.append('> ' + '  '.join(author_line))
    if body:
        parts.append(body)
    return '\n\n'.join(parts) + '\n'


def main():
    parser = argparse.ArgumentParser(description='Convert clean article HTML to Markdown')
    parser.add_argument('--input', '-i', required=True, help='Input clean HTML file')
    parser.add_argument('--output', '-o', required=True, help='Output Markdown file')
    parser.add_argument('--title', '-t', default='', help='Override title')
    parser.add_argument('--base-url', default='https://mp.weixin.qq.com', help='Base URL for relative links')
    parser.add_argument('--source-url', default='',
                        help='Original article URL (http/https, browser-accessible) to cite')
    parser.add_argument('--author', default='', help='Author / account name (default: read from HTML)')
    parser.add_argument('--publish-time', default='', help='Publish time (default: read from HTML)')
    args = parser.parse_args()

    with open(args.input, 'r', encoding='utf-8') as f:
        html = f.read()

    md = html_to_md(html, args.title, args.base_url, args.source_url, args.author, args.publish_time)

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
