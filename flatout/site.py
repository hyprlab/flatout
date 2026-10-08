"""The site document: storage, publishing, and what templates need to render it.

There are two copies of the document (models.SiteDocument). The editor and the
API change the draft; visitors see the live one; publishing copies the draft
over it and records a revision. A preview renders the draft with the same
templates, so what the owner checks is what visitors get.
"""
from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timezone

import markdown as md_lib
from flask import request
from markupsafe import Markup, escape

from . import site_schema
from .icons import icon_svg
from .models import Media, SiteDocument, SiteRevision, db, get_setting, utcnow
from .sanitize import sanitize_html, url_is_safe

REVISIONS_KEPT = 100


# ———————————————————————————— Storage ————————————————————————————

def _row(name: str) -> SiteDocument:
    row = db.session.get(SiteDocument, name)
    if row is None:
        # A new install starts with the placeholder site, live and draft alike.
        data = json.dumps(site_schema.default_document())
        for n in ("draft", "live"):
            if db.session.get(SiteDocument, n) is None:
                db.session.add(SiteDocument(name=n, data=data, updated_by="Flatout"))
        db.session.commit()
        row = db.session.get(SiteDocument, name)
    return row


def get(name: str = "live") -> dict:
    return json.loads(_row(name).data)


def meta(name: str = "draft") -> dict:
    row = _row(name)
    return {"updated_at": row.updated_at.isoformat() + "Z", "updated_by": row.updated_by}


def media_exists(url: str, kind: str) -> bool:
    """For validation: does an uploaded file with this address exist, of a
    kind that fits the field?"""
    filename = url.removeprefix("/media/")
    item = Media.query.filter_by(filename=filename).first()
    if item is None:
        return False
    return item.kind == "font" if kind == "font" else item.kind == "image"


def clean(doc) -> dict:
    """Validate a whole document; raises site_schema.Invalid."""
    return site_schema.validate(doc, media_ok=media_exists)


def save_draft(doc, who: str) -> dict:
    cleaned = clean(doc)
    row = _row("draft")
    row.data = json.dumps(cleaned)
    row.updated_by = who
    row.updated_at = utcnow()
    db.session.commit()
    return cleaned


def merge_patch(target, patch):
    """RFC 7396 JSON Merge Patch: objects merge, ``null`` deletes, anything
    else (lists included) replaces."""
    if not isinstance(patch, dict):
        return copy.deepcopy(patch)
    out = copy.deepcopy(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if value is None:
            out.pop(key, None)
        else:
            out[key] = merge_patch(out.get(key), value)
    return out


def has_changes() -> bool:
    return get("draft") != get("live")


def publish(who: str, note: str = "") -> dict:
    """Make the draft live. It is validated again first: an image it uses may
    have been deleted since it was saved."""
    doc = clean(get("draft"))
    data = json.dumps(doc)
    live = _row("live")
    live.data = data
    live.updated_by = who
    live.updated_at = utcnow()
    db.session.add(SiteRevision(data=data, published_by=who, note=(note or "")[:200]))
    db.session.flush()
    stale = (SiteRevision.query.order_by(SiteRevision.id.desc())
             .offset(REVISIONS_KEPT).all())
    for old in stale:
        db.session.delete(old)
    db.session.commit()
    return doc


def discard_draft(who: str) -> dict:
    row = _row("draft")
    row.data = _row("live").data
    row.updated_by = who
    row.updated_at = utcnow()
    db.session.commit()
    return json.loads(row.data)


def restore_revision(revision_id: int, who: str) -> dict | None:
    rev = db.session.get(SiteRevision, revision_id)
    if rev is None:
        return None
    doc = json.loads(rev.data)
    # A revision from before the app ID became a repository setting carries
    # it; the setting stays as it is now.
    site_schema.take_moved(doc)
    # Validated on the way in: the revision may name files deleted since.
    return save_draft(doc, who)


def revisions(limit: int = 50) -> list[dict]:
    rows = SiteRevision.query.order_by(SiteRevision.id.desc()).limit(limit).all()
    return [{"id": r.id, "published_at": r.published_at.isoformat() + "Z",
             "published_by": r.published_by, "note": r.note} for r in rows]


def media_in_use() -> set[str]:
    """Every /media/ address the draft, the live site or a kept revision uses,
    so the media library can refuse to delete one that is still needed."""
    used: set[str] = set()
    blobs = [_row("draft").data, _row("live").data]
    blobs += [r.data for r in SiteRevision.query.order_by(SiteRevision.id.desc()).limit(5)]
    for blob in blobs:
        used.update(re.findall(r'"(/media/[^"]+)"', blob))
    return used


# ———————————————————————————— Addresses ————————————————————————————

def base_url() -> str:
    """The site's public address, without a trailing slash. The admin can pin
    it (Repository settings); otherwise it is the address this request came
    in on, which is right whenever the proxy passes the Host header through."""
    pinned = (get_setting("public_url") or "").strip().rstrip("/")
    if pinned:
        return pinned
    try:
        return request.url_root.rstrip("/")
    except RuntimeError:
        return "http://localhost:8000"


# ———————————————————————————— Rendering ————————————————————————————

class Renderer:
    """Everything a site template needs, for one request and one document.

    Templates call ``s.fill(text)`` for plain text and ``s.md(text)`` for
    Markdown; both fill in the placeholders first.
    """

    def __init__(self, doc: dict, preview: bool = False):
        from . import releases

        self.doc = doc
        self.preview = preview
        self.visitor_status = None   # set by public._renderer for signed-in people
        self.base = base_url()
        self.repo = releases.public_info(doc, self.base)
        app = doc["app"]
        pk = self.repo["packages"]
        newest_package = pk["rpm"]["stable"] or pk["deb"]["stable"] or {}
        self.values = {
            "app_name": app["name"],
            "app_tagline": app["tagline"],
            "app_id": self.repo["app_id"],
            # An app published only as packages still has a version to show.
            "version": (self.repo["stable"] or newest_package).get("version", ""),
            "beta_version": (self.repo["beta"] or {}).get("version", ""),
            "site_url": self.base,
            "site_host": re.sub(r"^https?://", "", self.base),
            "repo_url": self.repo["repo_url"],
            "remote_name": self.repo["remote_name"],
            "flatpakref_url": self.repo["flatpakref_url"],
            "beta_flatpakref_url": self.repo["beta_flatpakref_url"],
            "flatpakrepo_url": self.repo["flatpakrepo_url"],
            "package_name": newest_package.get("name", ""),
            "rpm_repo_file_url": pk["rpm"]["repo_file_url"],
            "deb_sources_url": pk["deb"]["sources_url"],
            "source_url": app["source_url"],
            "issues_url": app["issues_url"],
            "year": str(datetime.now(timezone.utc).year),
        }
        # Two passes, so a tagline may itself use {app_name}.
        for key in ("app_tagline",):
            self.values[key] = self._fill(self.values[key])

    def _fill(self, text: str) -> str:
        if not text or "{" not in text:
            return text or ""
        return re.sub(r"\{([a-z_]+)\}", lambda m: self.values.get(m.group(1), m.group(0)), text)

    def fill(self, text: str) -> str:
        return self._fill(text)

    def md(self, text: str) -> Markup:
        if not text:
            return Markup("")
        html = md_lib.markdown(self._fill(text), extensions=["extra", "sane_lists"])
        return Markup(sanitize_html(html, site=True))

    def md_inline(self, text: str) -> Markup:
        """Markdown for a single line: the paragraph wrapper comes off."""
        html = str(self.md(text)).strip()
        if html.startswith("<p>") and html.endswith("</p>") and html.count("<p>") == 1:
            html = html[3:-4]
        return Markup(html)

    def link(self, url: str) -> str:
        """A button or link target, with placeholders filled. #install is the
        install dialog's anchor. The scheme is checked *after* filling: a
        placeholder must not be able to smuggle in a javascript: address."""
        filled = self._fill(url or "").strip()
        return filled if url_is_safe(filled) else ""

    def icon(self, value: str, css_class: str = "") -> Markup:
        if value and value.startswith(("/media/", "https://", "http://")):
            return Markup(f'<img class="ico ico--img {escape(css_class)}" src="{escape(value)}" alt="">')
        return Markup(icon_svg(value, css_class))

    def sections(self) -> list[dict]:
        """The sections to draw, in order. A beta section only appears while a
        beta exists, and the release list only once there is a release."""
        out = []
        for section in self.doc["sections"]:
            if not section["enabled"]:
                continue
            if section["type"] == "beta" and not self.repo["beta"]:
                continue
            if section["type"] == "releases" and not self.repo["history"]:
                continue
            out.append(section)
        return out

    def pages(self, where: str) -> list[dict]:
        return [p for p in self.doc["pages"] if p["published"] and p.get(where)]

    @property
    def icon_url(self) -> str:
        return self.doc["images"]["icon"] or "/static/site/placeholder-icon.svg"

    @property
    def favicon_url(self) -> str:
        return self.doc["images"]["favicon"] or self.icon_url

    def theme_css(self) -> Markup:
        return Markup(theme_css(self.doc["theme"]))


# ———————————————————————————— The theme as CSS ————————————————————————————

FONT_STACKS = {
    "cantarell": '"Cantarell", system-ui, -apple-system, "Segoe UI", sans-serif',
    "inter": '"Inter", system-ui, -apple-system, "Segoe UI", sans-serif',
    "system": 'system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans", sans-serif',
    "serif": 'ui-serif, Georgia, "Noto Serif", "Times New Roman", serif',
    "mono": 'ui-monospace, "SFMono-Regular", "JetBrains Mono", "DejaVu Sans Mono", monospace',
}
FONT_FORMATS = {"woff2": "woff2", "woff": "woff", "ttf": "truetype", "otf": "opentype"}

COLOR_VARS = {
    "accent": "--accent", "accent_text": "--accent-ink", "button": "--button",
    "button_text": "--button-ink", "highlight": "--highlight", "highlight_text": "--highlight-ink",
    "hero": "--hero-bg", "hero_text": "--hero-ink", "band": "--band-bg", "band_text": "--band-ink",
    "background": "--bg", "background_alt": "--bg-tint", "section_alt": "--tint-strong",
    "surface": "--surface", "text": "--ink", "text_muted": "--ink-soft", "border": "--line",
    "code": "--code-bg", "code_text": "--code-fg",
}


def _font(value: str, faces: list[str]) -> str:
    if value in FONT_STACKS:
        return FONT_STACKS[value]
    if value.startswith("/media/"):
        family = "Site font " + re.sub(r"[^A-Za-z0-9]", "", value.rsplit("/", 1)[-1].split(".")[0])[:12]
        ext = value.rsplit(".", 1)[-1].lower()
        faces.append(
            f'@font-face {{ font-family: "{family}"; font-display: swap; font-weight: 100 900; '
            f'src: url("{value}") format("{FONT_FORMATS.get(ext, "woff2")}"); }}'
        )
        return f'"{family}", {FONT_STACKS["system"]}'
    return FONT_STACKS["system"]


def _ink_for(hex_color: str) -> str:
    """White, unless the color is so light that white falls under 2:1 on it
    (WCAG relative luminance); then a near-black. Icons are large graphics,
    and white glyphs on colored tiles is the look the palette is made for."""
    def channel(c: int) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    lum = 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
    return "#ffffff" if 1.05 / (lum + 0.05) >= 2.0 else "#2a2000"


def theme_css(theme: dict) -> str:
    faces: list[str] = []
    body = _font(theme["font_body"], faces)
    heading = _font(theme["font_heading"], faces) if theme["font_heading"] != theme["font_body"] else "var(--font-body)"

    def colors(mode: dict) -> str:
        return " ".join(f"{COLOR_VARS[k]}: {v};" for k, v in mode.items() if k in COLOR_VARS)

    # Each palette color with the ink that reads on it: dark on light colors
    # (the yellow), white on the rest.
    palette = " ".join(f"--c-{k}: {v}; --c-{k}-ink: {_ink_for(v)};" for k, v in theme["palette"].items())
    shared = (
        f"--font-body: {body}; --font-heading: {heading}; "
        f"--font-size: {theme['font_size']}px; --heading-weight: {theme['heading_weight']}; "
        f"--radius: {theme['radius']}px; --maxw: {theme['width']}px; {palette}"
    )
    light, dark = colors(theme["light"]), colors(theme["dark"])
    # data-theme is always set by the head script; the media query covers a
    # visitor with scripts off, who still gets dark when the system is dark.
    return "\n".join(faces + [
        f":root {{ {shared} {light} }}",
        f':root[data-theme="dark"] {{ {dark} }}',
        f'@media (prefers-color-scheme: dark) {{ :root:not([data-theme]) {{ {dark} }} }}'
        if theme["mode"] == "system" else "",
    ])


# ———————————————————————————— Who sees the site ————————————————————————————
# Separate from publishing: Publish puts the draft's changes live; the status
# says whether visitors get the site at all. It is a setting, so a change
# applies at once, and the repository keeps serving whatever it is, so
# installed copies go on updating during maintenance.

STATUSES = ("published", "maintenance", "unpublished")

STATUS_PAGE_DEFAULTS = {
    "maintenance": {
        "title": "Back soon",
        "message": "{app_name} is getting some care and will be back shortly.",
        "until": "",
        "updates_note": True,
    },
    "unpublished": {
        "title": "Coming soon",
        "message": "{app_name} is getting ready. Check back soon.",
        "updates_note": False,
    },
}
STATUS_LIMITS = {"title": 80, "message": 600}


def status() -> str:
    """The site's status. An install that never set one is unpublished until
    its first publish, published after it, so a new install doesn't show the
    placeholder site to the world."""
    stored = get_setting("site_status")
    if stored in STATUSES:
        return stored
    return "published" if SiteRevision.query.first() else "unpublished"


def status_pages() -> dict:
    stored = get_setting("site_status_pages")
    pages = copy.deepcopy(STATUS_PAGE_DEFAULTS)
    if stored:
        try:
            for name, values in json.loads(stored).items():
                if name in pages and isinstance(values, dict):
                    pages[name].update({k: v for k, v in values.items() if k in pages[name]})
        except ValueError:
            pass
    return pages


def status_json() -> dict:
    return {"status": status(), "pages": status_pages()}


def set_status(new_status: str | None = None, pages: dict | None = None) -> dict:
    """Change the status, the text of the two status pages, or both.
    Raises site_schema.Invalid with every problem; nothing is saved then."""
    from .models import set_setting
    errors = []
    if new_status is not None and new_status not in STATUSES:
        errors.append({"path": "$.status", "message": "one of: " + ", ".join(STATUSES)})
    current = status_pages()
    for name, values in (pages or {}).items():
        if name not in current or not isinstance(values, dict):
            errors.append({"path": f"$.pages.{name}", "message": "maintenance or unpublished"})
            continue
        for key, value in values.items():
            path = f"$.pages.{name}.{key}"
            if key not in current[name]:
                errors.append({"path": path, "message": "not a field here"})
            elif key == "updates_note":
                if not isinstance(value, bool):
                    errors.append({"path": path, "message": "expected true or false"})
                else:
                    current[name][key] = value
            elif key == "until":
                value = str(value or "").strip()
                if value and not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:\d{2})$", value):
                    errors.append({"path": path, "message": "a date and time like 2026-10-08T14:00Z, or empty"})
                else:
                    current[name][key] = value
            else:
                value = str(value or "").strip()
                if not value and key == "title":
                    errors.append({"path": path, "message": "a title is needed"})
                elif len(value) > STATUS_LIMITS[key]:
                    errors.append({"path": path, "message": f"at most {STATUS_LIMITS[key]} characters"})
                else:
                    current[name][key] = value
    if errors:
        raise site_schema.Invalid(errors)
    if pages:
        set_setting("site_status_pages", json.dumps(current))
    if new_status is not None:
        set_setting("site_status", new_status)
    return status_json()
