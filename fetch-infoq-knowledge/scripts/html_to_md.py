#!/usr/bin/env python3
"""InfoQ 文章 HTML 片段 → Markdown（本技能自带，不依赖其他技能）。

为什么需要它：
    InfoQ 主站正文在 <div class="ProseMirror"> 内、写作社区同理，两者都是
    HTML 片段。本技能需要稳定的 HTML→Markdown 转换能力，因此自带一份，
    避免跨技能依赖（某个技能改动或缺失时不会连带影响本技能）。

支持：标题 / 段落 / 列表 / 引用 / 代码块 / 图片 / 表格 / 链接 / 强调。

用法:
    python3 html_to_md.py --input body.html --output out.md \
        --title "文章标题" --source-url "https://xie.infoq.cn/article/xxx" \
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

DEFAULT_BASE_URL = "https://www.infoq.cn"


def _text(fragment):
    """去标签 + 反转义。"""
    return unescape(re.sub(r"<[^>]+>", "", fragment))


def _abs_url(url, base_url):
    """补全协议相对 / 根相对 URL。"""
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
    """行内元素：图片、链接、强调、行内代码。"""
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
    fragment = re.sub(
        r"</?(span|font|strong|em|b|i|code|a|label|section|div|p|article|figure|figcaption"
        r"|blockquote|ul|ol|li|h[1-6]|table|thead|tbody|tr|td|th|pre|mark|u|s|del|ins|sub|sup)[^>]*>",
        "", fragment, flags=re.I)
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
        bullet = f"{i}." if ordered else "-"
        lines.append(f"{bullet} {text}")
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
    """把 InfoQ 正文 HTML 片段（ProseMirror 内容）转为 Markdown。"""
    body = html

    # 若传入了完整 <article>，只取其中内容；否则直接用
    if "<article" in body.lower():
        body = re.split(r"<article[^>]*>", body, maxsplit=1, flags=re.I)[-1]
        body = re.split(r"</article>", body, maxsplit=1)[0]

    # 标题：显式传入优先，否则取第一个 <h1>
    if not title:
        m = re.search(r"<h1[^>]*>(.*?)</h1>", body, re.I | re.S)
        if m:
            title = _text(m.group(1)).strip()
    title = title or "未命名文章"

    # 去掉编辑器残留的空注释占位符（InfoQ ProseMirror 会产生 <!---->）
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)

    # 表格 → 列表 → 标题 → 引用 → 代码块 的顺序很重要（先块级后行内）
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
                  lambda m: "\n\n> " + _inline(m.group(1), base_url).strip().replace("\n", "\n> ") + "\n\n",
                  body, flags=re.I | re.S)

    def code_block(m):
        inner = m.group(2)
        lang_m = re.search(r'lang="([^"]*)"', m.group(1), re.I)
        lang = (lang_m.group(1).strip() if lang_m else "") or ""
        code = re.sub(r"</?code[^>]*>", "", inner, flags=re.I)
        code = re.sub(r"<br\s*/?>", "\n", code, flags=re.I)
        code = _text(code).replace("\xa0", " ").replace("\u200b", "")
        code = code.replace("\r\n", "\n").replace("\r", "\n")
        code = "\n".join(line.rstrip() for line in code.split("\n")).strip("\n")
        return f"\n\n```{lang}\n{code}\n```\n\n"

    body = re.sub(r"(<pre[^>]*>)(.*?)</pre>", code_block, body, flags=re.I | re.S)

    # 段落：块级闭合标签统一转空行
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
    ap = argparse.ArgumentParser(description="InfoQ 正文 HTML → Markdown（本技能自带）")
    ap.add_argument("--input", "-i", required=True, help="正文 HTML 文件")
    ap.add_argument("--output", "-o", required=True, help="输出 Markdown")
    ap.add_argument("--title", "-t", default="", help="文章标题（默认取第一个 h1）")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL, help="补全相对链接的基址")
    ap.add_argument("--source-url", default="", help="原文链接（http/https）")
    ap.add_argument("--author", default="", help="作者")
    ap.add_argument("--publish-time", default="", help="发布时间")
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
