#!/usr/bin/env python3
"""本编排器（import_article_to_wiki）自带的 HTML→Markdown 转换器。

设计原则：**解耦合**
    不同来源/站点的正文 HTML 结构不同，会有各自特殊的标签与残留。
    因此每个技能自带一份 `html_to_md.py`，跟随来源独立演进，
    不互相引用：改微信的规则不会影响 InfoQ，新增渠道也只在本文件内加分支。

本文件按来源分派规则：
    --source wx     : 微信公众号正文（wx_preprocess.py 产出的片段）
    --source infoq  : InfoQ 正文（<div class="ProseMirror"> 片段）

用法:
    python3 html_to_md.py --input body.html --output out.md \
        --source infoq --title "标题" --source-url "https://..." \
        [--author "作者"] [--publish-time "2026-01-01"]

退出码:
    0  成功
    2  参数/输入错误
"""
import argparse
import os
import re
import sys
from html import unescape

# 各来源的默认 base-url（补全相对链接用）
BASE_URLS = {
    "wx": "https://mp.weixin.qq.com",
    "infoq": "https://www.infoq.cn",
}


# ---------------------------------------------------------------------------
# 公共骨架（两个来源共用的处理顺序）；来源专属差异通过参数注入
# ---------------------------------------------------------------------------

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


def _inline(fragment, base_url, strip_tags):
    def img_sub(m):
        attrs = m.group(0)
        src_m = re.search(r'src="([^"]*)"', attrs, re.I)
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
    if strip_tags:
        fragment = re.sub(
            r"</?(?:" + "|".join(strip_tags) + r")[^>]*>", "", fragment, flags=re.I)
    return unescape(fragment)


def _list_items(block, base_url, strip_tags):
    ordered = block.lstrip().lower().startswith("<ol")
    items = re.findall(r"<li[^>]*>(.*?)</li>", block, re.I | re.S)
    lines = []
    for i, item in enumerate(items, 1):
        text = _inline(item, base_url, strip_tags).strip()
        text = re.sub(r"\s*\n\s*", " ", text)
        if not text or re.fullmatch(r"[-*+]", text):
            continue
        lines.append(f"{i if ordered else '-'}. {text}" if ordered else f"- {text}")
    return "\n".join(lines)


def _table(block, base_url, strip_tags):
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", block, re.I | re.S)
    parsed = []
    for row in rows:
        cells = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.I | re.S)
        cleaned = []
        for c in cells:
            c = re.sub(r"<br\s*/?>", " ", c, flags=re.I)
            cleaned.append(re.sub(r"\s+", " ", _inline(c, base_url, strip_tags)).strip())
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


def _convert(body, base_url, strip_tags, remove_comments, code_lang_attr):
    """共用转换流程。"""
    if remove_comments:
        body = re.sub(r"<!--.*?-->", "", body, flags=re.S)

    body = re.sub(r"<table[^>]*>.*?</table>",
                  lambda m: "\n\n" + _table(m.group(0), base_url, strip_tags) + "\n\n",
                  body, flags=re.I | re.S)
    body = re.sub(r"<(ul|ol)[^>]*>.*?</\1>",
                  lambda m: "\n\n" + _list_items(m.group(0), base_url, strip_tags) + "\n\n",
                  body, flags=re.I | re.S)
    body = re.sub(r"<h([1-6])[^>]*>(.*?)</h\1>",
                  lambda m: "\n\n" + "#" * int(m.group(1)) + " " +
                            _inline(m.group(2), base_url, strip_tags).strip() + "\n\n",
                  body, flags=re.I | re.S)
    body = re.sub(r"<blockquote[^>]*>(.*?)</blockquote>",
                  lambda m: "\n\n> " + _inline(m.group(1), base_url, strip_tags)
                            .strip().replace("\n", "\n> ") + "\n\n",
                  body, flags=re.I | re.S)

    def code_block(m):
        inner = m.group(2)
        lang_m = re.search(rf'{code_lang_attr}="([^"]*)"', m.group(1), re.I)
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

    body = _inline(body, base_url, strip_tags)
    body = re.sub(r"[ \t]+\n", "\n", body)
    body = re.sub(r"(?m)^[ \t]*[-*+][ \t]*$\n", "", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body


# ---------------------------------------------------------------------------
# 来源专属入口
# ---------------------------------------------------------------------------

def convert_wx(html, title=None, base_url=None, source_url=None,
               author="", publish_time=""):
    """微信公众号正文。"""
    base_url = base_url or BASE_URLS["wx"]
    body = html
    if "<article" in body.lower():
        body = re.split(r"<article[^>]*>", body, maxsplit=1, flags=re.I)[-1]
        body = re.split(r"</article>", body, maxsplit=1)[0]
    if not title:
        m = re.search(r"<h1[^>]*>(.*?)</h1>", body, re.I | re.S)
        if m:
            title = _text(m.group(1)).strip()
    body = _convert(
        body, base_url,
        strip_tags=["span", "font", "strong", "em", "b", "i", "code", "a", "label",
                    "leaf", "section", "div", "p", "article", "figure", "figcaption",
                    "blockquote", "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6",
                    "table", "thead", "tbody", "tr", "td", "th", "pre", "mark", "u",
                    "s", "del", "ins", "sub", "sup"],
        remove_comments=False,
        code_lang_attr="data-lang")
    # 微信标题常为空，回退占位
    title = title or "微信文章"
    return _assemble(title, source_url, author, publish_time, body)


def convert_infoq(html, title=None, base_url=None, source_url=None,
                  author="", publish_time=""):
    """InfoQ 正文（主站与写作社区均为 ProseMirror 片段）。"""
    base_url = base_url or BASE_URLS["infoq"]
    body = html
    if "<article" in body.lower():
        body = re.split(r"<article[^>]*>", body, maxsplit=1, flags=re.I)[-1]
        body = re.split(r"</article>", body, maxsplit=1)[0]
    if not title:
        m = re.search(r"<h1[^>]*>(.*?)</h1>", body, re.I | re.S)
        if m:
            title = _text(m.group(1)).strip()
    body = _convert(
        body, base_url,
        strip_tags=["span", "font", "strong", "em", "b", "i", "code", "a", "label",
                    "leaf", "section", "div", "p", "article", "figure", "figcaption",
                    "blockquote", "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6",
                    "table", "thead", "tbody", "tr", "td", "th", "pre", "mark", "u",
                    "s", "del", "ins", "sub", "sup"],
        remove_comments=True,       # InfoQ ProseMirror 会产生 <!----> 残留
        code_lang_attr="lang")      # InfoQ 代码块语言在 <pre lang="go">
    title = title or "未命名文章"
    return _assemble(title, source_url, author, publish_time, body)


def _assemble(title, source_url, author, publish_time, body):
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


CONVERTERS = {"wx": convert_wx, "infoq": convert_infoq}


def main():
    ap = argparse.ArgumentParser(description="HTML→Markdown（本编排器自带，按来源分派）")
    ap.add_argument("--input", "-i", required=True)
    ap.add_argument("--output", "-o", required=True)
    ap.add_argument("--source", "-s", required=True, choices=sorted(CONVERTERS),
                    help="来源：wx / infoq")
    ap.add_argument("--title", "-t", default="")
    ap.add_argument("--base-url", default="", help="默认按来源取")
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

    fn = CONVERTERS[args.source]
    md = fn(html, args.title, args.base_url or None, args.source_url,
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
