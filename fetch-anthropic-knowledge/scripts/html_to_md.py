#!/usr/bin/env python3
"""Anthropic / Claude 博客正文 HTML → Markdown（本技能自带，独立实现）。

设计原则（解耦合）：
    不同站点正文 HTML 结构与特殊标签不同，因此每个技能自带一份转换器，
    跟随来源独立演进，不互相引用。

claude.com 博客正文特点（实测）：
    - 正文容器：<div class="blog_post_content_wrap">
      内层：<div data-readtime="content" class="u-rich-text-blog w-richtext">
    - 正文首部常含 <div class="w-embed"> 内的 <style> 共享样式块，需丢弃
    - 图片：cdn.prod.website-files.com，loading="lazy"
    - 代码块：<pre><code>；标题 h2/h3；无 blockquote 时居多
    - 实体：&#x27; 等需反转义

用法:
    python3 html_to_md.py --input body.html --output out.md \
        --title "标题" --source-url "https://claude.com/blog/xxx" \
        [--author "..."] [--publish-time "..."]

退出码:
    0 成功；2 参数/输入错误
"""
import argparse
import os
import re
import sys
from html import unescape

DEFAULT_BASE_URL = "https://claude.com"

STRIP_TAGS = [
    "span", "font", "strong", "em", "b", "i", "code", "a", "label", "section",
    "div", "p", "article", "figure", "figcaption", "blockquote", "ul", "ol", "li",
    "h1", "h2", "h3", "h4", "h5", "h6", "table", "thead", "tbody", "tr", "td", "th",
    "pre", "mark", "u", "s", "del", "ins", "sub", "sup", "br",
]


def _text(fragment):
    return unescape(re.sub(r"<[^>]+>", "", fragment))


def _abs_url(url, base_url):
    url = unescape(url).strip()
    if not url:
        return url
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return base_url.rstrip("/") + url
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", url):
        return base_url.rstrip("/") + "/" + url.lstrip("/")
    return url


def _inline(fragment, base_url):
    def img_sub(m):
        attrs = m.group(0)
        src_m = re.search(r'\bsrc="([^"]*)"', attrs, re.I) or \
                re.search(r'\bdata-src="([^"]*)"', attrs, re.I)
        alt_m = re.search(r'alt="([^"]*)"', attrs, re.I)
        src = _abs_url(src_m.group(1), base_url) if src_m else ""
        alt = unescape(alt_m.group(1)) if alt_m else ""
        return f"\n\n![{alt}]({src})\n\n" if src else ""

    fragment = re.sub(r"<img[^>]*>", img_sub, fragment, flags=re.I)
    fragment = re.sub(
        r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
        lambda m: f"[{_text(m.group(2))}]({_abs_url(m.group(1), base_url)})",
        fragment, flags=re.I | re.S)
    fragment = re.sub(r"<(strong|b)[^>]*>(.*?)</\1>", r"**\2**", fragment, flags=re.I | re.S)
    fragment = re.sub(r"<(em|i)[^>]*>(.*?)</\1>", r"*\2*", fragment, flags=re.I | re.S)
    fragment = re.sub(r"<code[^>]*>(.*?)</code>", r"`\1`", fragment, flags=re.I | re.S)
    fragment = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    fragment = re.sub(r"</?(?:" + "|".join(STRIP_TAGS) + r")[^>]*>", "", fragment, flags=re.I)
    return unescape(fragment)


def _list_items(block, base_url):
    ordered = block.lstrip().lower().startswith("<ol")
    items = re.findall(r"<li[^>]*>(.*?)</li>", block, re.I | re.S)
    lines = []
    for i, item in enumerate(items, 1):
        text = _inline(item, base_url).strip()
        text = re.sub(r"\s*\n\s*", " ", text)
        if not text or re.fullmatch(r"[-*+]", text):
            continue
        lines.append(f"{i}. {text}" if ordered else f"- {text}")
    return "\n".join(lines)


def _table(block, base_url):
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", block, re.I | re.S)
    parsed = []
    for row in rows:
        cells = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.I | re.S)
        cleaned = []
        for c in cells:
            c = re.sub(r"<br\s*/?>", " ", c, flags=re.I)
            cleaned.append(re.sub(r"\s+", " ", _inline(c, base_url)).strip())
        parsed.append(cleaned)
    if not parsed:
        return ""
    width = max(len(r) for r in parsed)
    out = []
    for idx, r in enumerate(parsed):
        r = r + [""] * (width - len(r))
        out.append("| " + " | ".join(r) + " |")
        if idx == 0:
            out.append("| " + " | ".join(["---"] * width) + " |")
    return "\n".join(out)


def html_to_md(html, title=None, base_url=DEFAULT_BASE_URL, source_url=None,
               author="", publish_time=""):
    """把 Anthropic 博客正文 HTML 转为 Markdown。"""
    body = html

    if "<article" in body.lower():
        body = re.split(r"<article[^>]*>", body, maxsplit=1, flags=re.I)[-1]
        body = re.split(r"</article>", body, maxsplit=1)[0]

    if not title:
        m = re.search(r"<h1[^>]*>(.*?)</h1>", body, re.I | re.S)
        if m:
            title = _text(m.group(1)).strip()
    title = title or "Untitled"

    # 丢弃站点嵌入的 <style> 共享样式块（w-embed）与注释
    body = re.sub(r"<style[^>]*>.*?</style>", "", body, flags=re.I | re.S)
    body = re.sub(r"<script[^>]*>.*?</script>", "", body, flags=re.I | re.S)
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)

    # 块级 → 行内（顺序重要）
    body = re.sub(r"<table[^>]*>.*?</table>",
                  lambda m: "\n\n" + _table(m.group(0), base_url) + "\n\n",
                  body, flags=re.I | re.S)
    body = re.sub(r"<(ul|ol)[^>]*>.*?</\1>",
                  lambda m: "\n\n" + _list_items(m.group(0), base_url) + "\n\n",
                  body, flags=re.I | re.S)
    body = re.sub(r"<h([1-6])[^>]*>(.*?)</h\1>",
                  lambda m: "\n\n" + "#" * int(m.group(1)) + " " +
                            _inline(m.group(2), base_url).strip() + "\n\n",
                  body, flags=re.I | re.S)
    body = re.sub(r"<blockquote[^>]*>(.*?)</blockquote>",
                  lambda m: "\n\n> " + _inline(m.group(1), base_url)
                            .strip().replace("\n", "\n> ") + "\n\n",
                  body, flags=re.I | re.S)

    def code_block(m):
        pre_tag, inner = m.group(1), m.group(2)
        lang_m = re.search(r'class="[^"]*language-([\w+#-]+)', pre_tag + inner, re.I)
        lang = (lang_m.group(1).strip() if lang_m else "") or ""
        code = re.sub(r"</?code[^>]*>", "", inner, flags=re.I)
        code = re.sub(r"<br\s*/?>", "\n", code, flags=re.I)
        code = _text(code).replace("\xa0", " ").replace("\u200b", "")
        code = code.replace("\r\n", "\n").replace("\r", "\n")
        code = "\n".join(line.rstrip() for line in code.split("\n")).strip("\n")
        return f"\n\n```{lang}\n{code}\n```\n\n"

    body = re.sub(r"(<pre[^>]*>)(.*?)</pre>", code_block, body, flags=re.I | re.S)

    body = re.sub(r"</(p|section|div|article|blockquote|figure|figcaption)>", "\n\n", body, flags=re.I)
    body = re.sub(r"<(p|section|div|article|figure|figcaption)[^>]*>", "", body, flags=re.I)
    body = re.sub(r"<(h[1-6])[^>]*>", r"\n\n<\1>", body, flags=re.I)

    body = _inline(body, base_url)
    body = re.sub(r"[ \t]+\n", "\n", body)
    body = re.sub(r"(?m)^[ \t]*[-*+][ \t]*$\n", "", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()

    parts = [f"# {title}"]
    if source_url:
        if not re.match(r"^https?://", source_url):
            raise ValueError(f"--source-url 必须是 http(s) URL，收到: {source_url!r}")
        parts.append(f"> 原文链接：{source_url}")
    meta = []
    if author:
        meta.append(f"作者：{author}")
    if publish_time:
        meta.append(f"发布时间：{publish_time}")
    if meta:
        parts.append("> " + "　|　".join(meta))
    if body:
        parts.append(body)
    return "\n\n".join(parts) + "\n"


def main():
    ap = argparse.ArgumentParser(description="Anthropic 博客正文 HTML → Markdown（本技能自带）")
    ap.add_argument("--input", "-i", required=True)
    ap.add_argument("--output", "-o", required=True)
    ap.add_argument("--title", "-t", default="")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--source-url", default="")
    ap.add_argument("--author", default="")
    ap.add_argument("--publish-time", default="")
    args = ap.parse_args()

    try:
        with open(args.input, encoding="utf-8") as f:
            html = f.read()
    except OSError as e:
        print(f"读取失败: {e}", file=sys.stderr)
        return 2

    md = html_to_md(html, args.title, args.base_url, args.source_url,
                    args.author, args.publish_time)

    outdir = os.path.dirname(os.path.abspath(args.output))
    if outdir:
        os.makedirs(outdir, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(md)

    images = len(re.findall(r"!\[[^\]]*\]\([^)]+\)", md))
    print(f"Markdown: {len(md)} chars, {images} images")
    print(f"Output: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
