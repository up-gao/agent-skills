#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""采集微信 / 掘金 / 今日头条文章，并把完整 Markdown（含图）写入飞书知识库。

流程：
  1. 识别链接来源（wx / juejin / toutiao），调用对应的本地采集技能拿到完整 .md
  2. 规范化图片路径为相对路径
  3. 调用 feishu_doc_writer/scripts/publish.py 写入知识库节点（新建或覆盖）

用法：
  # 单篇，自动识别来源，在知识库新建节点
  python3 save_to_wiki.py --url "https://mp.weixin.qq.com/s/xxx" --space 7689120845356092370

  # 多篇（共享工作目录、图片去重）
  python3 save_to_wiki.py --url "..." --url "..." --space <space_id>

  # 写入知识库已有文档
  python3 save_to_wiki.py --url "..." --wiki https://xxx.feishu.cn/wiki/XXXX

  # 指定父节点 / 只采集不发布 / 只发布已有 md
  python3 save_to_wiki.py --url "..." --space <id> --parent-node <node_token>
  python3 save_to_wiki.py --url "..." --space <id> --collect-only
  python3 save_to_wiki.py --md /path/to/article.md --space <id>

退出码：0 全部成功；1 有失败（详见 summary.json）；2 参数/依赖错误。
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from check_dead_link import check_md_content, MIN_CONTENT_CHARS

SKILLS_DIR = Path(__file__).resolve().parents[2]          # .../skills
FETCH_WX = SKILLS_DIR / "fetch-wx-knowledge"
FETCH_WX_ARTICLE = Path(__file__).resolve().parent / "fetch_wx_article.py"
FETCH_TOUTIAO = SKILLS_DIR / "fetch-toutiao-knowledge"
FETCH_JJ = SKILLS_DIR / "fetch-juejin-knowledge"
FETCH_INFOQ = SKILLS_DIR / "fetch-infoq-knowledge"
FETCH_CSDN = SKILLS_DIR / "fetch-csdn-knowledge"
FETCH_ANTHROPIC = SKILLS_DIR / "fetch-anthropic-knowledge"
PUBLISH = SKILLS_DIR / "feishu_doc_writer" / "scripts" / "publish.py"
# 本编排器自带的 HTML→Markdown 转换器（解耦合，不引用其他技能）
LOCAL_HTML_TO_MD = Path(__file__).resolve().parent / "html_to_md.py"
DOWNLOAD_IMAGES = Path(__file__).resolve().parent / "download_images.py"

WORKSPACE0 = Path(__file__).resolve().parents[3]
LOG_DIR = WORKSPACE0 / "wk_logs"

# 与 log_upload.py 同源的实现（脚本内部内联，避免子进程开销）
SOURCE_CN = {"wx": "微信", "toutiao": "头条", "juejin": "掘金"}

FENCE_RE = re.compile(r"^\s*```")
IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
OK_IMG_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")


def log(msg):
    print(msg, flush=True)


def agent_name(explicit=None):
    return explicit or os.environ.get("KG_AGENT_NAME") or "kg_fetcher_robot"


def write_operation_log(source, url, agent=None, ok=None, detail=""):
    """追加一行操作日志到 <工作区>/wk_logs/upload_md_fs_wiki_YYYY_MM_DD.log。

    格式：[时间戳]  [agent名称]：导入微信/头条/掘金文章（URL）到wiki库
    永不抛异常：日志是旁路功能，不能中断采集上传。
    """
    try:
        now = time.localtime()
        target = LOG_DIR / time.strftime(
            "upload_md_fs_wiki_%Y_%m_%d.log", now)
        target.parent.mkdir(parents=True, exist_ok=True)
        cn = SOURCE_CN.get(source, source or "其他")
        line = (f"[{time.strftime('%Y-%m-%d %H:%M:%S', now)}]  "
                f"[{agent_name(agent)}]：导入{cn}文章（{url}）到wiki库")
        if ok is False:
            line += f" —— 失败：{detail}" if detail else " —— 失败"
        elif ok is True and detail:
            line += f" —— {detail}"
        fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                     0o644)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
        finally:
            os.close(fd)
        return target
    except Exception:                                            # noqa: BLE001
        log("  ! 写入操作日志失败（不影响主流程）")
        return None


def die(msg, code=1):
    print(f"✗ {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


def count_remote_images(md_path):
    """统计 Markdown 中仍是远程 URL 的图片数量（发布前把关用）。"""
    text = Path(md_path).read_text(encoding="utf-8")
    return sum(1 for m in IMG_RE.finditer(text)
               if m.group(2).startswith(("http://", "https://")))


def _last_json(text):
    """取出输出中最后一行可解析的 JSON（子脚本的结果行）。"""
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


# --------------------------------------------------------------------------
# Step 0: 链接识别
# --------------------------------------------------------------------------

SUPPORTED_SOURCES = {
    "wx": "微信公众号（mp.weixin.qq.com）",
    "juejin": "掘金（juejin.cn）",
    "infoq": "InfoQ 中国站（infoq.cn / xie.infoq.cn）",
    "toutiao": "今日头条（toutiao.com）",
    "csdn": "CSDN 博客（blog.csdn.net）",
    "anthropic": "Anthropic / Claude 博客（claude.com / anthropic.com）",
}


def detect_source(url):
    """根据链接域名判断文章来源。

    支持：微信公众号 / 掘金 / InfoQ / 今日头条；其余返回 None。
    显式排除英文站 infoq.com（405 反爬，不可用）。
    """
    u = url.lower()
    if "mp.weixin.qq.com" in u or "mp.appmsgalbum" in u:
        return "wx"
    if "juejin.cn" in u or "juejin.im" in u:
        return "juejin"
    if "toutiao.com" in u:
        return "toutiao"
    # InfoQ 中国站：主站服务端渲染，写作社区 xie.infoq.cn 需 JS 渲染
    if "infoq.cn" in u:
        return "infoq"
    # CSDN 博客：blog.csdn.net/<user>/article/details/<id>
    if "blog.csdn.net" in u or "csdn.net" in u:
        return "csdn"
    # Anthropic / Claude 博客：claude.com/blog/... 或 anthropic.com/...
    if "claude.com" in u or "anthropic.com" in u:
        return "anthropic"
    # infoq.com 是英文站，返回 405 反爬页，不可采集（不要误归为 infoq）
    return None


def unsupported_message(url):
    """构造「暂不支持该来源」的提示，列出已支持渠道。"""
    try:
        host = urlparse(url).netloc or url
    except Exception:                                    # noqa: BLE001
        host = url
    supported = "、".join(SUPPORTED_SOURCES.values())
    hint = f"暂不支持该文章来源渠道（{host}）。目前已支持：{supported}。"
    if "infoq.com" in url.lower():
        hint += " 注：infoq.com 为英文站，有反爬拦截，请改用 infoq.cn 的中文文章链接。"
    return hint


def run(cmd, **kw):
    """运行子进程，实时输出，返回 (returncode, stdout)。"""
    log(f"  $ {' '.join(str(c) for c in cmd)}")
    p = subprocess.run(cmd, capture_output=True, text=True, **kw)
    out = (p.stdout or "") + (p.stderr or "")
    for line in out.splitlines():
        log(f"    {line}")
    return p.returncode, out


# --------------------------------------------------------------------------
# 各来源采集
# --------------------------------------------------------------------------

def slugify(name, max_len=100):
    name = re.sub(r'[\\/:*?"<>|\n\r\t]', "_", name).strip()
    name = re.sub(r"\s+", " ", name)
    return (name[:max_len] or "untitled").strip()


def localize_images(md_path, images_dir, referer=None, retries=2):
    """下载 Markdown 中的远程图片并替换为相对路径。

    微信图片必须显式下载（`html_to_md.py` 只负责转格式，不下载图片）。
    下载失败的图片会重试 `retries` 轮（微信 CDN 偶发超时）。
    返回 (下载数, 失败 URL 列表)。
    """
    if not DOWNLOAD_IMAGES.exists():
        log(f"  ! 找不到图片下载脚本 {DOWNLOAD_IMAGES}，跳过图片本地化")
        return 0, []

    total_ok, remaining = 0, []
    for attempt in range(retries + 1):
        remote = count_remote_images(md_path)
        if remote == 0:
            break
        if attempt:
            log(f"  图片重试第 {attempt} 轮（剩余 {remote} 张）")
            time.sleep(2)
            # 重跑：上一轮成功的已改为本地路径，不会重复下载
        cmd = [sys.executable, str(DOWNLOAD_IMAGES), str(md_path),
               "--images", str(images_dir)]
        if referer:
            cmd += ["--referer", referer]
        rc, out = run(cmd)
        if rc != 0:
            break
        m = re.search(r"\{\"downloaded\".*\}", out)
        if not m:
            break
        try:
            info = json.loads(m.group(0))
        except json.JSONDecodeError:
            break
        total_ok += info.get("downloaded", 0)
        remaining = info.get("failed", [])
        if not remaining:
            break
    return total_ok, remaining


def collect_wx(url, workdir):
    """微信公众号：一次调用 fetch_wx_article.py（内置抓页/预处理/转换/图片）。"""
    before = set(workdir.glob("*.md"))
    rc, out = run([sys.executable, str(FETCH_WX_ARTICLE), url,
                   "--output-dir", str(workdir)])
    info = _last_json(out)
    if info and info.get("images_failed"):
        log(f"  图片本地化：{info['images_failed']} 张失败（重试后仍未成功）")
    after = [p for p in workdir.glob("*.md") if p not in before]
    if not after:
        return None, "微信采集失败（抓页被拦或链接失效，重试一次通常可解决）"
    return max(after, key=lambda p: p.stat().st_mtime), None


def collect_toutiao(url, workdir):
    """今日头条：Playwright 渲染脚本。"""
    before = set(workdir.glob("*.md"))
    rc, out = run([sys.executable, str(FETCH_TOUTIAO / "scripts" / "scrape_toutiao.py"),
                   url, str(workdir)])
    after = [p for p in workdir.glob("*.md") if p not in before]
    if not after:
        return None, "未生成 Markdown（页面结构变化或渲染超时，可重试）"
    return max(after, key=lambda p: p.stat().st_mtime), None


def collect_juejin(url, workdir):
    """掘金：走 API 脚本采集（输出绝对路径图片）。

    失败原因必须准确区分：
      - 文章不存在/已删除（接口 404）→ 不再重试，如实报告
      - 外层命令超时被截断 → 提示可重跑
    旧版无条件报「超时，重跑即可」，会把 404 死链误导成可重试问题。
    """
    m = re.search(r"post/(\d+)", url)
    if not m:
        return None, "不是掘金文章链接（仅支持 juejin.cn/post/:id 单篇）"
    article_id = m.group(1)

    before = set(workdir.glob("*.md"))
    rc, out = run([sys.executable, str(FETCH_JJ / "scripts" / "fetch_juejin.py"),
                   "article", "--id", article_id, "--output", str(workdir)])
    after = [p for p in workdir.glob("*.md") if p not in before]
    if not after:
        # 上游脚本失败时会输出 {"ok": false, "reason": "..."}，优先采用
        info = _last_json(out)
        if info and info.get("ok") is False and info.get("reason"):
            return None, info["reason"]
        # 没有机器可读原因时，再从输出里找线索
        low = out.lower()
        if "404" in low or "内容为空" in out:
            return None, "文章不存在或已删除（接口返回 404/内容为空）"
        return None, "掘金采集中止（可能为命令超时被截断，可重跑一次）"
    return max(after, key=lambda p: p.stat().st_mtime), None


def collect_infoq(url, workdir):
    """InfoQ 中国站：委托 fetch-infoq-knowledge 技能。

    - xie.infoq.cn（写作社区）：fetch_xie_article.py，Playwright 渲染，
      已内置图片本地化 + 相对路径，一步产出完整 Markdown。
    - www.infoq.cn（主站）：服务端已渲染，走 infoq_extract.py 抽正文，
      再由调用方统一本地化图片。

    注意：fetch_xie_article.py 的 --output-dir 默认写到工作空间的
    wk_data/infoq，这里必须显式传 workdir，否则文件会落到默认目录。
    """
    before = set(workdir.glob("*.md"))

    if "xie.infoq.cn" in url.lower():
        rc, out = run([sys.executable,
                       str(FETCH_INFOQ / "scripts" / "fetch_xie_article.py"),
                       url, "--output-dir", str(workdir)])
        info = _last_json(out)
        if info and info.get("images_failed"):
            log(f"  图片本地化：{info['images_failed']} 张失败（重试后仍未成功）")
        # 重跑同一篇时文件名相同、不会出现在“新增文件”里，
        # 因此优先用脚本 JSON 里回报的 md_path，回退到“新增文件”。
        reported = info.get("md_path") if info else None
        if reported and Path(reported).exists():
            return Path(reported), None
        after = [p for p in workdir.glob("*.md") if p not in before]
        if not after:
            return None, "InfoQ 写作社区采集失败（渲染失败或链接失效）"
        return max(after, key=lambda p: p.stat().st_mtime), None

    # www.infoq.cn 主站：两段式（抓页 → 抽正文 → 转 md），图片由调用方本地化
    # 该站有速率限制：短时间连续请求会临时返回 403，隔几秒后同一请求又能成功。
    # 所以 403 属于「可重试」而非「死链」，这里做退避重试（最多 3 次）。
    page = workdir / "_infoq_page.html"
    body = workdir / "_infoq_body.html"
    meta = workdir / "_infoq_extract.json"
    ua = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

    MAX_ATTEMPTS = 3
    BACKOFF_SECONDS = (5, 15)          # 第 1 次失败等 5s，第 2 次等 15s
    rc = out = None
    for attempt in range(MAX_ATTEMPTS):
        if attempt:
            wait = BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)]
            log(f"  第 {attempt + 1}/{MAX_ATTEMPTS} 次尝试（等 {wait}s 后退避重试）…")
            time.sleep(wait)
        if page.exists():
            page.unlink()               # 清掉上轮 403 空壳，避免误判
        rc, out = run(["curl", "-sL", "--max-time", "30",
                       "-H", f"User-Agent: {ua}",
                       "-H", "Referer: https://www.infoq.cn/",
                       "-o", str(page), url])
        if rc == 0 and page.exists() and page.stat().st_size > 5000:
            after = run([sys.executable,
                         str(FETCH_INFOQ / "scripts" / "infoq_extract.py"),
                         "--input", str(page), "--output", str(meta),
                         "--html-output", str(body)])
            if body.exists():
                break                   # 取到正文，退出重试

    if not body.exists():
        if rc != 0:
            return None, "主站抓取失败（网络错误）"
        return None, (f"主站连续 {MAX_ATTEMPTS} 次未取到正文（该站有速率限制，"
                      "403 为临时拦截；请稍等几分钟后重试）")

    try:
        info = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        info = {}
    title = info.get("title") or "未命名文章"
    md_path = workdir / f"{slugify(title)}.md"

    rc, out = run([sys.executable, str(LOCAL_HTML_TO_MD),
                   "--input", str(body), "--output", str(md_path),
                   "--source", "infoq",
                   "--title", title, "--base-url", "https://www.infoq.cn",
                   "--source-url", url,
                   "--author", info.get("author") or "",
                   "--publish-time", info.get("date") or ""])
    if rc != 0 or not md_path.exists():
        return None, "Markdown 转换失败"

    # 主站分支的图片是远程 URL（infoq 图床），需显式本地化并转相对路径。
    # 与微信分支同样的做法：下载失败不中断，但会记入 broken 由上层汇总。
    ok_n, failed = localize_images(md_path, workdir / "images",
                                   referer="https://www.infoq.cn/", retries=2)
    if failed:
        log(f"  图片本地化：成功 {ok_n}，失败 {len(failed)} 张")
    else:
        log(f"  图片本地化：成功 {ok_n} 张")
    return md_path, None


def collect_csdn(url, workdir):
    """CSDN 博客：委托 fetch-csdn-knowledge 技能。

    fetch_csdn_article.py 已内置抓页/抽正文/转 md/图片本地化，
    但它的 --output-dir 默认为技能自己的 wk_data/csdn，
    这里必须显式传 workdir，否则文件会落到默认目录。
    """
    before = set(workdir.glob("*.md"))
    rc, out = run([sys.executable,
                   str(FETCH_CSDN / "scripts" / "fetch_csdn_article.py"),
                   url, "--output-dir", str(workdir)])
    info = _last_json(out)
    if info and info.get("images_failed"):
        log(f"  图片本地化：{info['images_failed']} 张失败（重试后仍未成功）")
    # 重跑同名文件时不会出现在“新增文件”里，故优先用脚本 JSON 的 md_path
    reported = info.get("md_path") if info else None
    if reported and Path(reported).exists():
        return Path(reported), None
    after = [p for p in workdir.glob("*.md") if p not in before]
    if not after:
        return None, "CSDN 采集失败（抓页被拦、链接失效或页面结构变更）"
    return max(after, key=lambda p: p.stat().st_mtime), None


def collect_anthropic(url, workdir):
    """Anthropic / Claude 博客：委托 fetch-anthropic-knowledge 技能。

    该脚本自带抓页/抽正文/转 md/图片本地化，但 --output-dir 默认为
    技能自己的 wk_data/anthropic，这里必须显式传 workdir。
    """
    before = set(workdir.glob("*.md"))
    rc, out = run([sys.executable,
                   str(FETCH_ANTHROPIC / "scripts" / "fetch_anthropic_article.py"),
                   url, "--output-dir", str(workdir)])
    info = _last_json(out)
    if info and info.get("images_failed"):
        log(f"  图片本地化：{info['images_failed']} 张失败（重试后仍未成功）")
    # 重跑同名文件时不会出现在“新增文件”里，故优先用脚本 JSON 的 md_path
    reported = info.get("md_path") if info else None
    if reported and Path(reported).exists():
        return Path(reported), None
    after = [p for p in workdir.glob("*.md") if p not in before]
    if not after:
        return None, "Anthropic 采集失败（抓页失败或正文容器未找到）"
    return max(after, key=lambda p: p.stat().st_mtime), None


COLLECTORS = {"wx": collect_wx, "toutiao": collect_toutiao,
              "juejin": collect_juejin, "infoq": collect_infoq,
              "csdn": collect_csdn, "anthropic": collect_anthropic}


# --------------------------------------------------------------------------
# Step 2: 图片路径规范化（飞书侧一律用相对路径）
# --------------------------------------------------------------------------

def normalize_image_paths(md_path, images_dir):
    """把 Markdown 里的图片引用统一改成相对于 md 的相对路径。

    兼容三种写法：
      - 相对路径（微信采集）      → 原样保留
      - 绝对路径（掘金采集）      → 改成相对路径
      - 远程 URL（下载失败残留）  → 按文件名在图片目录里找，找不到则保留
    同时校验引用文件是否存在，返回 (改动数, 破损列表)。
    """
    md_path = Path(md_path).resolve()
    md_dir = md_path.parent
    images_dir = Path(images_dir).resolve()
    text = md_path.read_text(encoding="utf-8")

    existing = {}
    if images_dir.exists():
        for p in images_dir.rglob("*"):
            if p.is_file():
                existing.setdefault(p.name, p)

    changed = 0
    broken = []

    def repl(m):
        nonlocal changed
        alt, src = m.group(1), m.group(2)
        if src.startswith(("http://", "https://")):
            name = src.split("?")[0].rstrip("/").split("/")[-1]
            hit = existing.get(name)
            if not hit:
                broken.append(src)
                return m.group(0)
            new = os.path.relpath(hit, md_dir).replace(os.sep, "/")
            changed += 1
            return f"![{alt}]({new})"

        src_path = Path(src)
        abs_path = src_path if src_path.is_absolute() else (md_dir / src_path)
        if src_path.is_absolute():
            # 绝对路径 → 相对路径
            if abs_path.exists():
                new = os.path.relpath(abs_path, md_dir).replace(os.sep, "/")
                if new != src:
                    changed += 1
                return f"![{alt}]({new})"
            # 路径失效但同名文件在图片目录里 → 抢救
            hit = existing.get(abs_path.name)
            if hit:
                changed += 1
                return f"![{alt}]({os.path.relpath(hit, md_dir).replace(os.sep, '/')})"
            broken.append(src)
            return m.group(0)

        # 相对路径：确认文件存在
        if abs_path.exists():
            return m.group(0)
        hit = existing.get(src_path.name)
        if hit:
            changed += 1
            return f"![{alt}]({os.path.relpath(hit, md_dir).replace(os.sep, '/')})"
        broken.append(src)
        return m.group(0)

    new_text = IMG_RE.sub(repl, text)
    if new_text != text:
        md_path.write_text(new_text, encoding="utf-8")
    return changed, broken


def strip_h1(text):
    """去掉正文首个一级标题（飞书节点标题已承载）。返回 (title, body)。"""
    lines = text.splitlines()
    title = None
    out = []
    removed = False
    in_fence = False
    for i, line in enumerate(lines):
        if FENCE_RE.match(line):
            in_fence = not in_fence
        if not removed and not in_fence:
            m = re.match(r"^#\s+(.+?)\s*$", line)
            if m:
                title = m.group(1)
                removed = True
                continue
        out.append(line)
    body = "\n".join(out).lstrip("\n")
    return title, body


def doc_title_from_md(md_path):
    """publish.py 的标题推导规则：首个 H1，否则文件名。"""
    for line in Path(md_path).read_text(encoding="utf-8").splitlines():
        m = re.match(r"^#\s+(.+?)\s*$", line)
        if m:
            return m.group(1)
    return Path(md_path).stem


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="采集 微信/掘金/头条 文章并写入飞书知识库")
    ap.add_argument("--url", action="append", default=[],
                    help="文章链接，可重复传入多个")
    ap.add_argument("--md", action="append", default=[],
                    help="已有本地 Markdown，跳过采集直接发布，可重复")
    ap.add_argument("--space", help="知识空间 ID（在其下新建节点）")
    ap.add_argument("--wiki", help="知识库已有文档链接或 node_token（覆盖写入）")
    ap.add_argument("--parent-node", help="新节点的父节点 token（可选）")
    ap.add_argument("--title", help="文档标题（仅单篇有效，默认取 H1）")
    ap.add_argument("--workdir", help="采集工作目录（默认 /tmp/feishu-wiki-<时间戳>）")
    ap.add_argument("--account", default=None, help="飞书账号（默认 kg_fetcher_robot）")
    ap.add_argument("--min-chars", type=int, default=MIN_CONTENT_CHARS,
                    help=f"正文最少字数，低于此值判为内容为空并跳过发布（默认 {MIN_CONTENT_CHARS}）")
    ap.add_argument("--strip-h1", action="store_true",
                    help="写入前去掉 Markdown 首个 H1（避免与节点标题重复）")
    ap.add_argument("--continue-on-error", action="store_true",
                    help="某篇失败时继续处理后续文章")
    ap.add_argument("--collect-only", action="store_true",
                    help="只采集不发布")
    ap.add_argument("--agent", default=None,
                    help="日志里的 agent 名称（默认 kg_fetcher_robot）")
    ap.add_argument("--no-log", action="store_true",
                    help="不写操作日志")
    args = ap.parse_args()

    if not args.url and not args.md:
        ap.error("至少需要一个 --url 或 --md")
    if not args.space and not args.wiki and not args.collect_only:
        ap.error("需要 --space（新建节点）或 --wiki（写入已有文档）")

    if not PUBLISH.exists() and not args.collect_only:
        die(f"找不到发布脚本：{PUBLISH}")

    workdir = Path(args.workdir).resolve() if args.workdir else Path(
        f"/tmp/feishu-wiki-{time.strftime('%Y%m%d-%H%M%S')}")
    workdir.mkdir(parents=True, exist_ok=True)
    images_dir = workdir / "images"
    log(f"工作目录: {workdir}\n")

    # ---------- Step 1: 采集 ----------
    md_files = []
    collected = []      # 仅供操作日志：md 路径 → 原始 URL / 来源
    failures = []

    for md in args.md:
        p = Path(md).expanduser().resolve()
        if not p.exists():
            failures.append((str(p), "文件不存在"))
            continue
        # 已有 md 的图片目录：优先同级 images/
        side = p.parent / "images"
        if side.exists():
            for f in side.iterdir():
                if f.is_file():
                    images_dir.mkdir(parents=True, exist_ok=True)
                    target = images_dir / f.name
                    if not target.exists():
                        shutil.copy2(f, target)
        md_files.append(p)

    for i, url in enumerate(args.url, 1):
        source = detect_source(url)
        label = SUPPORTED_SOURCES.get(source, source) if source else "未知来源"
        log(f"[{i}/{len(args.url)}] {label} · {url}")
        if not source:
            msg = unsupported_message(url)
            failures.append((url, msg))
            log(f"  ✗ {msg}")
            if not args.no_log:
                write_operation_log("其他", url, agent=args.agent, ok=False,
                                    detail=msg)
            if not args.continue_on_error:
                break
            continue
        try:
            md_path, err = COLLECTORS[source](url, workdir)
        except Exception as exc:                                 # noqa: BLE001
            md_path, err = None, f"采集异常：{exc}"
        if err or not md_path:
            # 采集失败：不自动重试，只给出明确提示（含 URL 与原因）
            log(f"  ✗ {url} 链接获取失败：{err}")
            failures.append((url, err))
            if not args.no_log:
                write_operation_log(source, url, agent=args.agent, ok=False,
                                    detail=err or "采集失败")
            if not args.continue_on_error:
                break
            continue
        log(f"  ✓ 采集完成：{md_path.name}")

        # 采集成功但正文为空/过少：同样不重试，明确提示并跳过发布
        d_dead, d_reason, d_chars = check_md_content(md_path, args.min_chars)
        if d_dead:
            msg = f"{url} 链接获取失败：链接获取内容为空（{d_reason}）"
            log(f"  ✗ {msg}")
            failures.append((url, msg))
            if not args.no_log:
                write_operation_log(source, url, agent=args.agent, ok=False,
                                    detail=msg)
            if not args.continue_on_error:
                break
            continue

        # 掘金脚本输出绝对路径、微信脚本置于同级 images/，统一收敛到 workdir/images
        changed, broken = normalize_image_paths(md_path, images_dir)
        if changed or broken:
            log(f"  图片路径规范化：改动 {changed}，破损 {len(broken)}")
        if broken:
            log(f"  ! 以下图片未本地化（保留原链接）：{broken[:3]}")
        md_files.append(md_path)
        collected.append({"url": url, "source": source, "md": md_path})
        time.sleep(2)  # 站点友好间隔

    log("")
    if not md_files:
        log("没有可发布的 Markdown。")
        for u, e in failures:
            log(f"  ✗ {u} — {e}")
        return 1

    if args.collect_only:
        log("采集完成（--collect-only，未发布）：")
        for p in md_files:
            log(f"  {p}")
        if not args.no_log:
            for c in collected:
                write_operation_log(c["source"], c["url"], agent=args.agent,
                                    ok=True, detail="仅采集到本地，未上传")
            log(f"操作日志：{LOG_DIR}")
        return 0

    # ---------- Step 2: 发布 ----------
    results = []
    for i, md_path in enumerate(md_files, 1):
        log("=" * 60)
        log(f"发布 [{i}/{len(md_files)}] {md_path.name}")

        target = md_path
        if args.strip_h1:
            raw = md_path.read_text(encoding="utf-8")
            h1, body = strip_h1(raw)
            if h1:
                stripped = workdir / f"_stripped_{md_path.name}"
                stripped.write_text(body, encoding="utf-8")
                target = stripped
                log(f"  已移除重复 H1：{h1}")

        title = args.title if (args.title and len(md_files) == 1) \
            else doc_title_from_md(md_path)

        # 发布前把关：仍为远程 URL 的图片会被飞书侧跳过，先告诉调用方
        remote_left = count_remote_images(target)
        if remote_left:
            log(f"  ! 仍有 {remote_left} 张图片是远程链接，将在飞书文档中**缺口**")

        cmd = [sys.executable, str(PUBLISH), str(target), "--title", title]
        if args.wiki:
            cmd += ["--wiki", args.wiki]
        else:
            cmd += ["--space", args.space]
            if args.parent_node:
                cmd += ["--parent-node", args.parent_node]
        if args.account:
            cmd += ["--account", args.account]

        rc, out = run(cmd)
        m = re.search(r"✓ 完成(?:（知识库）)?：(\S+)", out)
        link = m.group(1) if m else None
        # 校验行：例如「校验：共 156 块，图片块 4（空 0），表格块 2」
        v = re.search(r"校验：共 (\d+) 块，图片块 (\d+)（空 (\d+)），表格块 (\d+)", out)
        empty_imgs = int(v.group(3)) if v else None
        ok = rc == 0 and bool(link) and not empty_imgs
        results.append({"md": str(md_path), "title": title, "ok": ok,
                        "link": link, "remote_images_left": remote_left,
                        "empty_image_blocks": empty_imgs})

        # 操作日志：只有 --url 采集的才记（--md 走已有本地文件，无来源 URL）
        if not args.no_log:
            info = next((c for c in collected if c["md"] == md_path), None)
            if info:
                detail = link or ""
                if remote_left:
                    detail += f"（缺图 {remote_left} 张）"
                write_operation_log(info["source"], info["url"],
                                    agent=args.agent, ok=ok, detail=detail)

        if not ok and not args.continue_on_error:
            log("  ✗ 发布失败，终止（可加 --continue-on-error 继续后续文章）")
            break

    # ---------- 汇总 ----------
    log("")
    log("=" * 60)
    ok_n = sum(1 for r in results if r["ok"])
    log(f"完成：{ok_n}/{len(md_files)} 篇写入飞书知识库")
    total_missing = sum(r.get("remote_images_left") or 0 for r in results)
    for r in results:
        log(f"  [{'✓' if r['ok'] else '✗'}] {r['title']}")
        if r["link"]:
            log(f"      {r['link']}")
        if r.get("remote_images_left"):
            log(f"      ! 缺图 {r['remote_images_left']} 张")
    if total_missing:
        log(f"\n⚠ 共 {total_missing} 张图片未能本地化，飞书文档中对应位置为空白。")
        log("  原始链接已保留在采集的 Markdown 文件里，可手动补传。")
    if failures:
        log("采集失败：")
        for u, e in failures:
            log(f"  ✗ {u} — {e}")

    summary = {"workdir": str(workdir), "ok": ok_n,
               "total": len(md_files), "missing_images": total_missing,
               "results": results,
               "failures": [{"url": u, "error": e} for u, e in failures]}
    (workdir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"\n汇总：{workdir / 'summary.json'}")

    # 操作日志：采集后未进入发布阶段就中断的篇目（整批立即中断的情况）
    if not args.no_log:
        logged = {r["md"] for r in results}
        for c in collected:
            if str(c["md"]) not in logged:
                write_operation_log(c["source"], c["url"],
                                    agent=args.agent, ok=False,
                                    detail="已采集未发布（前序失败中断）")
        if not args.collect_only and results:
            log(f"操作日志：{LOG_DIR / ('upload_md_fs_wiki_' + time.strftime('%Y_%m_%d') + '.log')}")

    return 0 if ok_n == len(md_files) and not failures \
        and not total_missing else 1


if __name__ == "__main__":
    sys.exit(main())
