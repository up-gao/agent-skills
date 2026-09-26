#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载 Markdown 中的图片到本地，并把引用替换为相对路径。

补充 fetch-wx-knowledge 中「Step 5/6」的图片本地化能力（该技能只给了代码片段，
未提供可调用脚本）。微信图片托管在 mmbiz.qpic.cn，扩展名由 URL 参数 `wx_fmt` 决定。

用法：
  python3 download_images.py <md_file> [--images <dir>] [--referer <url>]

默认图片目录为 <md 所在目录>/images，路径替换为相对 md 的相对路径。
输出 JSON 到 stdout：{"downloaded": n, "failed": [...], "map": {...}}
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
UA_WX = "Mozilla/5.0 (compatible; WeChat/1.0)"
UA_GENERIC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
VALID_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp",
             ".awebp", ".avif"}
FMT_MAP = {"jpeg": ".jpeg", "jpg": ".jpg", "png": ".png", "gif": ".gif",
           "webp": ".webp"}


def guess_ext(url):
    """优先用 URL 参数判断格式（微信 wx_fmt），否则用路径后缀。"""
    m = re.search(r"wx_fmt=(\w+)", url)
    if m and m.group(1).lower() in FMT_MAP:
        return FMT_MAP[m.group(1).lower()]
    if "mmbiz.qpic.cn" in url:
        m = re.search(r"/(?:sz_)?mmbiz_(png|jpeg|jpg|gif|webp)/", url)
        if m:
            return FMT_MAP.get(m.group(1).lower(), ".png")
    path = unquote(urlparse(url).path)
    ext = os.path.splitext(path)[1].lower()
    if ext in VALID_EXT:
        return ext
    return ".png"


def ts_name(ext, used):
    """时间戳命名 image-YYYYMMDDHHmmssSSS.ext，冲突时顺延毫秒。"""
    while True:
        now = datetime.now()
        ts = now.strftime("%Y%m%d%H%M%S") + f"{now.microsecond // 1000:03d}"
        name = f"image-{ts}{ext}"
        if name not in used:
            used.add(name)
            return name


def download(url, dest, referer):
    cmd = ["curl", "-sL", "--max-time", "20",
           "-H", f"User-Agent: {UA_WX if 'qpic.cn' in url else UA_GENERIC}"]
    if referer:
        cmd += ["-e", referer]
    cmd += ["-o", str(dest), url]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0 or not dest.exists():
        return False
    if dest.stat().st_size == 0:
        dest.unlink(missing_ok=True)
        return False
    return True


def main():
    ap = argparse.ArgumentParser(description="下载 Markdown 图片并转相对路径")
    ap.add_argument("md_file")
    ap.add_argument("--images", help="图片目录（默认 <md目录>/images）")
    ap.add_argument("--referer", default="https://mp.weixin.qq.com/",
                    help="Referer（微信图片需要）")
    args = ap.parse_args()

    md_path = Path(args.md_file).expanduser().resolve()
    if not md_path.exists():
        print(f"✗ 文件不存在：{md_path}", file=sys.stderr)
        return 1

    md_dir = md_path.parent
    images_dir = Path(args.images).expanduser().resolve() if args.images \
        else md_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    text = md_path.read_text(encoding="utf-8")

    # 已有本地文件 -> 复用（避免重复下载）
    existing = {}
    for p in images_dir.rglob("*"):
        if p.is_file():
            existing.setdefault(p.name, p)

    urls = list(dict.fromkeys(
        m.group(2) for m in IMG_RE.finditer(text)
        if m.group(2).startswith(("http://", "https://"))
    ))

    url_map, failed = {}, []
    used = set(existing.keys())

    for i, url in enumerate(urls, 1):
        ext = guess_ext(url)
        name = ts_name(ext, used)
        dest = images_dir / name
        if download(url, dest, args.referer):
            url_map[url] = name
            print(f"OK [{i}/{len(urls)}]: {name}", flush=True)
        else:
            failed.append(url)
            print(f"FAIL [{i}/{len(urls)}]: {url[:90]}", flush=True)

    # 替换引用为相对路径
    def repl(m):
        alt, src = m.group(1), m.group(2)
        if src in url_map:
            return f"![{alt}]({(images_dir / url_map[src]).relative_to(md_dir).as_posix()})"
        return m.group(0)

    new_text = IMG_RE.sub(repl, text)
    if new_text != text:
        md_path.write_text(new_text, encoding="utf-8")

    print(f"\n下载成功: {len(url_map)}/{len(urls)}", flush=True)
    print(json.dumps({"downloaded": len(url_map), "failed": failed,
                      "images_dir": str(images_dir),
                      "map": url_map}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
