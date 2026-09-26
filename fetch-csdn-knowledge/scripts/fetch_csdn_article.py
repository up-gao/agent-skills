#!/usr/bin/env python3
"""采集 CSDN 博客文章 → 本地 Markdown（含正文与本地图片）。

本技能自带全部实现，不依赖其他技能：
    抓页 → 抽取正文 → 转 Markdown → 下载图片 → 相对路径 → 校验

CSDN 正文特点（实测）：
    - 正文容器 <div id="content_views" class="htmledit_views">
    - 标题在 <h1 class="title-article">，也可从 JS 变量 articleTitle 取
    - 作者在 JS 变量 nickName
    - 图片托管在 i-blog.csdnimg.cn，需带 Referer 否则可能被拒
    - 中文全角标点以 HTML 实体出现（&#xff08; 等）

用法:
    python3 fetch_csdn_article.py <文章URL> [--output-dir <目录>]

默认输出到当前 agent 工作空间 wk_data/csdn。

末行输出 JSON:
    {"ok":bool,"title":...,"author":...,"publish_time":...,
     "md_path":...,"images_ok":N,"images_failed":N,
     "images_failed_urls":[...],"source_url":...}

退出码:
    0 成功（允许一般性缺图，缺图在 JSON 中体现）
    2 参数/依赖错误
    3 抓取或正文抽取失败
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from html import unescape
from urllib.parse import urlparse, unquote

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONVERTER = os.path.join(SCRIPT_DIR, "html_to_md.py")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")
REFERER = "https://blog.csdn.net/"

# 默认输出到当前 agent 工作空间的 wk_data/csdn
WORKSPACE_ROOT = os.path.expanduser("~/.openclaw/wks/kg_fetcher")
DEFAULT_OUTPUT_DIR = os.path.join(WORKSPACE_ROOT, "wk_data", "csdn")


def log(msg):
    print(msg, flush=True)


def slugify(name, max_len=120):
    name = re.sub(r'[\\/:*?"<>|\n\r\t]+', "-", name).strip()
    name = re.sub(r"\s+", " ", name)
    return (name[:max_len] or "untitled").strip()


def fetch_page(url, timeout=30):
    """抓取文章页 HTML。"""
    r = subprocess.run(
        ["curl", "-sL", "--max-time", str(timeout),
         "-H", f"User-Agent: {UA}",
         "-H", "Accept-Language: zh-CN,zh;q=0.9",
         "-H", f"Referer: {REFERER}",
         "-o", "/tmp/_csdn_page.html", url],
        capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        with open("/tmp/_csdn_page.html", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except OSError:
        return None


def extract_meta(html):
    """抽取标题 / 作者 / 发布时间。"""
    title = author = date = None

    # CSDN 把元数据放在 <script> 的 JS 变量里，优先取
    m = re.search(r'var articleTitle\s*=\s*"([^"]*)"', html)
    if m:
        title = unescape(m.group(1)).strip()
    if not title:
        m = re.search(r'<h1[^>]*class="[^"]*title-article[^"]*"[^>]*>(.*?)</h1>', html, re.S) \
            or re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
        if m:
            title = _strip(m.group(1))
    if not title:
        m = re.search(r'<meta property="og:title" content="([^"]*)"', html)
        if m:
            title = unescape(m.group(1)).split("_")[0].strip()

    m = re.search(r'var nickName\s*=\s*"([^"]*)"', html)
    if m:
        author = unescape(m.group(1)).strip()

    # 发布时间：文章页显示「最新推荐文章于 YYYY-MM-DD HH:MM:SS 发布」
    m = re.search(r"于\s*&nbsp;?\s*(\d{4}-\d{2}-\d{2})", html) or \
        re.search(r"(\d{4}-\d{2}-\d{2})\s*\d{2}:\d{2}:\d{2}", html)
    if m:
        date = m.group(1)
    return title, author, date


def _strip(fragment):
    return unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def extract_body(html):
    """抽取 <div id="content_views"> 正文 HTML 片段。"""
    i = html.find('id="content_views"')
    if i < 0:
        return None
    start = html.rfind("<div", 0, i)
    if start < 0:
        return None
    depth = 0
    for m in re.finditer(r"<(/?)div\b", html[start:]):
        if m.group(1) == "":
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                return html[start:start + m.end()]
    return None


def download_images(md_path, images_dir, referer=REFERER, retries=2):
    """下载 Markdown 中的远程图片并改写为相对路径。返回 (ok, failed)。"""
    with open(md_path, encoding="utf-8") as f:
        content = f.read()

    urls = list(dict.fromkeys(re.findall(r"!\[[^\]]*\]\(([^)]+)\)", content)))
    urls = [u for u in urls if u.startswith("http")]
    os.makedirs(images_dir, exist_ok=True)
    md_dir = os.path.dirname(os.path.abspath(md_path))

    url_map, failed = {}, []
    for i, url in enumerate(urls, 1):
        parsed = urlparse(url)
        ext = os.path.splitext(unquote(parsed.path))[1].lower()
        if ext not in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"):
            ext = ".jpeg"
        now = datetime.now()
        ts = now.strftime("%Y%m%d%H%M%S") + f"{now.microsecond // 1000:03d}"
        name = f"image-{ts}{ext}"
        target = os.path.join(images_dir, name)

        ok = False
        for attempt in range(retries + 1):
            r = subprocess.run(
                ["curl", "-sL", "--max-time", "20",
                 "-H", f"User-Agent: {UA}",
                 "-H", f"Referer: {referer}",
                 "-o", target, url],
                capture_output=True, text=True)
            if r.returncode == 0 and os.path.exists(target) and os.path.getsize(target) > 0:
                ok = True
                break
            time.sleep(1)

        if ok:
            url_map[url] = name
            log(f"    OK [{i}/{len(urls)}]: {name}")
        else:
            failed.append(url)
            log(f"    FAIL [{i}/{len(urls)}]: {url[:90]}")

    def repl(m):
        alt, url = m.group(1), m.group(2)
        if url in url_map:
            abs_p = os.path.abspath(os.path.join(images_dir, url_map[url]))
            rel = os.path.relpath(abs_p, md_dir).replace(os.sep, "/")
            return f"![{alt}]({rel})"
        return m.group(0)

    content = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", repl, content)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(content)
    return len(url_map), failed


def main():
    ap = argparse.ArgumentParser(description="采集 CSDN 文章为本地 Markdown")
    ap.add_argument("url", help="CSDN 文章链接")
    ap.add_argument("--output-dir", "-o", default=DEFAULT_OUTPUT_DIR,
                    help=f"输出目录（默认 {DEFAULT_OUTPUT_DIR}）")
    ap.add_argument("--images-dir", default=None, help="图片目录（默认 <output-dir>/images）")
    args = ap.parse_args()

    url = args.url
    if "blog.csdn.net" not in url and "csdn.net" not in url:
        log(f"不是 CSDN 链接: {url}")
        return 2
    if not os.path.isfile(CONVERTER):
        log("依赖缺失: 找不到本技能自带的 scripts/html_to_md.py")
        return 2

    out_dir = os.path.abspath(args.output_dir)
    images_dir = args.images_dir or os.path.join(out_dir, "images")
    os.makedirs(out_dir, exist_ok=True)

    log("[1/5] 抓取页面")
    html = fetch_page(url)
    if not html:
        log("抓取失败（网络错误）")
        return 3
    if len(html) < 5000:
        log(f"页面过小（{len(html)} 字节），可能被拦截或链接失效")
        return 3

    log("[2/5] 抽取标题 / 作者 / 日期")
    title, author, date = extract_meta(html)
    title = title or "未命名文章"
    log(f"    标题: {title}")
    log(f"    作者: {author}")
    log(f"    日期: {date}")

    log("[3/5] 抽取正文并转 Markdown")
    body = extract_body(html)
    if not body:
        log("未找到正文容器 div#content_views（页面结构可能已变更或非文章页）")
        return 3
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)

    tmp_html = os.path.join(out_dir, "_csdn_body.html")
    with open(tmp_html, "w", encoding="utf-8") as f:
        f.write(body)

    md_path = os.path.join(out_dir, f"{slugify(title)}.md")
    r = subprocess.run(
        [sys.executable, CONVERTER,
         "--input", tmp_html, "--output", md_path,
         "--title", title, "--base-url", REFERER.rstrip("/"),
         "--source-url", url,
         "--author", author or "", "--publish-time", date or ""],
        capture_output=True, text=True)
    if r.returncode != 0:
        log(f"转换失败: {(r.stderr or r.stdout).strip()[:300]}")
        return 3

    log("[4/5] 下载图片")
    ok_count, failed = download_images(md_path, images_dir)

    log("[5/5] 校验")
    with open(md_path, encoding="utf-8") as f:
        md = f.read()
    refs = len(re.findall(r"!\[[^\]]*\]\([^)]+\)", md))
    remote = len(re.findall(r"!\[[^\]]*\]\(https?://", md))
    log(f"    文件: {md_path}（{len(md)} 字符）")
    log(f"    图片引用: {refs}，本地化: {ok_count}，仍为远程: {remote}")
    if failed:
        log(f"    ⚠ {len(failed)} 张图片未能本地化")

    os.remove(tmp_html)

    print(json.dumps({
        "ok": True,
        "title": title,
        "author": author,
        "publish_time": date,
        "md_path": md_path,
        "images_ok": ok_count,
        "images_failed": len(failed),
        "images_failed_urls": failed,
        "source_url": url,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
