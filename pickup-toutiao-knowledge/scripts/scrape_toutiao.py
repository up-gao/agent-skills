#!/usr/bin/env python3
"""
Toutiao Article Scraper
Usage: python scrape_toutiao.py <article_url> [output_dir]

Fetches a Toutiao (今日头条) article by rendering it in a headless browser,
extracts title + body content, downloads images locally, and saves as Markdown.

Requirements:
  pip install playwright requests beautifulsoup4
  playwright install chromium

Output:
  - <title>.md        -- article content in Markdown
  - images/            -- downloaded images (referenced locally in the .md)
"""

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.toutiao.com/",
}

REQUEST_TIMEOUT = 30
IMAGE_DIR_NAME = "images"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def clean_title(title: str) -> str:
    """Remove site suffixes like ' - 今日头条' from article titles."""
    title = title.strip()
    title = re.sub(r"\s*[-–—|]\s*(今日头条|toutiao).*", "", title, flags=re.IGNORECASE)
    return title.strip()


def sanitize_filename(name: str) -> str:
    """Remove characters that are unsafe for filesystem names."""
    name = name.strip()
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r"\s+", " ", name)
    max_len = 200
    if len(name) > max_len:
        stem, ext = os.path.splitext(name)
        name = stem[: max_len - len(ext)] + ext
    return name


def fetch_page_with_playwright(url: str) -> str | None:
    """
    Fetch a fully-rendered page using Playwright headless Chromium.
    Returns the HTML string, or None if Playwright is unavailable or fails.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright not installed. Install with: pip install playwright && playwright install chromium")
        return None

    print("Launching headless browser (this may take a few seconds)...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            locale="zh-CN",
            # Block unnecessary resources to speed up loading
        )
        page = context.new_page()

        # Block images + fonts during initial load for speed (we download images separately)
        page.route(
            re.compile(r"\.(png|jpg|jpeg|gif|svg|webp|woff2?|ttf|eot)(\?.*)?$"),
            lambda route: route.abort(),
        )

        try:
            page.goto(url, wait_until="load", timeout=60000)
            # Wait for article content to appear
            selectors_to_try = [
                "article",
                ".article-content",
                ".article-body",
                ".rich_media_content",
                "[class*='article']",
                "h1",
                "p",
            ]
            for sel in selectors_to_try:
                try:
                    page.wait_for_selector(sel, timeout=5000)
                    break
                except Exception:
                    continue

            # Extra wait for lazy-loaded content and images
            time.sleep(5)

            html_text = page.content()
            return html_text
        except Exception as e:
            print(f"Playwright page load error: {e}")
            return None
        finally:
            browser.close()


def fetch_page_plain(url: str) -> str:
    """Fallback: fetch with plain requests (no JS rendering)."""
    resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return resp.text


# ---------------------------------------------------------------------------
# Extraction functions
# ---------------------------------------------------------------------------
def extract_from_jsonld(soup: BeautifulSoup) -> dict | None:
    """Try to extract article data from JSON-LD / schema.org markup."""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict):
                if "@graph" in item:
                    for sub in item["@graph"]:
                        if sub.get("@type") in ("Article", "NewsArticle"):
                            return sub
                if item.get("@type") in ("Article", "NewsArticle"):
                    return item
    return None


def extract_from_meta(soup: BeautifulSoup) -> dict:
    """Extract article metadata from <meta> tags."""
    result: dict = {"title": "", "description": ""}
    for prop in ("og:title", "twitter:title"):
        tag = soup.find("meta", attrs={"property": prop}) or soup.find(
            "meta", attrs={"name": prop}
        )
        if tag and tag.get("content"):
            result["title"] = tag["content"].strip()
            break

    if not result["title"] and soup.title:
        raw = soup.title.string
        if raw:
            result["title"] = raw.strip()

    desc_tag = soup.find("meta", attrs={"property": "og:description"})
    if desc_tag and desc_tag.get("content"):
        result["description"] = desc_tag["content"].strip()

    return result


def extract_content_elements(soup: BeautifulSoup) -> list[Tag]:
    """Find article body content elements."""
    article = soup.find("article")
    if article:
        return [article]

    selectors = [
        ".article-content",
        ".article-body",
        ".article-detail",
        ".content-article",
        "div.article",
        "[class*='article-content']",
        "[class*='articleContent']",
        ".rich_media_content",
        "#article-content",
        "[class*='content']",
    ]
    for sel in selectors:
        el = soup.select_one(sel)
        if el:
            return [el]

    # Readability fallback: largest text block
    candidates = soup.find_all(["div", "section", "article"])
    best: Tag | None = None
    best_len = 0
    for c in candidates:
        text = c.get_text(strip=True)
        if len(text) > best_len:
            best_len = len(text)
            best = c

    if best and best_len > 200:
        return [best]

    return []


def extract_from_embedded_json(soup: BeautifulSoup) -> dict | None:
    """Try to extract article data embedded in <script> tags."""
    patterns = [
        r'window\._pageData\s*=\s*({.*?});',
        r'window\.__INITIAL_STATE__\s*=\s*({.*?});',
        r'"articleInfo":\s*({.*?})[,;}]',
        r'"content"\s*:\s*["\'](.+?)["\']',
    ]
    for script in soup.find_all("script"):
        text = script.string or ""
        for pat in patterns:
            m = re.search(pat, text, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(1))
                except (json.JSONDecodeError, TypeError):
                    pass
    return None


def extract_author_info(soup: BeautifulSoup, url: str) -> dict:
    """
    Extract author name, author link, and publish time from the page.
    Returns dict with keys: author_name, author_link, publish_time, source_url
    """
    result: dict = {
        "author_name": "",
        "author_link": "",
        "publish_time": "",
        "source_url": url,
    }

    # 1) Try JSON-LD
    jsonld = extract_from_jsonld(soup)
    if jsonld:
        # Author
        author_data = jsonld.get("author")
        if isinstance(author_data, dict):
            result["author_name"] = author_data.get("name", "")
            result["author_link"] = author_data.get("url", "")
        elif isinstance(author_data, list) and author_data:
            result["author_name"] = author_data[0].get("name", "")
            result["author_link"] = author_data[0].get("url", "")
        # Publish time
        pub = jsonld.get("datePublished") or jsonld.get("dateCreated")
        if pub:
            result["publish_time"] = pub

    # 2) Meta tags: article:author, article:published_time
    if not result["author_name"]:
        tag = soup.find("meta", attrs={"property": "article:author"})
        if tag and tag.get("content"):
            result["author_name"] = tag["content"].strip()

    if not result["publish_time"]:
        for prop in ("article:published_time", "article:publishedDate"):
            tag = soup.find("meta", attrs={"property": prop})
            if tag and tag.get("content"):
                result["publish_time"] = tag["content"].strip()
                break

    # 3) Look for author link elements in the page
    # Toutiao often has: <a class="author-name" href="...">Author</a>
    if not result["author_link"] or not result["author_name"]:
        author_link_tags = soup.select(
            "a[href*='user/token'], "
            "a[class*='author'], "
            "a[class*='name'], "
            "[class*='author'] a, "
            "[class*='user-info'] a"
        )
        for a_tag in author_link_tags[:5]:
            href = a_tag.get("href", "")
            text = _get_clean_text(a_tag)
            if text and href:
                if not result["author_name"]:
                    result["author_name"] = text
                if not result["author_link"] and ("user/token" in href or "user_id" in href):
                    result["author_link"] = urljoin(url, href)
                break

    # 4) Look for time element
    if not result["publish_time"]:
        time_el = soup.find("time") or soup.select_one("[class*='time'], [class*='date'], [datetime]")
        if time_el:
            dt = time_el.get("datetime") or _get_clean_text(time_el)
            if dt:
                result["publish_time"] = dt

    # 5) Try embedded JSON for author info
    if not result["author_name"] or not result["publish_time"]:
        embedded = extract_from_embedded_json(soup)
        if embedded:
            if not result["author_name"]:
                result["author_name"] = (
                    embedded.get("author_name")
                    or embedded.get("authorName")
                    or embedded.get("source", "")
                )
            if not result["publish_time"]:
                result["publish_time"] = (
                    embedded.get("publish_time")
                    or embedded.get("publishTime")
                    or embedded.get("create_time")
                    or embedded.get("behot_time")
                    or ""
                )

    # Clean up publish time (truncate ISO format to readable)
    pt = result["publish_time"]
    if pt:
        # ISO 8601 → "2026-05-21 20:30"
        m = re.match(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})", pt)
        if m:
            result["publish_time"] = f"{m.group(1)} {m.group(2)}"

    return result


# ---------------------------------------------------------------------------
# HTML → Markdown conversion
# ---------------------------------------------------------------------------
def html_to_markdown(content_els: list[Tag], base_url: str, image_dir: Path) -> tuple[str, set[str]]:
    """Convert HTML elements to Markdown, downloading images to `image_dir`.

    Returns (markdown_text, set_of_downloaded_image_urls).
    """
    md_lines: list[str] = []
    seen_images: dict[str, str] = {}

    for el in content_els:
        _convert_element(el, base_url, image_dir, md_lines, seen_images)

    return "\n\n".join(md_lines), set(seen_images.keys())


def _convert_element(
    el: Tag,
    base_url: str,
    image_dir: Path,
    md_lines: list[str],
    seen_images: dict[str, str],
):
    """Recursively convert an HTML element to Markdown lines."""
    if _should_skip(el):
        return

    tag_name = el.name.lower() if isinstance(el, Tag) and el.name else ""

    if tag_name == "img":
        img_url, alt = _extract_image_url(el, base_url)
        if img_url:
            local_path = _download_image(img_url, image_dir, seen_images)
            md_lines.append(f"![{alt}]({local_path})")
        return

    if tag_name in ("h1", "h2", "h3", "h4", "h5", "h6"):
        level = int(tag_name[1])
        # Shift headings down: body H1→H2, H2→H3, etc. (article title uses H1)
        level = min(level + 1, 6)
        text = _get_clean_text(el)
        if text:
            md_lines.append(f"{'#' * level} {text}")
        return

    if tag_name == "p":
        text = _get_clean_text(el)
        if text:
            md_lines.append(text)
        return

    if tag_name == "blockquote":
        text = _get_clean_text(el)
        if text:
            md_lines.append(f"> {text}")
        return

    if tag_name in ("ul", "ol"):
        for li in el.find_all("li", recursive=False):
            text = _get_clean_text(li)
            if text:
                prefix = "- " if tag_name == "ul" else "1. "
                md_lines.append(f"{prefix}{text}")
        return

    if tag_name == "pre":
        code = _get_clean_text(el)
        if code:
            md_lines.append(f"```\n{code}\n```")
        return

    if tag_name == "figure":
        img = el.find("img")
        if img:
            _convert_element(img, base_url, image_dir, md_lines, seen_images)
        else:
            for child in el.children:
                if isinstance(child, Tag):
                    _convert_element(child, base_url, image_dir, md_lines, seen_images)
        return

    if tag_name in ("div", "section", "span", "article", "a", "main", "body"):
        for child in el.children:
            if isinstance(child, Tag):
                _convert_element(child, base_url, image_dir, md_lines, seen_images)
        return

    text = _get_clean_text(el)
    if text:
        md_lines.append(text)


def _should_skip(el: Tag) -> bool:
    if not isinstance(el, Tag):
        return True
    if not el.name:
        return True
    if el.name.lower() in ("script", "style", "noscript", "meta", "link", "nav", "footer"):
        return True
    style = el.get("style", "")
    cls = " ".join(el.get("class", []))
    if "display:none" in style or "display: none" in style:
        return True
    if "hide" in cls.lower() and "article" not in cls.lower():
        return True
    # Skip common Toutiao UI elements that aren't content
    if any(x in cls.lower() for x in ("comment", "recommend", "related", "advertisement")):
        return True
    return False


def _extract_image_url(el: Tag, base_url: str) -> tuple[str, str]:
    """
    Extract the best image URL and alt text from an <img> element.
    Prefers non-placeholder sources: data-src > data-original > src,
    but skips data: URIs and tiny placeholders.
    """
    alt = el.get("alt", "") or "image"

    # Collect all candidate URLs
    candidates = []
    for attr in ("data-src", "data-original", "src"):
        val = el.get(attr, "")
        if val:
            candidates.append(val)

    # Pick the first non-data-URI candidate
    for val in candidates:
        if not val.startswith("data:"):
            return urljoin(base_url, val), alt

    # All are data URIs — return the last one as-is
    if candidates:
        return candidates[-1], alt

    return "", alt


def _collect_external_images(
    soup: BeautifulSoup, base_url: str, skip_urls: set[str] | None = None
) -> list[tuple[str, str]]:
    """
    Collect article content images that may be outside the main content container.
    Toutiao often places article images in a separate 'flow-container' div
    alongside the <article> tag rather than inside it.

    Images whose URLs already appear in `skip_urls` are excluded to avoid duplicates.
    """
    if skip_urls is None:
        skip_urls = set()
    results: list[tuple[str, str]] = []

    # Look for image containers that likely hold article content images
    img_containers = soup.select(
        ".item-image img, "
        ".article-image img, "
        "[class*='image-list'] img, "
        ".pgc-img img, "
        "figure img, "
        ".flow-container img"
    )

    for img in img_containers:
        # Skip tiny UI icons (share, like, comment, etc.)
        alt = (img.get("alt") or "").lower()
        if any(x in alt for x in ("分享", "评论", "点赞", "收藏", "share", "like", "comment")):
            continue
        # Skip avatar/logo images
        classes = " ".join(img.get("class", []))
        if any(x in classes for x in ("avatar", "logo", "icon")):
            continue

        img_url, img_alt = _extract_image_url(img, base_url)
        if img_url and not img_url.startswith("data:") and img_url not in skip_urls:
            results.append((img_url, img_alt))

    return results


def _get_clean_text(el: Tag) -> str:
    if not el:
        return ""
    text = el.get_text(separator=" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Image downloading
# ---------------------------------------------------------------------------

# Content-Type → file extension mapping
_CONTENT_TYPE_MAP = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
}


def _ext_from_content_type(content_type: str) -> str:
    """Map HTTP Content-Type header to a file extension."""
    if not content_type:
        return ""
    ct = content_type.split(";")[0].strip().lower()
    return _CONTENT_TYPE_MAP.get(ct, "")


def _download_image(url: str, image_dir: Path, seen: dict[str, str]) -> str:
    """Download image and return relative path for the Markdown file.
    seen is a dict mapping URL → assigned filename (for dedup).
    Detects the real file extension from the HTTP Content-Type header."""
    # Skip data: URIs — they are inline and don't need downloading
    if url.startswith("data:"):
        return url

    # Return already-assigned filename for duplicate URLs
    if url in seen:
        return f"{IMAGE_DIR_NAME}/{seen[url]}"

    image_dir.mkdir(parents=True, exist_ok=True)

    # Generate base name without extension; we'll add the real one after download
    base_name = _image_basename()
    tmp_path = image_dir / (base_name + ".tmp")

    real_ext = ""

    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, stream=True)
        resp.raise_for_status()

        # Detect extension from Content-Type header
        real_ext = _ext_from_content_type(resp.headers.get("Content-Type", ""))

        with open(tmp_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

        # Fallback: detect from magic bytes if Content-Type didn't help
        if not real_ext:
            real_ext = _ext_from_magic(tmp_path)

        # Last resort
        if not real_ext:
            real_ext = ".png"

        # Rename to final name with correct extension
        final_name = base_name + real_ext
        final_path = image_dir / final_name
        tmp_path.rename(final_path)

        seen[url] = final_name
        print(f"  ✓ Downloaded: {final_name}")
        return f"{IMAGE_DIR_NAME}/{final_name}"

    except Exception as e:
        print(f"  ✗ Failed to download {url}: {e}")
        # Clean up temp file if it exists
        if tmp_path.exists():
            tmp_path.unlink()
        return url


def _ext_from_magic(filepath: Path) -> str:
    """Detect image type from file magic bytes."""
    magic_to_ext = {
        b"\xff\xd8\xff": ".jpg",       # JPEG
        b"\x89PNG\r\n\x1a\n": ".png",  # PNG
        b"GIF87a": ".gif",             # GIF
        b"GIF89a": ".gif",             # GIF
        b"RIFF": ".webp",              # WEBP (RIFF....WEBP)
        b"BM": ".bmp",                 # BMP
    }
    try:
        with open(filepath, "rb") as f:
            header = f.read(16)
        # WEBP: RIFF????WEBP
        if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
            return ".webp"
        for magic, ext in magic_to_ext.items():
            if header.startswith(magic):
                return ext
    except Exception:
        pass
    return ""


# Image naming counter — increments per image to guarantee unique filenames
_image_counter = 0


def _image_basename() -> str:
    """
    Generate base filename (without extension): image-YYYYMMDDHHmmssSSSxx
    Extension is added later based on actual file content type.
    """
    global _image_counter
    _image_counter += 1
    ts = datetime.now().strftime("%Y%m%d%H%M%S%f")
    return f"image-{ts}{_image_counter:02d}"


def _image_filename(url: str) -> str:
    """
    Legacy stub — returns a placeholder name with guessed extension.
    Real naming is now handled by _download_image which detects Content-Type.
    """
    global _image_counter
    _image_counter += 1
    ts = datetime.now().strftime("%Y%m%d%H%M%S%f")
    parsed = urlparse(url)
    _, ext = os.path.splitext(parsed.path)
    if not ext or len(ext) > 6:
        ext = ".png"
    return f"image-{ts}{_image_counter:02d}{ext}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def scrape(url: str, output_dir: str = ".", use_playwright: bool = True) -> str:
    """
    Main entry point. Fetches a Toutiao article and saves as Markdown.

    Args:
        url: Toutiao article URL
        output_dir: Directory to save the .md and images/
        use_playwright: If True, try Playwright first; fall back to plain requests.

    Returns the path to the generated Markdown file.
    """
    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    image_dir = out / IMAGE_DIR_NAME

    print(f"Fetching: {url}")

    html_text = None

    # Strategy 1: Playwright (headless browser, renders JavaScript)
    if use_playwright:
        html_text = fetch_page_with_playwright(url)

    # Strategy 2: Plain HTTP request (no JS rendering)
    if not html_text:
        print("Falling back to plain HTTP request (JavaScript will not execute)...")
        html_text = fetch_page_plain(url)

    soup = BeautifulSoup(html_text, "html.parser")

    # --- Extract title ---
    title = ""
    jsonld = extract_from_jsonld(soup)
    if jsonld:
        title = jsonld.get("headline", "") or jsonld.get("name", "")

    if not title:
        meta = extract_from_meta(soup)
        title = meta.get("title", "")

    if not title:
        embedded = extract_from_embedded_json(soup)
        if embedded:
            title = (
                embedded.get("title")
                or embedded.get("headline")
                or embedded.get("name", "")
            )

    if not title:
        h1 = soup.find("h1")
        if h1:
            title = _get_clean_text(h1)

    if not title:
        title = f"toutiao_article_{int(time.time())}"

    # Clean up common site suffixes from title
    title = clean_title(title)

    print(f"Title: {title}")

    # --- Extract description ---
    meta = extract_from_meta(soup)
    description = meta.get("description", "")

    # --- Extract body content ---
    content_els = extract_content_elements(soup)
    if not content_els:
        print("Warning: Could not locate article body content.")
        print("Saving page text as fallback...")
        body = soup.body
        if body:
            content_els = [body]
        else:
            content_els = [soup]

    # --- Extract author info ---
    author_info = extract_author_info(soup, url)

    # --- Convert to Markdown ---
    md_body, content_img_urls = html_to_markdown(content_els, url, image_dir)

    # --- Collect images outside content container ---
    # Toutiao places article images in a separate flow container.
    # Skip images already captured in the main content to avoid duplicates.
    seen_for_external: dict[str, str] = {}
    external_imgs = _collect_external_images(soup, url, skip_urls=content_img_urls)
    if external_imgs:
        md_body += "\n\n"
        for img_url, img_alt in external_imgs:
            local_path = _download_image(img_url, image_dir, seen_for_external)
            md_body += f"\n![{img_alt}]({local_path})\n"

    # --- Build final Markdown ---
    md_parts = [f"# {title}\n"]

    # Author info line: "2026-05-21 20:30·[作者名](作者链接)"
    author_meta_parts = []
    if author_info["publish_time"]:
        author_meta_parts.append(author_info["publish_time"])
    if author_info["author_name"]:
        if author_info["author_link"]:
            author_meta_parts.append(f"[{author_info['author_name']}]({author_info['author_link']})")
        else:
            author_meta_parts.append(author_info["author_name"])
    if author_meta_parts:
        md_parts.append("·".join(author_meta_parts) + "\n")

    # Source URL
    md_parts.append(f"> 原文链接: {url}\n")

    if description and description not in md_body:
        md_parts.append(f"> {description}\n")

    md_parts.append(md_body.strip())

    md_content = "\n".join(md_parts)

    # --- Save ---
    filename = sanitize_filename(title) + ".md"
    filepath = out / filename
    filepath.write_text(md_content, encoding="utf-8")

    print(f"\nSaved: {filepath}")
    print(f"Images: {image_dir}")
    return str(filepath)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    article_url = sys.argv[1]
    out_dir = sys.argv[2] if len(sys.argv) > 2 else "."

    # --no-playwright flag to skip browser rendering
    use_pw = "--no-playwright" not in sys.argv

    scrape(article_url, out_dir, use_playwright=use_pw)
