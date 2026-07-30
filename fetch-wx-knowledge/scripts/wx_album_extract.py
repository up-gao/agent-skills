#!/usr/bin/env python3
"""
Extract article URLs from a WeChat album/column page.

WeChat album pages (mp.appmsgalbum) render article lists via JavaScript,
but the article links are present in the raw HTML. This script extracts:
- Album name (from og:title or page heuristics)
- List of unique article URLs

Output: JSON with album_name and article_urls
"""

import re
import sys
import json
import argparse
from html import unescape


def extract_album(html, album_url):
    """Extract article URLs from a WeChat album page HTML."""

    # Step 1: Find album name
    album_name = ''
    # Try og:title
    m = re.search(r'<meta\s[^>]*property="og:title"[^>]*content="([^"]*)"', html, re.I)
    if m:
        album_name = unescape(m.group(1)).strip()
    if not album_name:
        m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
        if m and m.group(1).strip():
            album_name = m.group(1).strip()

    # Step 2: Extract article links
    # Pattern: /s?__biz=...&mid=...&idx=...&sn=...&chksm=...
    # Links may have &amp; instead of &
    pattern = r'mp\.weixin\.qq\.com/s\?__biz=[^"\'#\s]+'
    raw_urls = re.findall(pattern, html)

    # Clean URLs: decode &amp; → &, remove #rd suffix, make absolute
    article_urls = []
    seen = set()
    for url in raw_urls:
        url = url.replace('&amp;', '&')
        # Remove trailing #rd or #wechat_redirect
        url = re.sub(r'#.*$', '', url)
        full_url = 'https://' + url
        if full_url not in seen:
            seen.add(full_url)
            article_urls.append(full_url)

    return {
        'album_name': album_name,
        'album_url': album_url,
        'article_count': len(article_urls),
        'article_urls': article_urls,
    }


def main():
    parser = argparse.ArgumentParser(description='Extract article URLs from WeChat album page')
    parser.add_argument('--input', '-i', required=True, help='Input HTML file (raw album page)')
    parser.add_argument('--album-url', '-u', default='', help='Album page URL (for output metadata)')
    parser.add_argument('--output', '-o', help='Output JSON file (default: stdout)')
    args = parser.parse_args()

    with open(args.input, 'r', encoding='utf-8') as f:
        html = f.read()

    result = extract_album(html, args.album_url)

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"Album: {result['album_name'] or '(unnamed)'}")
        print(f"Articles: {result['article_count']}")
        for i, url in enumerate(result['article_urls']):
            print(f"  [{i+1}] {url}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
