#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""采集与上传飞书知识库的操作日志。

日志目录：<工作区>/wk_logs
日志文件：upload_md_fs_wiki_YYYY_MM_DD.log（按天切分）
行格式：  [YYYY-MM-DD HH:MM:SS]  [agent名称]：导入微信/头条/掘金文章（URL）到wiki库

设计要点：
- 只追加，不覆盖；并发追加用 O_APPEND 单次 write，避免交错。
- 写日志失败绝不影响主流程（容错优先）。
- agent 名称默认 kg_fetcher_robot，可用 --agent / 环境变量 KG_AGENT_NAME 覆盖。
"""

import os
from datetime import datetime
from pathlib import Path

# <工作区>/skills/import_article_to_wiki/scripts/log_upload.py → 工作区
WORKSPACE = Path(__file__).resolve().parents[3]
DEFAULT_LOG_DIR = WORKSPACE / "wk_logs"

SOURCE_CN = {"wx": "微信", "toutiao": "头条", "juejin": "掘金"}


def log_dir(path=None):
    d = Path(path).expanduser().resolve() if path else DEFAULT_LOG_DIR
    return d


def agent_name(explicit=None):
    return explicit or os.environ.get("KG_AGENT_NAME") or "kg_fetcher_robot"


def log_file(when=None, directory=None):
    """当天日志文件路径：upload_md_fs_wiki_YYYY_MM_DD.log"""
    when = when or datetime.now()
    return log_dir(directory) / f"upload_md_fs_wiki_{when.strftime('%Y_%m_%d')}.log"


def format_line(source, url, agent=None, when=None, ok=None, detail=""):
    """构造一行日志（不含换行）。

    source: 'wx' / 'toutiao' / 'juejin'（或 '其他'）
    ok: 成功 True / 失败 False / None 不标注
    """
    when = when or datetime.now()
    cn = SOURCE_CN.get(source, source or "其他")
    line = (f"[{when.strftime('%Y-%m-%d %H:%M:%S')}]  "
            f"[{agent_name(agent)}]：导入{cn}文章（{url}）到wiki库")
    if ok is False:
        line += f" —— 失败：{detail}" if detail else " —— 失败"
    elif ok is True and detail:
        line += f" —— {detail}"
    return line


def write_log(source, url, agent=None, ok=None, detail="", directory=None,
              when=None):
    """追加一行日志到当天文件。返回写入的文件路径（或 None 表示失败）。

    本函数永不抛异常：日志是旁路功能，不能因磁盘/权限问题中断采集上传。
    """
    try:
        when = when or datetime.now()
        target = log_file(when, directory)
        target.parent.mkdir(parents=True, exist_ok=True)
        line = format_line(source, url, agent, when, ok, detail) + "\n"
        # O_APPEND 保证多进程追加不互相覆盖
        fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                     0o644)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
        return target
    except Exception:                                            # noqa: BLE001
        return None


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        description="写入一条「导入文章到 wiki 库」日志")
    ap.add_argument("url", help="文章 URL")
    ap.add_argument("--source", default="其他",
                    help="来源：wx / toutiao / juejin（或中文）")
    ap.add_argument("--agent", help="agent 名称（默认 kg_fetcher_robot）")
    ap.add_argument("--ok", choices=["yes", "no"], help="结果")
    ap.add_argument("--detail", default="", help="附加说明")
    ap.add_argument("--log-dir", help="日志目录（默认 <工作区>/wk_logs）")
    args = ap.parse_args()

    ok = None if not args.ok else (args.ok == "yes")
    path = write_log(args.source, args.url, agent=args.agent, ok=ok,
                     detail=args.detail, directory=args.log_dir)
    if path:
        print(f"已写入：{path}")
    else:
        print("写入失败（不影响主流程）")
