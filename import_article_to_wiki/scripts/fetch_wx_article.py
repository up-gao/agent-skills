#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""微信公众号文章采集（纯脚本，零交互）。

替代 fetch-wx-knowledge 的 Step 2-7 人工流程：该技能把抓页、图片下载、
路径替换写成了需要模型逐段执行/编写的代码片段，每次采集都要多轮模型往返。
本脚本把整条链路固化为一次调用。

用法：
  python3 fetch_wx_article.py <url> --output-dir <目录> [--images <目录>]

输出（stdout）：
  人类可读进度 + 末行 JSON：{"title","md_path","images_ok","images_failed",
  "images_failed_urls","source_url"}
退出码：0 成功；1 失败。
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# 上游技能目录（仅微信预处理脚本 wx_preprocess.py 仍在上游）
SKILLS_DIR = Path(__file__).resolve().parents[2]
WX_SCRIPTS = SKILLS_DIR / "fetch-wx-knowledge" / "scripts"
# 本编排器自带的 HTML→Markdown 转换器（解耦合，不引用其他技能）
LOCAL_HTML_TO_MD = Path(__file__).resolve().parent / "html_to_md.py"
DOWNLOAD_IMAGES = Path(__file__).resolve().parent / "download_images.py"

UA_MOBILE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) "
             "AppleWebKit/605.1.15")
FENCE_RE = re.compile(r"^\s*```")


def log(msg):
    print(msg, flush=True)


def slurp_step(cmd, timeout=60):
    """执行一步，返回 (returncode, combined_output)。"""
    log(f"  $ {' '.join(str(c) for c in cmd)}")
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    out = (p.stdout or "") + (p.stderr or "")
    for line in out.splitlines():
        log(f"    {line}")
    return p.returncode, out


def slugify(name, max_len=100):
    name = re.sub(r'[\\/:*?"<>|\n\r\t]', "_", name).strip()
    name = re.sub(r"\s+", " ", name)
    return (name[:max_len] or "untitled").strip()


def fetch_page(url, dest):
    return subprocess.run(
        ["curl", "-sL", "--max-time", "30", "-H", f"User-Agent: {UA_MOBILE}",
         "-o", str(dest), url],
        capture_output=True, text=True).returncode


def result(title=None, md_path=None, ok=0, failed=None, **extra):
    failed = failed or []
    payload = {"ok": ok == 0, "title": title,
               "md_path": str(md_path) if md_path else None,
               "images_failed": len(failed), "images_failed_urls": failed,
               "source_url": extra.get("source_url")}
    print(json.dumps(payload, ensure_ascii=False))
    return ok


def main():
    ap = argparse.ArgumentParser(description="微信公众号文章采集（单篇）")
    ap.add_argument("url", help="mp.weixin.qq.com 文章链接")
    ap.add_argument("--output-dir", "-o", required=True, help="Markdown 输出目录")
    ap.add_argument("--images", help="图片目录（默认 <output-dir>/images）")
    ap.add_argument("--retries", type=int, default=2, help="图片下载重试轮数")
    args = ap.parse_args()

    if "mp.weixin.qq.com" not in args.url:
        log(f"✗ 不是微信公众号链接：{args.url}")
        return result(ok=1, source_url=args.url)

    outdir = Path(args.output_dir).expanduser().resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    images_dir = Path(args.images).expanduser().resolve() if args.images \
        else outdir / "images"

    raw = outdir / "_wx_page.html"
    clean = outdir / "_wx_clean.html"

    log("[1/4] 抓取页面")
    if fetch_page(args.url, raw) != 0 or not raw.exists() \
            or raw.stat().st_size == 0:
        log("✗ 页面抓取失败（可能被反爬拦截或链接失效）")
        return result(ok=1, source_url=args.url)

    log("[2/4] 预处理")
    rc, _ = slurp_step(["python3", str(WX_SCRIPTS / "wx_preprocess.py"),
                        "--input", str(raw), "--output", str(clean)])
    if rc != 0 or not clean.exists():
        log("✗ 预处理失败")
        return result(ok=1, source_url=args.url)

    title = ""
    title_file = clean.with_suffix(".title")
    if title_file.exists():
        title = title_file.read_text(encoding="utf-8").strip()
    if not title:
        m = re.search(r"<title>(.*?)</title>",
                      clean.read_text(encoding="utf-8", errors="ignore"), re.S)
        title = (m.group(1).strip() if m else "") or "微信文章"

    log("[3/4] 转 Markdown")
    md_path = outdir / f"{slugify(title)}.md"
    rc, _ = slurp_step(["python3", str(LOCAL_HTML_TO_MD),
                        "--input", str(clean), "--output", str(md_path),
                        "--source", "wx",
                        "--title", title, "--source-url", args.url])
    if rc != 0 or not md_path.exists():
        log("✗ HTML 转 Markdown 失败")
        return result(title=title, ok=1, source_url=args.url)

    log("[4/4] 下载图片")
    failed = []
    for attempt in range(args.retries + 1):
        text = md_path.read_text(encoding="utf-8")
        urls = list(dict.fromkeys(
            m.group(2) for m in
            re.finditer(r"!\[([^\]]*)\]\(([^)\s]+)\)", text)
            if m.group(2).startswith(("http://", "https://"))))
        if not urls:
            break
        if attempt:
            log(f"  重试第 {attempt} 轮（剩余 {len(urls)} 张）")
        rc, out = slurp_step([sys.executable, str(DOWNLOAD_IMAGES),
                              str(md_path), "--images", str(images_dir),
                              "--referer", "https://mp.weixin.qq.com/"])
        m = re.search(r"\{\"downloaded\".*\}", out)
        failed = []
        if m:
            try:
                failed = json.loads(m.group(0)).get("failed", [])
            except json.JSONDecodeError:
                pass
        if not failed:
            break

    log("")
    if failed:
        log(f"⚠ 完成：{md_path}（{len(failed)} 张图片未本地化）")
    else:
        log(f"✓ 完成：{md_path}")
    rc = 0 if not failed else 1
    return result(title=title, md_path=md_path, ok=rc, failed=failed,
                  source_url=args.url)


if __name__ == "__main__":
    sys.exit(main())
