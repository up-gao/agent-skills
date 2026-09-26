#!/usr/bin/env python3
"""判断文章链接是否为「死链」。

用途：在采集/入库前把关，避免把 404 空壳页或内容过少的页面写进知识库。

两条规则（任一命中即判为死链）：
    1. HTTP 4XX 错误（404 / 403 / 410 等）
    2. 采到的 Markdown 正文过少（默认少于 200 字）

为什么不能只看 HTTP 状态：
    - infoq.cn 被风控拦截时可能仍返回 200（内容却是空壳）
    - 微信文章正常页面的 <title> 常为空，不能靠标题判断
    - 掘金死链返回 404 且 <title> 为「找不到页面」
因此 HTTP 判完还要看「内容量」，两者互补。

用法:
    # 只做 HTTP 检查（快，不采集）
    python3 check_dead_link.py --url "https://..."

    # HTTP + 内容量检查（会真实抓取/采集，慢但准）
    python3 check_dead_link.py --url "https://..." --with-content

    # 检查已落盘的 Markdown 是否内容过少
    python3 check_dead_link.py --md /path/to/article.md

退出码:
    0  链接健康
    1  判定为死链
    2  参数错误
"""
import argparse
import json
import os
import re
import subprocess
import sys
from urllib.parse import urlparse

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# 默认最少正文字数（去掉标点/空白后的字符数）
MIN_CONTENT_CHARS = 200

# 常见「页面不存在」提示语，命中即视为死链（补充 HTTP 状态）
DEAD_PAGE_MARKERS = [
    "找不到页面", "页面不存在", "内容不存在", "该内容已被发布者删除",
    "此内容因违规无法查看", "404 not found", "page not found",
    "article not found", "内容为空", "该文章已删除",
]

# 各来源抓页所需的额外请求头
REFERERS = {
    "mp.weixin.qq.com": "https://mp.weixin.qq.com/",
    "juejin.cn": "https://juejin.cn/",
    "infoq.cn": "https://www.infoq.cn/",
    "toutiao.com": "https://www.toutiao.com/",
}


def log(msg):
    print(msg, flush=True)


def pick_referer(url):
    host = urlparse(url).netloc.lower()
    for domain, ref in REFERERS.items():
        if domain in host:
            return ref
    return None


def fetch_head(url, timeout=25):
    """发一次 GET（只为拿状态码），返回 (status_code, body_snippet)。"""
    referer = pick_referer(url)
    cmd = ["curl", "-sL", "--max-time", str(timeout),
           "-H", f"User-Agent: {UA}",
           "-H", "Accept-Language: zh-CN,zh;q=0.9",
           "-o", "/tmp/_dead_check_body.html",
           "-w", "%{http_code}"]
    if referer:
        cmd += ["-H", f"Referer: {referer}"]
    cmd.append(url)
    p = subprocess.run(cmd, capture_output=True, text=True)
    code = (p.stdout or "").strip()
    try:
        status = int(code)
    except ValueError:
        status = 0
    snippet = ""
    try:
        with open("/tmp/_dead_check_body.html", encoding="utf-8", errors="ignore") as f:
            snippet = f.read(20000)
    except OSError:
        pass
    return status, snippet


def count_content_chars(md_text):
    """统计正文有效字数：去掉标题行、引用行、图片、链接、标点与空白。"""
    text = md_text
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)          # 图片
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)      # 链接保留文字
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith(">"):
            continue                                           # 标题/引用/空行
        if s.startswith("```"):
            continue
        lines.append(s)
    body = "\n".join(lines)
    body = re.sub(r"[\s\W_]+", "", body)                       # 去空白与标点
    return len(body)


def check_url_status(url):
    """规则 1：HTTP 4XX / 页面标记判定。返回 (is_dead, reason, status)。"""
    status, snippet = fetch_head(url)

    if status == 0:
        return True, "无法访问（网络错误或域名不可达）", status
    if 400 <= status < 500:
        return True, f"HTTP {status}（4XX 错误）", status
    if status >= 500:
        return False, f"HTTP {status}（服务端错误，非死链，可重试）", status

    # 200 也要看内容：风控页/删除页可能返回 200
    low = snippet.lower()
    for marker in DEAD_PAGE_MARKERS:
        if marker.lower() in low:
            return True, f"页面提示「{marker}」（HTTP {status} 但内容为空壳）", status
    return False, f"HTTP {status}", status


def check_md_content(md_path, min_chars=MIN_CONTENT_CHARS):
    """规则 2：Markdown 正文过少。返回 (is_dead, reason, chars)。"""
    try:
        with open(md_path, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        return True, f"Markdown 不存在或不可读：{e}", 0

    n = count_content_chars(text)
    if n < min_chars:
        return True, f"正文过少（{n} 字 < {min_chars} 字），疑为死链空壳页", n
    return False, f"正文 {n} 字", n


def main():
    ap = argparse.ArgumentParser(description="判断文章链接是否为死链")
    ap.add_argument("--url", help="待检查的文章链接")
    ap.add_argument("--md", help="已落盘的 Markdown 路径（检查内容量）")
    ap.add_argument("--with-content", action="store_true",
                    help="对 --url 额外做内容量检查（需要采集，较慢）")
    ap.add_argument("--min-chars", type=int, default=MIN_CONTENT_CHARS,
                    help=f"正文最少字数，默认 {MIN_CONTENT_CHARS}")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    args = ap.parse_args()

    if not args.url and not args.md:
        log("至少给 --url 或 --md 之一")
        return 2

    result = {"url": args.url, "md": args.md, "is_dead": False,
              "reasons": [], "checks": []}

    # ---- 规则 1：HTTP 状态 ----
    if args.url:
        dead, reason, status = check_url_status(args.url)
        result["checks"].append({"rule": "http", "dead": dead,
                                 "status": status, "detail": reason})
        if dead:
            result["reasons"].append(reason)

    # ---- 规则 2：内容量 ----
    if args.md:
        dead, reason, n = check_md_content(args.md, args.min_chars)
        result["checks"].append({"rule": "content", "dead": dead,
                                 "chars": n, "detail": reason})
        if dead:
            result["reasons"].append(reason)

    result["is_dead"] = bool(result["reasons"])

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if args.url:
            log(f"链接: {args.url}")
        if args.md:
            log(f"文件: {args.md}")
        for c in result["checks"]:
            flag = "✗ 死链" if c["dead"] else "✓ 正常"
            log(f"  [{c['rule']:7}] {flag} — {c['detail']}")
        log("")
        log("结论: " + ("死链（" + "；".join(result["reasons"]) + "）"
                        if result["is_dead"] else "链接健康"))

    return 1 if result["is_dead"] else 0


if __name__ == "__main__":
    sys.exit(main())
