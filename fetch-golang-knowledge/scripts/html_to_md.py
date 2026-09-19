#!/usr/bin/env python3
"""
Convert HTML pages to clean markdown.

Handles:
- Main content extraction (article > main > .content > body)
- Headings, paragraphs, lists, blockquotes
- Code blocks with language hints
- Tables (simple and nested)
- Images (resolves relative URLs against base URL)
- Inline formatting (bold, italic, links, inline code)
"""

import sys
import os
import re
import argparse
from html.parser import HTMLParser
from urllib.parse import urljoin


def try_import_bs4():
    """Try to import BeautifulSoup, fall back to built-in HTMLParser."""
    try:
        from bs4 import BeautifulSoup, NavigableString, Tag
        return True, (BeautifulSoup, NavigableString, Tag)
    except ImportError:
        return False, None


HAS_BS4, bs4 = try_import_bs4()


def slugify(text):
    """Convert text to a safe filename slug."""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[-\s]+', '-', text)
    return text.strip('-')


class MarkdownConverter:
    """Convert HTML to Markdown using BeautifulSoup or raw HTMLParser."""

    def __init__(self, base_url="", extract_main=True):
        self.base_url = base_url
        self.extract_main = extract_main
        self.output = []
        self.list_depth = 0
        self.in_code_block = False
        self.in_table = False

    def resolve_url(self, url):
        """Resolve relative URLs against base_url."""
        if not url or url.startswith('data:'):
            return url
        if self.base_url and not url.startswith(('http://', 'https://', '//')):
            return urljoin(self.base_url, url)
        return url

    def convert_with_bs4(self, html_content):
        """Convert using BeautifulSoup (preferred)."""
        BeautifulSoup, NavigableString, Tag = bs4
        soup = BeautifulSoup(html_content, 'lxml' if self._has_lxml() else 'html.parser')

        # Extract main content
        if self.extract_main:
            content = self._find_main_content(soup)
        else:
            content = soup.body if soup.body else soup

        if content is None:
            content = soup.body if soup.body else soup

        self._process_element(content)
        return self._clean_output()

    def _has_lxml(self):
        try:
            import lxml  # noqa
            return True
        except ImportError:
            return False

    def _find_main_content(self, soup):
        """Find the main content container."""
        selectors = [
            'article',
            'main',
            '[role="main"]',
            '.post-content',
            '.article-content',
            '.content',
            '.entry-content',
            '.page-content',
            '#content',
            '.markdown-body',
            '.theme-default-content',   # VuePress
            '.theme-hope-content',       # VuePress Theme Hope
        ]
        for selector in selectors:
            if selector.startswith('.') or selector.startswith('#'):
                el = soup.select_one(selector)
            else:
                el = soup.find(selector)
            if el:
                return el
        return soup.body if soup.body else soup

    def _process_element(self, element, tag=None):
        """Process a BeautifulSoup element recursively."""
        BeautifulSoup, NavigableString, Tag = bs4

        if isinstance(element, NavigableString):
            text = str(element).strip()
            if text:
                self.output.append(self._escape_md(text))
            return

        if not isinstance(element, Tag):
            return

        tag_name = element.name.lower() if element.name else ''

        handlers = {
            'h1': lambda e: self._heading(e, 1),
            'h2': lambda e: self._heading(e, 2),
            'h3': lambda e: self._heading(e, 3),
            'h4': lambda e: self._heading(e, 4),
            'h5': lambda e: self._heading(e, 5),
            'h6': lambda e: self._heading(e, 6),
            'p': self._paragraph,
            'br': lambda e: self.output.append('\n'),
            'hr': lambda e: self.output.append('\n---\n'),
            'ul': self._list,
            'ol': self._list,
            'li': self._list_item,
            'blockquote': self._blockquote,
            'pre': self._code_block,
            'code': self._inline_code,
            'img': self._image,
            'a': self._link,
            'strong': self._inline_tag,
            'b': self._inline_tag,
            'em': self._inline_tag,
            'i': self._inline_tag,
            'del': lambda e: self._wrap_inline(e, '~~'),
            'table': self._table,
            'thead': self._process_children,
            'tbody': self._process_children,
            'tr': self._table_row,
            'th': self._table_cell,
            'td': self._table_cell,
            'figure': self._figure,
            'figcaption': self._figcaption,
            'div': self._process_children,
            'section': self._process_children,
            'span': self._process_children,
            'header': self._process_children,
            'footer': self._process_children,
            'nav': self._skip,
            'script': self._skip,
            'style': self._skip,
            'noscript': self._skip,
        }

        handler = handlers.get(tag_name, self._process_children)
        handler(element)

    def _process_children(self, element):
        """Process all children of an element."""
        BeautifulSoup, NavigableString, Tag = bs4
        for child in element.children:
            if isinstance(child, Tag) or (isinstance(child, NavigableString) and str(child).strip()):
                self._process_element(child)

    def _skip(self, element):
        """Skip this element and its children."""
        pass

    def _get_text(self, element):
        """Get all text content from an element."""
        BeautifulSoup, NavigableString, Tag = bs4
        parts = []
        for child in element.children:
            if isinstance(child, NavigableString):
                text = str(child).strip()
                if text:
                    parts.append(text)
            elif isinstance(child, Tag):
                parts.append(self._get_text(child))
        return ' '.join(parts)

    def _escape_md(self, text):
        """Escape special markdown characters in text."""
        # Don't escape too aggressively — only chars that would break markdown structure
        return text

    def _heading(self, element, level):
        prefix = '#' * level
        text = self._get_text(element).strip()
        if text:
            self.output.append(f'\n{prefix} {text}\n')

    def _paragraph(self, element):
        # Check if paragraph contains images — if so, process children
        # individually so img tags aren't lost by _get_text
        if hasattr(element, 'find') and element.find('img'):
            self._process_children(element)
            self.output.append('')
            return
        text = self._get_text(element).strip()
        if text:
            self.output.append(f'\n{text}\n')

    def _list(self, element):
        self._process_children(element)
        self.output.append('')

    def _list_item(self, element):
        text = self._get_text(element).strip()
        if text:
            self.output.append(f'- {text}')

    def _blockquote(self, element):
        text = self._get_text(element).strip()
        if text:
            quoted = '\n'.join(f'> {line}' for line in text.split('\n'))
            self.output.append(f'\n{quoted}\n')

    def _image(self, element):
        src = element.get('src', '')
        alt = element.get('alt', '')
        src = self.resolve_url(src)
        self.output.append(f'\n![{alt}]({src})\n')

    def _link(self, element):
        href = element.get('href', '')
        text = self._get_text(element).strip()
        href = self.resolve_url(href)
        if text:
            self.output.append(f'[{text}]({href})')

    def _inline_tag(self, element):
        tag_name = element.name.lower() if element.name else ''
        markers = {'strong': '**', 'b': '**', 'em': '*', 'i': '*'}
        marker = markers.get(tag_name, '')
        text = self._get_text(element).strip()
        if text and marker:
            self.output.append(f'{marker}{text}{marker}')

    def _wrap_inline(self, element, wrapper):
        text = self._get_text(element).strip()
        if text:
            self.output.append(f'{wrapper}{text}{wrapper}')

    def _code_block(self, element):
        code_tag = element.find('code') if hasattr(element, 'find') else None

        # Get language from class
        language = ''
        if code_tag and code_tag.get('class'):
            for cls in code_tag.get('class'):
                if cls.startswith('language-') or cls.startswith('lang-'):
                    language = cls.replace('language-', '').replace('lang-', '')
                    break

        # Get code text
        if code_tag:
            code_text = code_tag.get_text()
        else:
            code_text = element.get_text()

        self.output.append(f'\n```{language}\n{code_text.strip()}\n```\n')

    def _inline_code(self, element):
        if element.parent and hasattr(element.parent, 'name') and element.parent.name == 'pre':
            return  # handled by _code_block
        text = element.get_text().strip()
        if text:
            self.output.append(f'`{text}`')

    def _figure(self, element):
        """Handle figure elements — extract the image."""
        if hasattr(element, 'find'):
            img = element.find('img')
            if img:
                self._image(img)
            else:
                self._process_children(element)

    def _figcaption(self, element):
        """Handle figcaption — output as italic text."""
        text = self._get_text(element).strip()
        if text:
            self.output.append(f'\n*{text}*\n')

    def _table(self, element):
        self.output.append('')
        self._process_children(element)
        self.output.append('')

    def _table_row(self, element):
        self._process_children(element)
        self.output.append('|\n')

        # Add header separator after thead
        parent = element.parent
        if parent and hasattr(parent, 'name'):
            if parent.name == 'thead':
                # Count columns from this row
                cells = element.find_all(['th', 'td']) if hasattr(element, 'find_all') else []
                col_count = len(cells) if cells else 1
                self.output.append('| ' + ' | '.join(['---'] * col_count) + ' |\n')

    def _table_cell(self, element):
        text = self._get_text(element).strip().replace('\n', ' ')
        self.output.append(f'| {text} ')

    def _clean_output(self):
        """Clean up the output markdown."""
        text = ''.join(self.output)

        # Remove excessive blank lines
        text = re.sub(r'\n{3,}', '\n\n', text)

        # Remove leading/trailing whitespace
        text = text.strip()

        return text

    def convert_with_htmlparser(self, html_content):
        """Fallback: simple conversion using built-in HTMLParser."""
        # Strip scripts and styles
        html = re.sub(r'<(script|style|noscript)[^>]*>.*?</\1>', '', html_content, flags=re.DOTALL | re.I)

        # Find title
        title_match = re.search(r'<title[^>]*>(.*?)</title>', html, re.I | re.DOTALL)
        title = title_match.group(1).strip() if title_match else ''

        # Extract body-like content
        body_match = re.search(r'<body[^>]*>(.*?)</body>', html, re.I | re.DOTALL)
        body = body_match.group(1) if body_match else html

        # Try to find main content area
        main_selectors = [
            r'<article[^>]*>(.*?)</article>',
            r'<main[^>]*>(.*?)</main>',
            r'<div[^>]*class="[^"]*theme-hope-content[^"]*"[^>]*>(.*?)</div>',
            r'<div[^>]*class="[^"]*content[^"]*"[^>]*>(.*?)</div>',
        ]

        main_content = None
        for pattern in main_selectors:
            match = re.search(pattern, body, re.I | re.DOTALL)
            if match:
                main_content = match.group(1)
                break

        if main_content is None:
            main_content = body

        # Basic HTML-to-text conversion
        text = main_content

        # Headings
        for i in range(6, 0, -1):
            text = re.sub(
                f'<h{i}[^>]*>(.*?)</h{i}>',
                lambda m, lvl=i: f'\n{"#" * lvl} {re.sub(r"<[^>]+>", "", m.group(1)).strip()}\n',
                text,
                flags=re.I | re.DOTALL
            )

        # Code blocks
        def code_block_replacer(m):
            lang = ''
            code_tag = re.search(r'<code[^>]*class="[^"]*language-(\w+)[^"]*"[^>]*>(.*?)</code>', m.group(1), re.I | re.DOTALL)
            if code_tag:
                lang = code_tag.group(1)
                code = code_tag.group(2)
            else:
                code = re.sub(r'<[^>]+>', '', m.group(1))
            code = code.strip()
            return f'\n```{lang}\n{code}\n```\n'

        text = re.sub(r'<pre[^>]*>(.*?)</pre>', code_block_replacer, text, flags=re.I | re.DOTALL)

        # Images
        def img_replacer(m):
            src_match = re.search(r'src="([^"]+)"', m.group(0), re.I)
            alt_match = re.search(r'alt="([^"]*)"', m.group(0), re.I)
            src = self.resolve_url(src_match.group(1)) if src_match else ''
            alt = alt_match.group(1) if alt_match else ''
            return f'\n![{alt}]({src})\n'

        text = re.sub(r'<img[^>]+>', img_replacer, text, flags=re.I)

        # Links
        def link_replacer(m):
            href_match = re.search(r'href="([^"]+)"', m.group(0), re.I)
            link_text = re.sub(r'<[^>]+>', '', m.group(1)).strip()
            href = self.resolve_url(href_match.group(1)) if href_match else ''
            return f'[{link_text}]({href})'

        text = re.sub(r'<a[^>]*>(.*?)</a>', link_replacer, text, flags=re.I | re.DOTALL)

        # Bold and italic
        text = re.sub(r'<(strong|b)[^>]*>(.*?)</\1>', r'**\2**', text, flags=re.I | re.DOTALL)
        text = re.sub(r'<(em|i)[^>]*>(.*?)</\1>', r'*\2*', text, flags=re.I | re.DOTALL)

        # Inline code
        text = re.sub(r'<code[^>]*>(.*?)</code>', r'`\1`', text, flags=re.I | re.DOTALL)

        # List items
        text = re.sub(r'<li[^>]*>(.*?)</li>', r'- \1', text, flags=re.I | re.DOTALL)

        # Paragraphs
        text = re.sub(r'<p[^>]*>(.*?)</p>', r'\n\1\n', text, flags=re.I | re.DOTALL)

        # Line breaks
        text = re.sub(r'<br\s*/?>', '\n', text, flags=re.I)

        # Horizontal rules
        text = re.sub(r'<hr\s*/?>', '\n---\n', text, flags=re.I)

        # Remove remaining HTML tags
        text = re.sub(r'<[^>]+>', '', text)

        # Decode HTML entities
        import html as html_mod
        text = html_mod.unescape(text)

        # Clean up
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = text.strip()

        if title:
            text = f'# {title}\n\n{text}'

        return text


def main():
    parser = argparse.ArgumentParser(description='Convert HTML to Markdown')
    parser.add_argument('--input', '-i', required=True, help='Input HTML file')
    parser.add_argument('--output', '-o', required=True, help='Output markdown file')
    parser.add_argument('--title', '-t', default='', help='Page title (used for H1 if not in content)')
    parser.add_argument('--base-url', '-b', default='', help='Base URL for resolving relative links/images')
    parser.add_argument('--source-url', '-s', default='', help='Original page URL (inserted as a link after the title)')
    parser.add_argument('--no-extract', action='store_true', help='Do not try to extract main content; convert entire body')
    args = parser.parse_args()

    with open(args.input, 'r', encoding='utf-8') as f:
        html_content = f.read()

    converter = MarkdownConverter(base_url=args.base_url, extract_main=not args.no_extract)

    if HAS_BS4:
        md = converter.convert_with_bs4(html_content)
    else:
        md = converter.convert_with_htmlparser(html_content)

    # Ensure there's an h1 at the top — use the title if the first heading isn't h1
    if args.title:
        first_heading = re.search(r'^# (.+)$', md, re.MULTILINE)
        if not first_heading or not first_heading.group(0).startswith('# '):
            md = f'# {args.title}\n\n{md}'
        elif first_heading.group(1).strip() != args.title.strip():
            # Replace first heading with the page title
            md = md.replace(first_heading.group(0), f'# {args.title}', 1)

    # Insert source URL link after the title line
    if args.source_url:
        # Find the first H1 line and insert the source link after it
        lines = md.split('\n')
        new_lines = []
        inserted = False
        for i, line in enumerate(lines):
            new_lines.append(line)
            if not inserted and line.startswith('# ') and '原文链接' not in line:
                # Add a blank line then the source link
                new_lines.append('')
                new_lines.append(f'> 原文链接：{args.source_url}')
                inserted = True
        md = '\n'.join(new_lines)

    # Write output
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, 'w', encoding='utf-8') as f:
        f.write(md)

    print(f'Converted: {args.input} → {args.output}')
    print(f'Markdown length: {len(md)} characters')


if __name__ == '__main__':
    main()
