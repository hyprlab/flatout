"""Allowlist HTML sanitizer (stdlib only).

Any HTML the app did not write itself goes through ``sanitize_html()`` before a
template renders it with ``|safe``. Tags outside ``ALLOWED`` are dropped,
attributes are cut down to the listed ones, and script-bearing containers go
with their contents.
"""
import re
from html import escape
from html.parser import HTMLParser

ALLOWED = {
    "p": (), "br": (), "hr": (),
    "a": ("href",),
    "strong": (), "b": (), "em": (), "i": (), "u": (), "s": (), "mark": (), "small": (),
    "blockquote": (), "q": (),
    "ul": (), "ol": (), "li": (),
    "h1": (), "h2": (), "h3": (), "h4": (), "h5": (), "h6": (),
    "pre": (), "code": (),
    "img": ("src", "alt", "title"),
    "figure": (), "figcaption": (),
    "table": (), "thead": (), "tbody": (), "tr": (), "th": (), "td": (),
    "sup": (), "sub": (),
}
VOID = {"br", "hr", "img"}
# Headings in article bodies get demoted so they sit under the reader's own title.
DEMOTE = {"h1": "h3", "h2": "h3", "h5": "h4", "h6": "h4"}
DROP_WITH_CONTENT = {"script", "style", "iframe", "object", "embed", "form", "svg", "video", "audio", "noscript"}

_SAFE_URL = re.compile(r"^(https?:)?//|^https?:|^/|^#|^mailto:", re.I)


def url_is_safe(url: str) -> bool:
    """True for addresses a link or image may point at; javascript: and
    data: (except inside an image's own pipeline) are refused."""
    return bool(_SAFE_URL.match(url.strip()))


class _Sanitizer(HTMLParser):
    def __init__(self, demote: dict, site: bool):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.open_tags = []
        self.skip_depth = 0
        self.demote = demote
        self.site = site

    def handle_starttag(self, tag, attrs):
        if self.skip_depth:
            if tag in DROP_WITH_CONTENT:
                self.skip_depth += 1
            return
        if tag in DROP_WITH_CONTENT:
            self.skip_depth = 1
            return
        if tag not in ALLOWED:
            return
        allowed_attrs = ALLOWED[tag]
        parts = []
        for name, value in attrs:
            if name in allowed_attrs and value:
                if name in ("href", "src") and not _SAFE_URL.match(value.strip()):
                    continue
                parts.append(f' {name}="{escape(value, quote=True)}"')
        if tag == "a":
            href = dict(attrs).get("href") or ""
            # Foreign HTML opens every link elsewhere; the site's own text only
            # sends links that leave the site to a new tab.
            if not self.site or re.match(r"^(https?:)?//", href.strip(), re.I):
                parts.append(' target="_blank" rel="noopener noreferrer"')
        if tag == "img":
            parts.append(' loading="lazy"')
        out_tag = self.demote.get(tag, tag)
        self.out.append(f"<{out_tag}{''.join(parts)}{' /' if tag in VOID else ''}>")
        if tag not in VOID:
            self.open_tags.append(out_tag)

    def handle_endtag(self, tag):
        if self.skip_depth:
            if tag in DROP_WITH_CONTENT:
                self.skip_depth -= 1
            return
        out_tag = self.demote.get(tag, tag)
        if tag in ALLOWED and tag not in VOID and out_tag in self.open_tags:
            # Close anything misnested between the opener and this closer.
            while self.open_tags:
                open_tag = self.open_tags.pop()
                self.out.append(f"</{open_tag}>")
                if open_tag == out_tag:
                    break

    def handle_data(self, data):
        if not self.skip_depth and data:
            self.out.append(escape(data))

    def close(self):
        super().close()
        # Input that ends mid-element (an unfinished table or quote) must not
        # swallow the page that follows it: close what's still open.
        while self.open_tags:
            self.out.append(f"</{self.open_tags.pop()}>")


# The site's own Markdown keeps its heading levels, under the page's one <h1>.
SITE_DEMOTE = {"h1": "h2"}


def sanitize_html(html: str, site: bool = False) -> str:
    """``site=True`` is for the owner's own Markdown: headings keep their
    level (an <h1> becomes <h2>) and only links that leave the site open in a
    new tab."""
    if not html:
        return ""
    s = _Sanitizer(SITE_DEMOTE if site else DEMOTE, site)
    try:
        s.feed(html)
        s.close()
    except Exception:
        return escape(strip_tags(html))
    return "".join(s.out)


# Only block-level tags separate words; inline tags must not, or stylised markup
# such as The Atlantic's drop caps (<p>W<span>hen ...</span>) turns into "W hen".
BLOCK = {
    "p", "br", "hr", "div", "section", "article", "header", "footer", "aside",
    "ul", "ol", "li", "dl", "dt", "dd",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "pre", "figure", "figcaption",
    "table", "thead", "tbody", "tr", "th", "td",
}


class _Stripper(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.chunks = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in DROP_WITH_CONTENT:
            self.skip_depth += 1
            self.chunks.append(" ")
        elif tag in BLOCK:
            self.chunks.append(" ")

    def handle_endtag(self, tag):
        if tag in DROP_WITH_CONTENT:
            if self.skip_depth:
                self.skip_depth -= 1
            self.chunks.append(" ")
        elif tag in BLOCK:
            self.chunks.append(" ")

    def handle_data(self, data):
        if not self.skip_depth:
            self.chunks.append(data)


def strip_tags(html: str) -> str:
    """Plain text from HTML, whitespace collapsed."""
    if not html:
        return ""
    s = _Stripper()
    try:
        s.feed(html)
        s.close()
    except Exception:
        pass
    return re.sub(r"\s+", " ", "".join(s.chunks)).strip()


_IMG_SRC = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"']", re.I)


def first_image(html: str) -> str | None:
    if not html:
        return None
    m = _IMG_SRC.search(html)
    if m and _SAFE_URL.match(m.group(1).strip()):
        return m.group(1).strip()
    return None
