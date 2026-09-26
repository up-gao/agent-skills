#!/usr/bin/env python3
"""采集 InfoQ 写作社区（xie.infoq.cn）文章 → 本地 Markdown（含正文与本地图片）。

参考 fetch-wx-knowledge 的实现逻辑（抓页 → 抽正文 → 转 md → 下图 → 相对路径）：
    抓页 → 抽取正文 → 转 Markdown → 下载图片 → 相对路径 → 校验

与微信不同，xie.infoq.cn 的正文由 JS 渲染，纯 curl 只能拿到空壳，
因此本脚本用 Playwright 渲染后取 DOM。

用法:
    python3 fetch_xie_article.py <文章URL> --output-dir <目录>

输出 JSON（末行）:
    {"ok":bool,"title":...,"author":...,"publish_time":...,
     "md_path":...,"images_ok":N,"images_failed":N,"images_failed_urls":[...],
     "source_url":...}

退出码:
    0  成功（允许有缺图，缺图情况在 JSON 的 images_failed 中）
    2  参数错误 / 依赖缺失
    3  抓取或抽取正文失败
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from urllib.parse import urlparse, unquote

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")
REFERER = "https://xie.infoq.cn/"

# 默认输出到本 agent 工作空间的 wk_data/infoq 目录
WORKSPACE_ROOT = os.path.expanduser("~/.openclaw/wks/kg_fetcher")
DEFAULT_OUTPUT_DIR = os.path.join(WORKSPACE_ROOT, "wk_data", "infoq")

# 本技能自带转换器（解耦合设计，不依赖其他技能）
LOCAL_CONVERTER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "html_to_md.py")


def log(msg):
    print(msg, flush=True)


def find_converter():
    """返回本技能自带的 HTML→Markdown 转换器路径。"""
    return LOCAL_CONVERTER if os.path.isfile(LOCAL_CONVERTER) else None


def render_page(url, timeout_ms=60000):
    """用 Playwright 渲染页面，返回 (html, body_outer_html)。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("依赖缺失: 需要 playwright（pip install playwright && playwright install chromium）")
        return None, None

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(user_agent=UA, locale="zh-CN")
            page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            # 正文由 JS 异步渲染；networkidle 后再稳一会儿
            page.wait_for_timeout(4000)
            html = page.content()
            el = page.query_selector("div.ProseMirror")
            body = el.evaluate("e => e.outerHTML") if el else None
            return html, body
        finally:
            browser.close()


def extract_meta(html):
    """从渲染后的 HTML 中抽取标题、作者、发布日期。"""
    title = author = date = None

    m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.S)
    if m:
        title = re.sub(r"<[^>]+>", "", m.group(1)).strip()

    if not title:
        m = re.search(r'<meta property="og:title" content="([^"]*)"', html)
        if m:
            # og:title 形如 "标题_分类_作者_InfoQ写作社区"，去掉后缀
            title = re.sub(r"_InfoQ写作社区$", "", m.group(1)).strip()

    m = re.search(r'class="com-author-name"[^>]*>(.*?)</a>', html, re.S)
    if m:
        author = re.sub(r"<[^>]+>", "", m.group(1)).strip()

    m = re.search(r'class="read-time"[^>]*>(.*?)</ul>', html, re.S)
    if m:
        text = re.sub(r"<[^>]+>", " ", m.group(1))
        dm = re.search(r"(\d{4}-\d{2}-\d{2})", text)
        if dm:
            date = dm.group(1)
    if not date:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", html)
        if m:
            date = m.group(1)

    return title, author, date


def clean_body(body_html):
    """去掉 ProseMirror 里的编辑器残留（空注释占位符）。"""
    body = re.sub(r"<!--.*?-->", "", body_html, flags=re.S)
    return body


def download_images(md_path, images_dir, referer=REFERER):
    """下载 Markdown 中的图片并改写为相对路径。返回 (ok, failed_urls)。"""
    with open(md_path, encoding="utf-8") as f:
        content = f.read()

    urls = list(dict.fromkeys(re.findall(r"!\[[^\]]*\]\(([^)]+)\)", content)))
    os.makedirs(images_dir, exist_ok=True)
    md_dir = os.path.dirname(os.path.abspath(md_path))

    url_map, failed = {}, []
    for i, url in enumerate(urls, 1):
        if not url.startswith("http"):
            continue
        parsed = urlparse(url)
        ext = os.path.splitext(unquote(parsed.path))[1].lower()
        if ext not in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"):
            ext = ".jpeg"
        now = datetime.now()
        ts = now.strftime("%Y%m%d%H%M%S") + f"{now.microsecond // 1000:03d}"
        name = f"image-{ts}{ext}"

        ok = False
        for attempt in range(2):  # 失败重试一轮，成功过的不重复下载
            r = subprocess.run(
                ["curl", "-sL", "--max-time", "20",
                 "-H", f"User-Agent: {UA}",
                 "-H", f"Referer: {referer}",
                 "-o", os.path.join(images_dir, name), url],
                capture_output=True, text=True)
            if r.returncode == 0 and os.path.getsize(os.path.join(images_dir, name)) > 0:
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
    ap = argparse.ArgumentParser(description="采集 xie.infoq.cn 文章为本地 Markdown")
    ap.add_argument("url", help="xie.infoq.cn 文章链接")
    ap.add_argument("--output-dir", "-o", default=DEFAULT_OUTPUT_DIR,
                    help=f"输出目录（默认 {DEFAULT_OUTPUT_DIR}）")
    ap.add_argument("--images-dir", default=None, help="图片目录（默认 <output-dir>/images）")
    args = ap.parse_args()

    url = args.url
    if "xie.infoq.cn/article/" not in url:
        log(f"不是 xie.infoq.cn 文章链接: {url}")
        return 2
    if not find_converter():
        log("依赖缺失: 找不到本技能自带的 scripts/html_to_md.py")
        return 2

    out_dir = os.path.abspath(args.output_dir)
    images_dir = args.images_dir or os.path.join(out_dir, "images")
    os.makedirs(out_dir, exist_ok=True)

    log("[1/5] 渲染页面（Playwright）")
    html, body = render_page(url)
    if not html:
        log("渲染失败")
        return 3
    if not body:
        log("未找到正文容器 div.ProseMirror（页面结构可能已变更或文章不存在）")
        return 3

    log("[2/5] 抽取标题 / 作者 / 日期")
    title, author, date = extract_meta(html)
    title = title or "未命名文章"
    log(f"    标题: {title}")
    log(f"    作者: {author}")
    log(f"    日期: {date}")

    log("[3/5] 转 Markdown")
    body = clean_body(body)
    # html_to_md 依赖 <article> 容器切分正文，这里补上（不闭合，避免截断）
    wrapped = f'<article data-author="{author or ""}" data-publish-time="{date or ""}">{body}</article>'
    tmp_html = os.path.join(out_dir, "_xie_body.html")
    with open(tmp_html, "w", encoding="utf-8") as f:
        f.write(wrapped)

    md_path = os.path.join(out_dir, f"{title}.md")
    r = subprocess.run(
        ["python3", find_converter(),
         "--input", tmp_html,
         "--output", md_path,
         "--title", title,
         "--base-url", "https://xie.infoq.cn",
         "--source-url", url],
        capture_output=True, text=True)
    if r.returncode != 0:
        log(f"转换失败: {r.stderr.strip()[:300]}")
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
