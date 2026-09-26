#!/usr/bin/env python3
"""从 InfoQ 中国站（infoq.cn）文章页抽取标题 / 作者 / 日期 / 正文。

用法:
    python3 infoq_extract.py --input page.html \
        --output extract.json --html-output body.html

退出码:
    0  成功
    2  参数/输入错误（文件不存在等）
    3  未找到正文容器（多为 403 拦截页或非文章页）
"""
import argparse
import json
import re
import sys


def _find_matching_div_end(html: str, start: int) -> int:
    """返回 start 处 <div ...> 对应闭合标签之后的偏移；失败返回 -1。"""
    depth = 0
    for m in re.finditer(r"<(/?)div\b", html[start:]):
        if m.group(1) == "":
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                return start + m.end()
    return -1


def extract(html: str) -> dict:
    """抽取文章信息。找不到正文时 content 为 None。"""
    info = {"title": None, "author": None, "date": None, "content": None}

    m = re.search(r'<h1 class="article-title"[^>]*>(.*?)</h1>', html, re.S)
    if m:
        info["title"] = re.sub(r"<[^>]+>", "", m.group(1)).strip()

    m = re.search(r'class="com-author-name"[^>]*>(.*?)</a>', html, re.S)
    if m:
        info["author"] = re.sub(r"<[^>]+>", "", m.group(1)).strip()

    m = re.search(r'class="read-time"[^>]*>\s*<li[^>]*>\s*(\d{4}-\d{2}-\d{2})', html)
    if m:
        info["date"] = m.group(1)
    else:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", html)
        if m:
            info["date"] = m.group(1)

    start = html.find('<div class="ProseMirror">')
    if start != -1:
        end = _find_matching_div_end(html, start)
        if end != -1:
            info["content"] = html[start:end]

    return info


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="抓取到的 HTML 文件")
    ap.add_argument("--output", required=True, help="抽取结果 JSON 输出路径")
    ap.add_argument("--html-output", help="正文 HTML 片段输出路径")
    args = ap.parse_args()

    try:
        with open(args.input, encoding="utf-8", errors="ignore") as f:
            html = f.read()
    except OSError as e:
        print(f"读取失败: {e}", file=sys.stderr)
        return 2

    info = extract(html)

    if not info["content"]:
        page_title = re.search(r"<title>([^<]*)</title>", html)
        print(
            "未找到正文容器 <div class=\"ProseMirror\">；"
            "可能是 403 拦截页或非文章页。",
            file=sys.stderr,
        )
        print(f"页面 <title>: {page_title.group(1) if page_title else '(无)'}", file=sys.stderr)
        print(f"页面字节数: {len(html)}", file=sys.stderr)
        return 3

    body = info["content"]
    info["html_bytes"] = len(body)
    info["image_count"] = len(re.findall(r"<img", body))
    info["paragraph_count"] = len(re.findall(r"<p[ >]", body))

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)

    if args.html_output:
        with open(args.html_output, "w", encoding="utf-8") as f:
            f.write(body)

    print(f"标题: {info['title']}")
    print(f"作者: {info['author']}")
    print(f"日期: {info['date']}")
    print(f"正文: {info['html_bytes']} 字节, 段落 {info['paragraph_count']}, 图片 {info['image_count']}")
    print(f"JSON: {args.output}")
    if args.html_output:
        print(f"正文 HTML: {args.html_output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
