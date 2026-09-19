#!/usr/bin/env python3
"""
掘金文章采集工具 — 通过 API 获取文章内容、下载图片、生成 Markdown。

用法:
    # 获取单篇文章详情（JSON 输出到 stdout）
    python3 fetch_juejin.py article --id <article_id>

    # 获取单篇文章并保存为 Markdown
    python3 fetch_juejin.py article --id <article_id> --output <dir> [--images <dir>]

    # 获取用户文章列表
    python3 fetch_juejin.py user-articles --user-id <user_id> [--cursor 0] [--limit 10]

    # 从 Markdown 内容中下载图片并替换路径
    python3 fetch_juejin.py download-images --md-file <path> --images <dir>
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


# ============================================================
# 常量
# ============================================================

API_BASE = "https://api.juejin.cn"
ARTICLE_DETAIL_PATH = "/content_api/v1/article/detail"
ARTICLE_QUERY_LIST_PATH = "/content_api/v1/article/query_list"
API_QUERY = "aid=2608&uuid=&spider=0"
CLIENT_TYPE = 2608
REQUEST_TIMEOUT = 30
IMAGE_DOWNLOAD_TIMEOUT = 15
VALID_IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp', '.awebp'}


# ============================================================
# API 请求
# ============================================================

def api_post(path, body, timeout=REQUEST_TIMEOUT):
    """调用掘金 API (POST)"""
    url = f"{API_BASE}{path}?{API_QUERY}"
    body_str = json.dumps(body)
    result = subprocess.run(
        ['curl', '-sL', '--max-time', str(timeout),
         '-H', 'Content-Type: application/json',
         '-d', body_str, url],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return {"err_no": -1, "err_msg": f"curl 失败: {result.stderr.strip()}"}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        return {"err_no": -1, "err_msg": f"JSON 解析失败: {e}"}


def get_article_detail(article_id):
    """获取文章详情"""
    return api_post(ARTICLE_DETAIL_PATH, {
        "article_id": article_id,
        "client_type": CLIENT_TYPE
    })


def get_user_articles(user_id, cursor="0", sort_type=2):
    """获取用户文章列表（单页）"""
    return api_post(ARTICLE_QUERY_LIST_PATH, {
        "user_id": user_id,
        "cursor": cursor,
        "sort_type": sort_type,
        "client_type": CLIENT_TYPE
    })


# ============================================================
# 工具函数
# ============================================================

def slugify(s, max_len=80):
    """将字符串转为安全的文件名"""
    s = re.sub(r'[^\w\s-]', '', s)
    s = re.sub(r'[-\s]+', '-', s)
    return s.strip('-')[:max_len] or 'untitled'


def format_timestamp(ts):
    """Unix 时间戳 → 日期字符串"""
    try:
        return datetime.fromtimestamp(int(ts)).strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, OSError):
        return '未知'


def extract_article_id_from_url(url):
    """从掘金文章 URL 提取文章 ID"""
    match = re.search(r'post/(\d+)', url)
    return match.group(1) if match else None


def extract_user_id_from_url(url):
    """从掘金用户 URL 提取用户 ID"""
    match = re.search(r'user/(\d+)', url)
    return match.group(1) if match else None


def extract_column_id_from_url(url):
    """从掘金专栏 URL 提取专栏 ID"""
    match = re.search(r'column/(\d+)', url)
    return match.group(1) if match else None


# ============================================================
# Markdown 生成
# ============================================================

def build_markdown(article_data, source_url):
    """从 API 返回数据组装 Markdown 内容"""
    detail = article_data['data']
    info = detail['article_info']
    author = detail.get('author_user_info', {})

    title = info.get('title', '无标题')
    mark_content = info.get('mark_content', '') or info.get('brief_content', '') or ''
    ctime = format_timestamp(info.get('ctime', '0'))
    tag_ids = info.get('tag_ids', [])
    tags = ', '.join(str(t) for t in tag_ids) if tag_ids else '无'
    author_name = author.get('user_name', '未知')
    view_count = info.get('view_count', 0)
    digg_count = info.get('digg_count', 0)
    collect_count = info.get('collect_count', 0)
    comment_count = info.get('comment_count', 0)

    return f"""# {title}

> 原文链接：{source_url}
> 作者：{author_name} | 发布时间：{ctime}
> 标签：{tags}
> 阅读：{view_count} | 点赞：{digg_count} | 收藏：{collect_count} | 评论：{comment_count}

{mark_content}
"""


def save_markdown(md_content, output_dir, title):
    """保存 Markdown 到文件，返回文件路径"""
    os.makedirs(output_dir, exist_ok=True)
    filename = slugify(title) + '.md'
    filepath = os.path.join(output_dir, filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(md_content)
    return filepath


# ============================================================
# 图片下载与替换
# ============================================================

def get_image_extension(url):
    """从 URL 推断图片扩展名"""
    parsed = urlparse(url)
    path = unquote(parsed.path)
    ext = os.path.splitext(path)[1].lower()
    if ext in VALID_IMAGE_EXTS:
        return ext
    return '.png'  # 默认


def download_image(url, save_path):
    """下载单张图片，返回 True/False"""
    result = subprocess.run(
        ['curl', '-sL', '--max-time', str(IMAGE_DOWNLOAD_TIMEOUT),
         '-H', 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
         '-o', save_path, url],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return False
    # 验证文件非空
    if os.path.exists(save_path) and os.path.getsize(save_path) > 0:
        return True
    # 删除空文件
    if os.path.exists(save_path):
        os.remove(save_path)
    return False


def generate_timestamp_filename(ext):
    """生成时间戳文件名: image-YYYYMMDDHHmmssSSS.ext"""
    now = datetime.now()
    ts = now.strftime('%Y%m%d%H%M%S') + f'{now.microsecond // 1000:03d}'
    return f"image-{ts}{ext}"


def download_images_from_md(md_file, static_dir):
    """从 Markdown 文件中提取图片 URL，下载到 static_dir，返回 URL→文件名的映射"""
    os.makedirs(static_dir, exist_ok=True)

    with open(md_file, 'r', encoding='utf-8') as f:
        content = f.read()

    # 提取所有图片 URL（仅 http/https）
    all_urls = re.findall(r'!\[.*?\]\(([^)]+)\)', content)
    image_urls = []
    seen = set()
    for url in all_urls:
        if url.startswith('http') and url not in seen:
            seen.add(url)
            image_urls.append(url)

    if not image_urls:
        print("无远程图片需要下载")
        return {}

    print(f"发现 {len(image_urls)} 张远程图片\n")
    url_map = {}

    for i, url in enumerate(image_urls):
        ext = get_image_extension(url)
        filename = generate_timestamp_filename(ext)
        save_path = os.path.join(static_dir, filename)

        if download_image(url, save_path):
            url_map[url] = filename
            print(f"OK [{i+1}/{len(image_urls)}]: {filename}")
        else:
            print(f"FAIL [{i+1}/{len(image_urls)}]: {url[:80]}")

    print(f"\n下载成功: {len(url_map)}/{len(image_urls)}")
    return url_map


def replace_image_paths(md_file, static_dir, url_map):
    """将 Markdown 中的远程图片 URL 替换为本地绝对路径"""
    static_dir_abs = os.path.abspath(static_dir)

    with open(md_file, 'r', encoding='utf-8') as f:
        content = f.read()

    warnings = []

    def replace_img(match):
        alt = match.group(1)
        url = match.group(2)
        if url in url_map:
            abs_path = os.path.join(static_dir_abs, url_map[url])
            return f'![{alt}]({abs_path})'
        if not url.startswith('http'):
            return match.group(0)
        warnings.append(url)
        return match.group(0)

    content = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', replace_img, content)

    with open(md_file, 'w', encoding='utf-8') as f:
        f.write(content)

    for w in warnings:
        print(f"WARNING: 未下载的图片: {w[:80]}")

    print(f'图片路径已替换为绝对路径（{len(url_map)} 张）')
    return True


# ============================================================
# 批量采集
# ============================================================

def collect_user_all_articles(user_id):
    """分页拉取用户全部文章，返回文章 URL 列表"""
    all_ids = []
    cursor = '0'

    while True:
        print(f"  获取用户文章列表 (cursor={cursor})...")
        data = get_user_articles(user_id, cursor=cursor)

        if data.get('err_no') != 0:
            print(f"  API 错误: [{data.get('err_no')}] {data.get('err_msg')}")
            break

        articles = data.get('data', [])
        if not articles:
            break

        for a in articles:
            aid = a.get('article_id')
            if aid:
                all_ids.append(aid)

        has_more = data.get('has_more', False)
        if not has_more:
            break

        cursor = str(data.get('cursor', ''))
        if not cursor or cursor == '0':
            break

        time.sleep(1)  # 分页间隔

    return [f"https://juejin.cn/post/{aid}" for aid in all_ids]


# ============================================================
# CLI
# ============================================================

def cmd_article(args):
    """处理 article 子命令"""
    article_id = args.id
    print(f"获取文章详情: {article_id}")

    data = get_article_detail(article_id)

    if data.get('err_no') != 0:
        print(f"API 错误: [{data.get('err_no')}] {data.get('err_msg')}", file=sys.stderr)
        sys.exit(1)

    source_url = f"https://juejin.cn/post/{article_id}"

    if args.output:
        # 完整流程: 生成 MD + 下载图片 + 替换路径
        info = data['data']['article_info']
        title = info.get('title', '无标题')
        md_content = build_markdown(data, source_url)
        md_file = save_markdown(md_content, args.output, title)
        print(f"Markdown 已保存: {md_file}")
        print(f"标题: {title}")
        print(f"正文字符数: {len(info.get('mark_content', '') or info.get('brief_content', ''))}")

        # 下载图片
        images_dir = args.images or os.path.join(args.output, 'images')
        url_map = download_images_from_md(md_file, images_dir)

        # 替换路径
        if url_map:
            replace_image_paths(md_file, images_dir, url_map)
            with open('/tmp/juejin_url_map.json', 'w') as f:
                json.dump(url_map, f)

        # 保存 API 原始数据供后续使用
        with open('/tmp/juejin_article.json', 'w') as f:
            json.dump(data, f, ensure_ascii=False)
    else:
        # 仅输出 JSON
        print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_user_articles(args):
    """处理 user-articles 子命令"""
    user_id = args.user_id
    cursor = args.cursor or '0'

    data = get_user_articles(user_id, cursor=cursor)

    if data.get('err_no') != 0:
        print(f"API 错误: [{data.get('err_no')}] {data.get('err_msg')}", file=sys.stderr)
        sys.exit(1)

    articles = data.get('data', [])
    has_more = data.get('has_more', False)
    next_cursor = data.get('cursor', 0)

    print(f"has_more: {has_more}")
    print(f"next_cursor: {next_cursor}")
    print(f"count: {len(articles)}")
    print()

    for a in articles:
        info = a.get('article_info', {})
        print(f"  [{a.get('article_id')}] {info.get('title', '无标题')}")
        print(f"  https://juejin.cn/post/{a.get('article_id')}")
        print(f"  阅读:{info.get('view_count',0)} 点赞:{info.get('digg_count',0)} 收藏:{info.get('collect_count',0)}")
        print()


def cmd_download_images(args):
    """处理 download-images 子命令"""
    md_file = args.md_file
    images_dir = args.images

    if not os.path.exists(md_file):
        print(f"文件不存在: {md_file}", file=sys.stderr)
        sys.exit(1)

    url_map = download_images_from_md(md_file, images_dir)

    if url_map:
        replace_image_paths(md_file, images_dir, url_map)
        with open('/tmp/juejin_url_map.json', 'w') as f:
            json.dump(url_map, f)


def cmd_build_md(args):
    """从 API JSON 文件构建 Markdown"""
    api_json = args.input
    source_url = args.url

    with open(api_json, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if data.get('err_no') != 0:
        print(f"API 数据错误: [{data.get('err_no')}] {data.get('err_msg')}", file=sys.stderr)
        sys.exit(1)

    md_content = build_markdown(data, source_url)
    info = data['data']['article_info']
    title = info.get('title', '无标题')

    if args.output_dir:
        md_file = save_markdown(md_content, args.output_dir, title)
        print(f"Markdown 已保存: {md_file}")
    else:
        print(md_content)


def main():
    parser = argparse.ArgumentParser(
        description='掘金文章采集工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s article --id 7665184555516395555 --output ./output
  %(prog)s user-articles --user-id 3793793900098220
  %(prog)s download-images --md-file ./output/article.md --images ./output/images
        """
    )
    subparsers = parser.add_subparsers(dest='command', help='子命令')

    # article 子命令
    p_article = subparsers.add_parser('article', help='获取单篇文章')
    p_article.add_argument('--id', required=True, help='文章 ID')
    p_article.add_argument('--output', '-o', help='输出目录（不指定则输出 JSON）')
    p_article.add_argument('--images', help='图片目录（默认: 输出目录/images）')

    # user-articles 子命令
    p_user = subparsers.add_parser('user-articles', help='获取用户文章列表')
    p_user.add_argument('--user-id', required=True, help='用户 ID')
    p_user.add_argument('--cursor', default='0', help='分页游标')
    p_user.add_argument('--limit', type=int, default=10, help='每页数量')

    # download-images 子命令
    p_dl = subparsers.add_parser('download-images', help='从 Markdown 下载图片并替换路径')
    p_dl.add_argument('--md-file', required=True, help='Markdown 文件路径')
    p_dl.add_argument('--images', required=True, help='图片目录')

    # build-md 子命令
    p_build = subparsers.add_parser('build-md', help='从 API JSON 构建 Markdown')
    p_build.add_argument('--input', '-i', required=True, help='API JSON 文件路径')
    p_build.add_argument('--url', required=True, help='原文 URL')
    p_build.add_argument('--output-dir', '-o', help='输出目录（不指定则输出到 stdout）')

    args = parser.parse_args()

    if args.command == 'article':
        cmd_article(args)
    elif args.command == 'user-articles':
        cmd_user_articles(args)
    elif args.command == 'download-images':
        cmd_download_images(args)
    elif args.command == 'build-md':
        cmd_build_md(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
