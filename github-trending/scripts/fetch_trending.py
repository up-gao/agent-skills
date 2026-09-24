#!/usr/bin/env python3
"""Fetch and parse GitHub Trending (daily / weekly / monthly).

Outputs three Markdown tables with: 仓库名称 | 内容介绍 | 星级变化.

Usage:
    python3 fetch_trending.py --out /tmp/gh_trending                 # save html + json
    python3 fetch_trending.py --out /tmp/gh_trending --format markdown
    python3 fetch_trending.py --debug                                # dump parse summary
    python3 fetch_trending.py --ip 140.82.113.3                      # force an IP

No auth required. Read-only.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
from pathlib import Path

BASE = "https://github.com/trending?since={}"
CYCLES = [
    ("daily", "今日"),
    ("weekly", "本周"),
    ("monthly", "本月"),
]
# github.com sometimes resolves to an unroutable IP; probe candidates and use the first that works.
# Verified reachable 2026-09-24: 20.27.177.113. Older 140.82.x.x nodes were unreachable that day.
FALLBACK_IPS = [
    "20.27.177.113",
    "140.82.113.3",
    "140.82.114.3",
    "140.82.112.3",
    "20.205.243.166",
]
# Set from --ip (single IP or comma-separated list); overrides FALLBACK_IPS when set.
FORCED_IPS: list[str] = []
# Cached working IP discovered by auto-probe (None = not probed yet).
_WORKING_IP: str | None = None
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
ROW_RE = re.compile(r'<article class="Box-row".*?</article>', re.S)


def curl(url: str, ip: str | None, timeout: int = 25) -> str | None:
    cmd = [
        "curl", "-sL", "--max-time", str(timeout),
        "-A", UA,
        "-H", "Accept-Language: en-US,en;q=0.9",
        "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    ]
    if ip:
        cmd += ["--resolve", f"github.com:443:{ip}"]
    cmd.append(url)
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10)
    except subprocess.TimeoutExpired:
        return None
    body = out.stdout
    if not body or "Box-row" not in body:
        return None
    return body


def _find_ip(url: str) -> str | None:
    """Return the first reachable candidate IP (probed via a quick request)."""
    candidates = FORCED_IPS if FORCED_IPS else FALLBACK_IPS
    for ip in candidates:
        if curl(url, ip, timeout=8):
            return ip
    return None


def _discover_ip(url: str) -> str | None:
    """Last-resort discovery: resolve github.com and probe every A record."""
    try:
        out = subprocess.run(
            ["getent", "ahostsv4", "github.com"], capture_output=True, text=True, timeout=10
        )
        ips = [line.split()[0] for line in out.stdout.splitlines() if line.strip()]
    except Exception:
        ips = []
    for ip in ips:
        if ip in FALLBACK_IPS:
            continue
        sys.stderr.write(f"[info] probing discovered {ip}\n")
        if curl(url, ip, timeout=8):
            return ip
    return None


def fetch(url: str) -> str | None:
    # 1) default resolution
    body = curl(url, None, timeout=20)
    if body:
        return body
    # 2) fallback IP pool (auto-probe, remember the working one)
    global _WORKING_IP
    if _WORKING_IP is None:
        sys.stderr.write("[info] auto-probing fallback IPs\n")
        _WORKING_IP = _find_ip(url) or ""
    if _WORKING_IP:
        sys.stderr.write(f"[info] using {_WORKING_IP}\n")
        return curl(url, _WORKING_IP)
    # 3) last resort: try each candidate once more with a longer timeout
    for ip in (FORCED_IPS if FORCED_IPS else FALLBACK_IPS):
        sys.stderr.write(f"[info] retrying via {ip}\n")
        body = curl(url, ip, timeout=20)
        if body:
            return body
    # 4) discover live IPs from DNS and probe them
    found = _discover_ip(url)
    if found:
        _WORKING_IP = found
        sys.stderr.write(f"[info] using discovered {found}\n")
        return curl(url, found, timeout=20)
    return None


def _text(fragment: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", fragment))).strip()


def parse_rows(page: str) -> list[dict]:
    rows = ROW_RE.findall(page)
    out: list[dict] = []
    for r in rows:
        m = re.search(r'<h2 class="h3 lh-condensed">\s*<a[^>]*href="/([^"]+)"', r)
        repo = m.group(1).strip() if m else ""
        d = re.search(r'<p class="col-9[^"]*"[^>]*>(.*?)</p>', r, re.S)
        desc = _text(d.group(1)) if d else ""
        st = re.search(r"</svg>\s*([\d,]+)\s*</a>\s*<span", r)
        total = st.group(1) if st else ""
        delta = re.search(r"([\d,]+)\s+stars (today|this week|this month)", r)
        lang = re.search(r'itemprop="programmingLanguage">([^<]+)<', r)
        if not repo:
            continue
        out.append(
            {
                "repo": repo,
                "desc": desc or "—",
                "delta": delta.group(1) if delta else "",
                "total": total or "",
                "lang": _text(lang.group(1)) if lang else "-",
                "url": f"https://github.com/{repo}",
            }
        )
    return out


def to_markdown(label: str, since: str, title: str, items: list[dict]) -> str:
    lines = [f"### {title}", ""]
    lines.append("| 仓库名称 | 仓库地址 | 内容介绍 | 星级变化 | 总星数 | 语言 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for x in items:
        desc = x["desc"].replace("|", "\\|")
        delta = f"+{x['delta']}" if x["delta"] else "—"
        total = x["total"] or "—"
        lines.append(
            f"| `{x['repo']}` | {x['url']} | {desc} | {delta} | ⭐{total} | {x['lang']} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/gh_trending", help="output directory")
    ap.add_argument("--format", choices=["json", "markdown"], default="markdown")
    ap.add_argument("--ip", default=None, help="force github.com IP(s), comma-separated")
    ap.add_argument(
        "--top",
        type=int,
        default=20,
        help="keep only the first N repos per cycle (default 20; 0 = all)",
    )
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    global FORCED_IPS
    if args.ip:
        FORCED_IPS = [x.strip() for x in args.ip.split(",") if x.strip()]

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    result: dict[str, list[dict]] = {}
    failed: list[str] = []

    for since, label in CYCLES:
        url = BASE.format(since)
        page = fetch(url)
        if page is None:
            sys.stderr.write(f"[error] fetch failed: {since}\n")
            failed.append(since)
            result[since] = []
            continue
        (outdir / f"trending_{since}.html").write_text(page, encoding="utf-8")
        items = parse_rows(page)
        if args.top and args.top > 0:
            items = items[: args.top]
        result[since] = items
        sys.stderr.write(f"[ok] {since}: {len(items)} repos\n")
        if args.debug:
            for x in items[:3]:
                sys.stderr.write(f"     {x}\n")

    (outdir / "trending.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if args.format == "markdown":
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        sections = [(since, label) for since, label in CYCLES]
        header_titles = ["1、今日榜单", "2、本周趋势榜", "3、本月趋势榜"]
        chunks = [f"# GitHub 趋势榜 · {today}", ""]
        for idx, (since, label) in enumerate(sections):
            title = header_titles[idx]
            if not result[since]:
                chunks.append(f"### {title}\n\n> 抓取失败，请重试。\n")
                continue
            chunks.append(to_markdown(label, since, title, result[since]))
        print("\n".join(chunks))

    if failed:
        sys.stderr.write(f"[warn] failed cycles: {', '.join(failed)}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
