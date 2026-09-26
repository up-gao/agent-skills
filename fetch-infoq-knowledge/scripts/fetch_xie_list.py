#!/usr/bin/env python3
"""列出 InfoQ 写作社区（xie.infoq.cn）作者的文章列表。

用法:
    # 直接给作者主页
    python3 fetch_xie_list.py --user-url https://xie.infoq.cn/u/wangzhongyang/
    # 或给该作者任意一篇文章，脚本自动解析出作者
    python3 fetch_xie_list.py --article-url https://xie.infoq.cn/article/<uuid>

输出:
    --output 指定 JSON 文件（默认 ./xie_article_list.json）
    同时把 Markdown 清单打到 stdout

退出码:
    0  成功
    2  参数错误 / 依赖缺失
    3  解析失败（取不到作者或接口返回异常）
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# 默认输出到本 agent 工作空间的 wk_data/infoq 目录
WORKSPACE_ROOT = os.path.expanduser("~/.openclaw/wks/kg_fetcher")
DEFAULT_OUTPUT_DIR = os.path.join(WORKSPACE_ROOT, "wk_data", "infoq")

API_LIST = "https://www.infoq.cn/public/v1/user/getListByAuthor"
API_AUTHOR = "https://xie.infoq.cn/public/v1/user/authorInfo"

# 列表接口单页上限（实测 size=100 仍只返回 50 条，50 为服务端硬上限）
PAGE_SIZE = 100
CST = timezone(timedelta(hours=8))


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def launch_page(pw):
    browser = pw.chromium.launch()
    return browser, browser.new_page(user_agent=UA, locale="zh-CN")


def resolve_author_from_article(page, article_url):
    """访问文章页，拦截 authorInfo 接口拿作者信息。

    注意：authorInfo 是 POST 且页面里还有日志埋点请求 URL 也含 authorInfo，
    必须同时按 method == POST 过滤，否则可能拿到埋点响应。
    """
    holder = {}

    def on_resp(r):
        if "authorInfo" in r.url and r.request.method == "POST":
            holder["resp"] = r

    page.on("response", on_resp)
    page.goto(article_url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(8000)

    r = holder.get("resp")
    if not r:
        return None
    try:
        d = r.json()
    except Exception:
        return None
    data = d.get("data") or {}
    if not data.get("ucode") or not data.get("uid"):
        return None
    return {
        "uid": data.get("uid"),
        "ucode": data.get("ucode"),
        "nickname": data.get("nickname"),
        "uri": data.get("uri"),
    }


def resolve_author_from_user_url(page, user_url):
    """访问作者主页，同样靠 authorInfo 接口拿作者信息。"""
    if "/publish" not in user_url:
        user_url = user_url.rstrip("/") + "/publish"
    return resolve_author_from_article(page, user_url)


def fetch_list(page, ucode, max_pages=20):
    """POST 分页拉取文章列表。返回 (items, pages_fetched, truncated)。"""
    all_items, seen = [], set()
    truncated = False

    for pno in range(max_pages):
        res = page.evaluate(
            """async (args) => {
                const r = await fetch(args.url, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    credentials: 'include',
                    body: JSON.stringify({id: args.id, size: args.size,
                                          source: 0, order_type: 0, page: args.page})
                });
                const t = await r.text();
                return {status: r.status, text: t};
            }""",
            {"url": API_LIST, "id": ucode, "size": PAGE_SIZE, "page": pno},
        )
        try:
            d = json.loads(res["text"])
        except Exception:
            log(f"  第 {pno + 1} 页解析失败（status={res['status']}）")
            break

        items = d.get("data")
        if not isinstance(items, list) or not items:
            break

        new = 0
        for it in items:
            uuid = it.get("uuid")
            if not uuid or uuid in seen:
                continue
            seen.add(uuid)
            new += 1
            all_items.append(it)

        log(f"  第 {pno + 1} 页：{len(items)} 条（新增 {new}）")

        # 服务端分页参数无效时（实测 page/offset/pageNo 均不生效），
        # 首页即返回全部；若某页无新增，说明已到末尾，停止翻页。
        if new == 0:
            break
        if len(items) < PAGE_SIZE:
            break
    else:
        truncated = True
        log(f"  已达最大翻页数 {max_pages}，可能未取完")

    return all_items, truncated


def fmt_time(ms):
    if not ms:
        return ""
    try:
        return datetime.fromtimestamp(ms / 1000, CST).strftime("%Y-%m-%d")
    except Exception:
        return ""


def article_url(uuid):
    return f"https://xie.infoq.cn/article/{uuid}"


def to_markdown(author, items):
    lines = [f"# {author.get('nickname') or '未知作者'} 的文章列表", ""]
    info = []
    if author.get("uri"):
        info.append(f"主页: https://xie.infoq.cn{author['uri']}")
    info.append(f"共 {len(items)} 篇")
    lines.append("> " + "　|　".join(info))
    lines.append("")
    for i, it in enumerate(items, 1):
        title = (it.get("article_title") or "").strip()
        date = fmt_time(it.get("publish_time"))
        views = it.get("views", 0)
        lines.append(f"{i}. [{title}]({article_url(it.get('uuid'))})")
        lines.append(f"   - {date}　阅读 {views}")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description="列出 xie.infoq.cn 作者的文章")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--user-url", help="作者主页，如 https://xie.infoq.cn/u/xxx/")
    g.add_argument("--article-url", help="该作者的任意一篇文章链接")
    ap.add_argument("--output", "-o", default=os.path.join(DEFAULT_OUTPUT_DIR, "xie_article_list.json"),
                    help=f"JSON 输出路径（默认 {DEFAULT_OUTPUT_DIR}/xie_article_list.json）")
    ap.add_argument("--md-output", default=None, help="Markdown 清单输出路径（可选）")
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("依赖缺失: 需要 playwright")
        return 2

    with sync_playwright() as p:
        browser, page = launch_page(p)
        try:
            if args.user_url:
                author = resolve_author_from_user_url(page, args.user_url)
            else:
                author = resolve_author_from_article(page, args.article_url)

            if not author:
                log("无法解析作者信息（页面结构可能变更，或链接无效）")
                return 3

            log(f"作者: {author['nickname']} (uid={author['uid']})")
            items, truncated = fetch_list(page, author["ucode"])
        finally:
            browser.close()

    if not items:
        log("未取到任何文章")
        return 3

    md = to_markdown(author, items)
    print(md)

    payload = {
        "author": author,
        "count": len(items),
        "truncated": truncated,
        "articles": [
            {
                "title": (it.get("article_title") or "").strip(),
                "url": article_url(it.get("uuid")),
                "uuid": it.get("uuid"),
                "publish_date": fmt_time(it.get("publish_time")),
                "views": it.get("views", 0),
            }
            for it in items
        ],
    }
    out = os.path.abspath(args.output)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    log(f"JSON: {out}（{len(items)} 篇）")

    if args.md_output:
        with open(args.md_output, "w", encoding="utf-8") as f:
            f.write(md)
        log(f"Markdown: {args.md_output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
