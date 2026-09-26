#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把本地 Markdown（含图片、表格）按正确顺序发布到飞书云文档 / 知识库。

核心：逐块按 index 写入，保证文档顺序与源文件一致。

用法：
  python3 publish.py <file.md> --title "标题"           # 新建云文档
  python3 publish.py <file.md> --doc-id <token>          # 覆盖已有文档
  python3 publish.py <file.md> --wiki <wiki链接>          # 写入知识库文档
  python3 publish.py <file.md> --space <id> --title "标题" # 知识库新建节点
  python3 publish.py <file.md> --dry-run                 # 预览
"""

import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from feishu_client import FeishuClient, FeishuError  # noqa: E402

IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
TABLE_RE = re.compile(r"^\|.*\|\s*$")
FENCE_RE = re.compile(r"^\s*```")
LIST_RE = re.compile(r"^(\s*)([-*+]|\d+\.)\s+(.*)$")
HEADING_RE = re.compile(r"^(#{1,9})\s+(.*)$")
QUOTE_RE = re.compile(r"^>\s?(.*)$")
HR_RE = re.compile(r"^\s*(---+|\*\*\*+|___+)\s*$")


# ---------------- Markdown 解析 ----------------

def parse_markdown(path):
    """把 Markdown 解析成有序的语义段列表。

    每个元素是 (kind, payload)：
      ('markdown', text)  一个语义段（标题/段落/引用/列表块/代码块）
      ('table', grid)     二维数组
      ('image', path)     图片路径

    切分粒度很重要：转换接口不保证输出顺序，所以每段越小越安全。
    这里以空行为主分隔，同时确保标题、引用、列表项各自独立。
    """
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    elements = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]

        # 空行跳过
        if not line.strip():
            i += 1
            continue

        # 代码块：整体当作一段
        if FENCE_RE.match(line):
            fence = [line]
            i += 1
            while i < n and not FENCE_RE.match(lines[i]):
                fence.append(lines[i])
                i += 1
            if i < n:
                fence.append(lines[i])
                i += 1
            elements.append(("markdown", "\n".join(fence)))
            continue

        # 表格：连续 | 行
        if TABLE_RE.match(line):
            rows = []
            while i < n and TABLE_RE.match(lines[i]):
                rows.append(lines[i])
                i += 1
            grid = _parse_table(rows)
            if grid:
                elements.append(("table", grid))
            continue

        # 图片：独占一行
        m = IMG_RE.fullmatch(line.strip())
        if m:
            elements.append(("image", m.group(2)))
            i += 1
            continue

        # 标题：单行一段
        if HEADING_RE.match(line):
            elements.append(("markdown", line.strip()))
            i += 1
            continue

        # 分割线：单行一段
        if HR_RE.match(line):
            elements.append(("markdown", line.strip()))
            i += 1
            continue

        # 引用：连续的 > 行合成一段
        if QUOTE_RE.match(line):
            quote = []
            while i < n and QUOTE_RE.match(lines[i]):
                quote.append(lines[i].strip())
                i += 1
            elements.append(("markdown", "\n".join(quote)))
            continue

        # 列表：连续的列表项合成一段（保留结构）
        if LIST_RE.match(line):
            items = []
            while i < n:
                cur = lines[i]
                if LIST_RE.match(cur):
                    items.append(cur)
                    i += 1
                elif cur.strip() and (cur.startswith("  ") or cur.startswith("\t")):
                    # 续行
                    items.append(cur)
                    i += 1
                else:
                    break
            elements.append(("markdown", "\n".join(items)))
            continue

        # 普通段落：吸收到空行、标题或其他结构为止
        para = []
        while i < n:
            cur = lines[i]
            if not cur.strip():
                break
            if (TABLE_RE.match(cur) or FENCE_RE.match(cur)
                    or HEADING_RE.match(cur) or QUOTE_RE.match(cur)
                    or LIST_RE.match(cur) or HR_RE.match(cur)
                    or IMG_RE.fullmatch(cur.strip())):
                break
            para.append(cur)
            i += 1
        if para:
            elements.append(("markdown", "\n".join(para)))

    return elements


def _parse_table(rows):
    out = []
    for row in rows:
        if re.match(r"^\|[\s\-:|]+\|\s*$", row):
            continue
        out.append([c.strip() for c in row.strip().strip("|").split("|")])
    return out


def resolve_image(src, base_dir):
    if src.startswith(("http://", "https://")):
        return None
    p = Path(src)
    if not p.is_absolute():
        p = p / 1  # placeholder, replaced below
    return None


def resolve_image_path(src, base_dir):
    """解析图片路径，返回绝对 Path 或 None。"""
    if src.startswith(("http://", "https://")):
        return None
    p = Path(src)
    if not p.is_absolute():
        p = base_dir / p
    return p.resolve() if p.exists() else None


def extract_wiki_token(value):
    m = re.search(r"/wiki/([A-Za-z0-9]+)", value)
    return m.group(1) if m else value.strip()


# ---------------- 写入编排 ----------------

def write_ordered(client, doc_id, segments, base_dir, verbose=True):
    """逐段转换 + 逐块按 index 插入，保证文档顺序与源文件一致。

    关键：转换接口不保证输出数组顺序（实测 `# 标题\n\n> A\n\n> B\n\n正文`
    会返回 `[正文, 标题, A, B]`），因此不能批量转换。
    改为每个语义段单独转换（稳定输出 1 块），顺序完全由本循环控制。
    """
    index = 0
    stats = {"blocks": 0, "tables": 0, "images": 0,
             "skipped_images": [], "failed": []}

    for kind, payload in segments:
        if kind == "table":
            grid = payload
            nrow, ncol = len(grid), max(len(r) for r in grid)
            try:
                table_id = client.create_table_at(doc_id, nrow, ncol, index)
                index += 1
                stats["tables"] += 1
                if table_id:
                    client.fill_table(doc_id, table_id, grid)
            except FeishuError as exc:
                stats["failed"].append((kind, str(exc)[:120]))
                if verbose:
                    print(f"  ! 表格失败（index={index}）：{exc}")
            continue

        if kind == "image":
            abs_path = resolve_image_path(payload, base_dir)
            if not abs_path:
                stats["skipped_images"].append(payload)
                if verbose:
                    print(f"  ! 跳过图片 {payload}（远程 URL 或文件缺失）")
                continue
            try:
                client.insert_image_at(doc_id, str(abs_path), index)
                index += 1
                stats["images"] += 1
            except FeishuError as exc:
                stats["failed"].append((kind, str(exc)[:120]))
                if verbose:
                    print(f"  ! 图片失败 {payload}（index={index}）：{exc}")
            continue

        # markdown 段：单独转换，稳定得到 1 个块
        content = payload.strip()
        if not content:
            continue
        try:
            blocks = client.convert_blocks(content)
        except FeishuError as exc:
            stats["failed"].append((kind, str(exc)[:120]))
            if verbose:
                print(f"  ! 转换失败：{content[:40]}… -> {exc}")
            continue

        for blk in blocks:
            try:
                client.insert_block(doc_id, blk, index)
                index += 1
                stats["blocks"] += 1
            except FeishuError as exc:
                stats["failed"].append((kind, str(exc)[:120]))
                if verbose:
                    print(f"  ! 写入失败（index={index}）：{exc}")

    return stats


def main():
    ap = argparse.ArgumentParser(description="Markdown 按序发布到飞书文档/知识库")
    ap.add_argument("file", help="本地 Markdown 路径")
    ap.add_argument("--title", help="文档标题（默认取一级标题或文件名）")
    ap.add_argument("--doc-id", help="已有文档 token，覆盖模式")
    ap.add_argument("--wiki", help="知识库节点链接或 node_token")
    ap.add_argument("--space", help="知识空间 ID，在其下新建节点")
    ap.add_argument("--parent-node", help="新节点的父节点 token")
    ap.add_argument("--folder", help="目标文件夹 token")
    ap.add_argument("--account", help="飞书账号（默认 kg_fetcher_robot）")
    ap.add_argument("--dry-run", action="store_true", help="只预览不写入")
    ap.add_argument("--slow", type=float, default=0.0,
                    help="每块之间的间隔秒数（默认 0，遇到限流可调大）")
    args = ap.parse_args()

    src = Path(args.file).expanduser().resolve()
    if not src.exists():
        print(f"✗ 文件不存在：{src}")
        return 1

    elements = parse_markdown(src)
    base_dir = src.parent

    title = args.title
    if not title:
        for kind, payload in elements:
            if kind == "markdown":
                m = re.search(r"^#\s+(.+)$", payload, re.MULTILINE)
                if m:
                    title = m.group(1).strip()
                    break
        title = title or src.stem

    n_md = sum(1 for k, _ in elements if k == "markdown")
    n_tbl = sum(1 for k, _ in elements if k == "table")
    n_img = sum(1 for k, _ in elements if k == "image")

    print(f"源文件      : {src}")
    print(f"标题        : {title}")
    print(f"元素        : {len(elements)} 个（Markdown 片段 {n_md} / 表格 {n_tbl} / 图片 {n_img}）")
    for kind, payload in elements:
        if kind == "image":
            r = resolve_image_path(payload, base_dir)
            print(f"   - 图片 {payload}  [{'✓' if r else '✗'}]")

    if args.dry_run:
        print("\n[dry-run] 元素顺序预览：\n")
        for i, (kind, payload) in enumerate(elements):
            if kind == "markdown":
                head = payload.splitlines()[0][:60] if payload.strip() else ""
                print(f"  {i:3d} markdown  {head}")
            elif kind == "table":
                print(f"  {i:3d} table     {len(payload)}×{max(len(r) for r in payload)}")
            else:
                print(f"  {i:3d} image     {payload}")
        return 0

    try:
        client = FeishuClient(account=args.account)
    except FeishuError as exc:
        print(f"✗ 凭据加载失败：{exc}")
        return 2

    # 1) 定位或创建文档
    node_token = None
    try:
        if args.doc_id:
            doc_id = args.doc_id
            print(f"\n[1/4] 使用已有文档 {doc_id}")
        elif args.wiki:
            node_token = extract_wiki_token(args.wiki)
            obj, otype, node_token, _sid = client.resolve_wiki_doc(node_token)
            if not obj:
                print(f"✗ 无法解析 wiki 节点 {node_token}")
                return 3
            doc_id = obj
            print(f"[1/4] 知识库节点 {node_token} → 文档 {doc_id}")
        elif args.space:
            node = client.create_wiki_node(args.space, title,
                                           parent_node_token=args.parent_node)
            doc_id = node["obj_token"]
            node_token = node.get("node_token")
            print(f"[1/4] 知识库新建节点 {node_token} → 文档 {doc_id}")
        else:
            doc = client.create_document(title, folder_token=args.folder)
            doc_id = doc["document_id"]
            print(f"[1/4] 创建文档成功 {doc_id}")
    except FeishuError as exc:
        print(f"✗ 定位/创建文档失败：{exc}")
        return 3

    # 2) 清空已有内容（覆盖模式）
    try:
        deleted = client.delete_all_children(doc_id)
        if not args.doc_id and not args.wiki and not args.space:
            pass  # 新文档无需删除
        elif deleted:
            print(f"[2/4] 清空原有 {deleted} 块")
        else:
            print("[2/4] 无原有内容")
    except FeishuError as exc:
        print(f"✗ 清空失败：{exc}")
        return 3

    # 3) 按序逐块写入
    print("[3/4] 按序写入中（逐块 index，稍慢）…")
    t0 = time.time()
    try:
        stats = write_ordered(client, doc_id, elements, base_dir,
                              verbose=True)
    except FeishuError as exc:
        print(f"✗ 写入中断：{exc}")
        print(f"  文档：https://feishu.cn/docx/{doc_id}")
        return 4
    elapsed = time.time() - t0
    print(f"[3/4] 完成：块 {stats['blocks']} / 表格 {stats['tables']} / "
          f"图片 {stats['images']}，耗时 {elapsed:.0f}s")

    # 4) 校验
    try:
        blocks = client.list_blocks(doc_id)
        imgs = [b for b in blocks if b.get("block_type") == 27]
        empty = [b for b in imgs if not (b.get("image") or {}).get("token")]
        tbls = [b for b in blocks if b.get("block_type") == 31]
        print(f"[4/4] 校验：共 {len(blocks)} 块，"
              f"图片块 {len(imgs)}（空 {len(empty)}），表格块 {len(tbls)}")
        if empty:
            print("  ! 存在空图片块，图片未正确写入")
    except FeishuError as exc:
        print(f"  ! 校验失败：{exc}")

    if node_token:
        print(f"\n✓ 完成（知识库）：https://feishu.cn/wiki/{node_token}")
    else:
        print(f"\n✓ 完成：https://feishu.cn/docx/{doc_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
