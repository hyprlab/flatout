"""The shape of the public site, as data.

Everything an owner can change about their homepage lives in one JSON document:
the app's identity, the theme, the header and footer, the install dialog, the
extra pages, and an ordered list of sections. This module declares that
document once. The same declaration:

* fills in a new install's placeholder content (``default_document``),
* checks and cleans every change, from the editor or the API (``validate``),
* drives the editor's forms and the API's JSON Schema (``schema_json``).

Text fields may use placeholders such as ``{app_name}``; they are filled in
when the page renders (``site.placeholders``).
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field as dc_field
from typing import Any

from .icons import ICONS

HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")

# Page slugs live at the site root (/privacy), so they can't shadow a route.
RESERVED_SLUGS = {
    "admin", "api", "login", "logout", "register", "setup", "healthz", "media",
    "flatpak", "repo", "rpm", "deb", "static", "mcp", "preview", "download", "account",
    "settings", "search", "robots.txt", "sitemap.xml", "favicon.ico", "items",
}


# Fields that left the document, and where they went, so a client that still
# sends them is told rather than just refused.
MOVED = {
    "$.app.app_id": "now a repository setting: Repository > App, or PATCH /api/v1/repo/settings",
    "$.app.remote_name": "now a repository setting: Repository > App, or PATCH /api/v1/repo/settings",
}


def take_moved(doc: dict) -> dict:
    """Remove the fields that became repository settings from a document
    (one saved before they moved, or an old revision) and return them."""
    app = doc.get("app") if isinstance(doc, dict) else None
    if not isinstance(app, dict):
        return {}
    return {k: app.pop(k) for k in ("app_id", "remote_name") if k in app}


class Invalid(Exception):
    """Collects every problem in a document, each with the path to it."""

    def __init__(self, errors: list[dict]):
        super().__init__("; ".join(f"{e['path']}: {e['message']}" for e in errors))
        self.errors = errors


# ———————————————————————————— Fields ————————————————————————————

@dataclass
class F:
    """One field. ``type`` is one of: text, textarea, markdown, url, image,
    color, bool, select, int, icon, list, group."""
    type: str
    label: str
    default: Any = None
    help: str = ""
    max: int | None = None          # characters for text; items for list; value for int
    min: int | None = None          # value for int
    choices: list | None = None     # select: [(value, label), ...]
    item: "F | None" = None         # list: the item's field (usually a group)
    fields: dict | None = None      # group: name -> F
    item_label: str = ""            # list: what one item is called in the editor
    title_field: str = ""           # list of groups: the field that names an item

    def to_json(self) -> dict:
        out: dict[str, Any] = {"type": self.type, "label": self.label}
        if self.help:
            out["help"] = self.help
        if self.max is not None:
            out["max"] = self.max
        if self.min is not None:
            out["min"] = self.min
        if self.choices is not None:
            out["choices"] = [{"value": v, "label": lbl} for v, lbl in self.choices]
        if self.type == "list":
            out["item"] = self.item.to_json()
            out["item_label"] = self.item_label or "item"
            if self.title_field:
                out["title_field"] = self.title_field
        if self.type == "group":
            out["fields"] = {k: f.to_json() for k, f in self.fields.items()}
        if self.type not in ("list", "group"):
            out["default"] = self.default
        return out

    def default_value(self):
        if self.type == "group":
            return {k: f.default_value() for k, f in self.fields.items()}
        if self.type == "list":
            return copy.deepcopy(self.default) if self.default is not None else []
        return copy.deepcopy(self.default)


def text(label, default="", max=200, help=""):
    return F("text", label, default, help, max=max)


def textarea(label, default="", max=2000, help=""):
    return F("textarea", label, default, help, max=max)


def markdown(label, default="", max=20000, help="Markdown: **bold**, *italic*, [links](https://…), lists, `code`."):
    return F("markdown", label, default, help, max=max)


def url(label, default="", help=""):
    return F("url", label, default, help, max=2000)


def image(label, default="", help=""):
    return F("image", label, default, help, max=2000)


def color(label, default):
    return F("color", label, default)


def boolean(label, default=False, help=""):
    return F("bool", label, default, help)


def select(label, choices, default, help=""):
    return F("select", label, default, help, choices=choices)


def integer(label, default, min, max, help=""):
    return F("int", label, default, help, min=min, max=max)


def icon(label="Icon", default="", help="A built-in icon, or the URL of an uploaded image."):
    return F("icon", label, default, help, max=2000)


def group(label, fields, help=""):
    return F("group", label, help=help, fields=fields)


def items(label, item, default=None, max=50, item_label="item", title_field="", help=""):
    return F("list", label, default or [], help, max=max, item=item,
             item_label=item_label, title_field=title_field)


# ———————————————————————————— Shared pieces ————————————————————————————

BUTTON_STYLES = [
    ("primary", "Primary"), ("ghost", "Outline"), ("dark", "Dark"),
    ("discord", "Discord blurple"), ("coffee", "Buy me a coffee yellow"),
]

PALETTE = [
    ("purple", "Purple"), ("blue", "Blue"), ("green", "Green"),
    ("yellow", "Yellow"), ("teal", "Teal"), ("pink", "Pink"),
]


def button_item():
    return group("Button", {
        "label": text("Label", max=60),
        "url": url("Link", help="Leave empty to hide the button. #install opens the install dialog."),
        "icon": icon(),
        "style": select("Style", BUTTON_STYLES, "ghost"),
    })


def buttons(label="Buttons", default=None, max=6):
    return items(label, button_item(), default, max=max, item_label="button", title_field="label")


def link_item():
    return group("Link", {
        "label": text("Label", max=60),
        "url": url("Link"),
    })


# ———————————————————————————— Section types ————————————————————————————

@dataclass
class SectionType:
    label: str
    description: str
    fields: dict
    defaults: dict = dc_field(default_factory=dict)


SECTION_TYPES: dict[str, SectionType] = {
    "hero": SectionType(
        "Hero", "The first screen: the icon, the headline, the download button.",
        {
            "announcement": group("Announcement pill", {
                "text": text("Text", max=160, help="Leave empty to hide the pill."),
                "link_label": text("Link label", max=60),
                "link_url": url("Link"),
            }),
            "show_icon": boolean("Show the app icon", True),
            "title": text("Headline", max=160),
            "subtitle": textarea("Subheading", max=600),
            "download_label": text("Download button label", "Download", max=60,
                                   help="Opens the install dialog. Leave empty to hide it."),
            "buttons": buttons("More buttons"),
            "badges": items("Badges", group("Badge", {
                "text": text("Text", max=60),
                "color": select("Dot color", PALETTE, "green"),
            }), max=6, item_label="badge", title_field="text"),
            "screenshot": image("Screenshot", help="Shown under the headline, framed by the hero."),
            "screenshot_dark": image("Screenshot in dark mode", help="Optional. Used when the visitor is in dark mode."),
            "screenshot_alt": text("Screenshot description", max=300, help="Read aloud by screen readers."),
            "fade": boolean("Fade into the page",
                            help="The hero's color blends into the page below it, behind the screenshot "
                                 "if there is one, instead of ending at a straight edge."),
            "fade_length": integer("Fade length (px)", 470, 80, 1200,
                                   help="How far the blend runs on a wide screen. Phones use about half."),
        },
    ),
    "features": SectionType(
        "Feature cards", "A grid of short cards, each with an icon.",
        {
            "eyebrow": text("Eyebrow", max=60, help="A small label above the title."),
            "title": text("Title", max=160),
            "lead": textarea("Introduction", max=600),
            "columns": select("Columns on wide screens", [("2", "Two"), ("3", "Three"), ("4", "Four")], "4"),
            "cards": items("Cards", group("Card", {
                "icon": icon(),
                "color": select("Icon color", PALETTE, "purple"),
                "title": text("Title", max=120),
                "body": markdown("Text", max=1200),
            }), max=24, item_label="card", title_field="title"),
        },
    ),
    "gallery": SectionType(
        "Screenshots", "Framed screenshots with captions. A click opens them full size.",
        {
            "eyebrow": text("Eyebrow", max=60),
            "title": text("Title", max=160),
            "lead": textarea("Introduction", max=600),
            "items": items("Screenshots", group("Screenshot", {
                "image": image("Image"),
                "image_dark": image("Image in dark mode", help="Optional."),
                "alt": text("Description", max=300, help="Read aloud by screen readers."),
                "title": text("Caption title", max=120),
                "caption": textarea("Caption", max=600),
            }), max=12, item_label="screenshot", title_field="title"),
        },
    ),
    "band": SectionType(
        "Highlight band", "A full-width colored band with a checklist and a big number.",
        {
            "eyebrow": text("Eyebrow", max=60),
            "title": text("Title", max=160),
            "lead": textarea("Introduction", max=600),
            "items": items("Checklist", group("Point", {
                "title": text("Title", max=120),
                "body": textarea("Text", max=600),
            }), max=8, item_label="point", title_field="title"),
            "stat_value": text("Big number", max=12, help="Leave empty to hide the card."),
            "stat_label": text("Label under it", max=120),
        },
    ),
    "text": SectionType(
        "Text", "A title and free text, for an about section, a story or a manifesto.",
        {
            "eyebrow": text("Eyebrow", max=60),
            "title": text("Title", max=160),
            "lead": textarea("Introduction", max=600),
            "body": markdown("Text"),
            "icon": icon("Badge icon", help="Optional, shown above the title."),
            "align": select("Alignment", [("center", "Centered"), ("left", "Left")], "center"),
            "tinted": boolean("Tinted background"),
            "buttons": buttons(),
        },
    ),
    "install": SectionType(
        "Install guide", "The download button, what visitors need, and per-distro setup steps.",
        {
            "show_icon": boolean("Show the app icon", True),
            "title": text("Title", "Install {app_name}", max=160),
            "lead": textarea("Introduction", max=600),
            "download_label": text("Download button label", "Download", max=60),
            "requirements_title": text("Requirements title", "What you'll need", max=120),
            "requirements": items("Requirements", group("Requirement", {
                "title": text("Title", max=120),
                "body": markdown("Text", max=1200),
            }), max=10, item_label="requirement", title_field="title"),
            "requirements_note": markdown("Note under the requirements", max=2000),
            "distros_title": text("Distro guide title", "Will it run on my distro?", max=120),
            "distros": items("Distros", group("Distro", {
                "name": text("Name", max=40),
                "status": select("Status", [("ready", "Works out of the box"), ("setup", "Needs a one-time setup")], "ready"),
                "status_text": text("Status label", max=100),
                "body": markdown("Text", max=4000),
                "steps": items("Steps", group("Step", {
                    "text": markdown("Text", max=1200),
                    "command": textarea("Command", max=600, help="Shown in a copyable box."),
                }), max=10, item_label="step", title_field="text"),
                "command": textarea("Final command", max=600, help="Shown after the steps, in a copyable box."),
            }), max=12, item_label="distro", title_field="name"),
            "after": markdown("Text after the guide", max=4000),
            "buttons": buttons(),
        },
    ),
    "beta": SectionType(
        "Beta channel", "How to install the beta. Shown only while a beta release exists.",
        {
            "title": text("Title", "Try the {app_name} beta", max=160),
            "lead": textarea("Introduction", max=600),
            "warning": markdown("Warning box", max=2000),
            "body": markdown("Text", max=4000),
            "button_label": text("Button label", "Download the beta", max=60),
        },
    ),
    "releases": SectionType(
        "What's new", "The newest releases and their notes, straight from the repository.",
        {
            "title": text("Title", "What's new", max=160),
            "lead": textarea("Introduction", max=600),
            "count": integer("How many releases", 3, 1, 20),
            "channel": select("Channel", [("stable", "Stable only"), ("all", "Stable and beta")], "stable"),
        },
    ),
    "faq": SectionType(
        "Questions", "Questions and answers that open on a click.",
        {
            "title": text("Title", "Questions", max=160),
            "lead": textarea("Introduction", max=600),
            "items": items("Questions", group("Question", {
                "question": text("Question", max=200),
                "answer": markdown("Answer", max=4000),
            }), max=40, item_label="question", title_field="question"),
        },
    ),
    "people": SectionType(
        "People", "Credits: contributors, translators, sponsors.",
        {
            "title": text("Title", "Thanks", max=160),
            "lead": textarea("Introduction", max=600),
            "people": items("People", group("Person", {
                "name": text("Name", max=80),
                "url": url("Link"),
                "note": textarea("What they did", max=400),
            }), max=100, item_label="person", title_field="name"),
            "after": markdown("Text after the list", max=2000),
        },
    ),
    "cta": SectionType(
        "Call to action", "A centered closing block with buttons.",
        {
            "show_icon": boolean("Show the app icon", False),
            "icon": icon("Badge icon", help="Shown when the app icon is off."),
            "title": text("Title", max=160),
            "lead": textarea("Introduction", max=600),
            "buttons": buttons(),
            "note": markdown("Small print", max=2000),
            "tinted": boolean("Tinted background"),
        },
    ),
}

SECTION_COMMON = {
    "id": text("Section id", max=48, help="Also the anchor: /#features jumps here."),
    "type": text("Type", max=20),
    "enabled": boolean("Shown", True),
}


# ———————————————————————————— The theme ————————————————————————————

def mode_colors(d: dict) -> F:
    return group("Colors", {
        "accent": color("Accent: links, focus, highlights", d["accent"]),
        "accent_text": color("Text on the accent", d["accent_text"]),
        "button": color("Download button", d["button"]),
        "button_text": color("Text on the download button", d["button_text"]),
        "highlight": color("Checkmarks and the big number", d["highlight"]),
        "highlight_text": color("Text on the highlight", d["highlight_text"]),
        "hero": color("Hero and header background", d["hero"]),
        "hero_text": color("Text on the hero", d["hero_text"]),
        "band": color("Highlight band background", d["band"]),
        "band_text": color("Text on the band", d["band_text"]),
        "background": color("Page background", d["background"]),
        "background_alt": color("Panels and hover", d["background_alt"]),
        "section_alt": color("Tinted sections and footer", d["section_alt"]),
        "surface": color("Cards and dialogs", d["surface"]),
        "text": color("Text", d["text"]),
        "text_muted": color("Secondary text", d["text_muted"]),
        "border": color("Borders", d["border"]),
        "code": color("Command boxes", d["code"]),
        "code_text": color("Text in command boxes", d["code_text"]),
    })


LIGHT = {
    "accent": "#004eff", "accent_text": "#ffffff", "button": "#fec11e", "button_text": "#3a2b00",
    "highlight": "#f6b40a", "highlight_text": "#3a2b00", "hero": "#005bff", "hero_text": "#ffffff",
    "band": "#005bff", "band_text": "#ffffff", "background": "#ffffff", "background_alt": "#eef3ff",
    "section_alt": "#f2f4f6", "surface": "#ffffff", "text": "#241a34", "text_muted": "#5b5170",
    "border": "#e2e9fb", "code": "#241a34", "code_text": "#f4eefc",
}
DARK = {
    "accent": "#7d97ff", "accent_text": "#0d0b1f", "button": "#fec11e", "button_text": "#3a2b00",
    "highlight": "#f6b40a", "highlight_text": "#3a2b00", "hero": "#16309c", "hero_text": "#ffffff",
    "band": "#1b2b8f", "band_text": "#ffffff", "background": "#0d0b1f", "background_alt": "#15123a",
    "section_alt": "#0f0d2a", "surface": "#171440", "text": "#ece8ff", "text_muted": "#b1a9d8",
    "border": "#2b2660", "code": "#07061a", "code_text": "#e8e2ff",
}

FONT_CHOICES = [
    ("cantarell", "Cantarell (GNOME)"), ("inter", "Inter"), ("system", "The visitor's system font"),
    ("serif", "A serif system font"), ("mono", "A monospace system font"),
]

THEME = group("Design", {
    "mode": select("Color mode", [("system", "Follow the visitor's system"), ("light", "Always light"), ("dark", "Always dark")], "system"),
    "show_toggle": boolean("Show the light/dark switch in the header", True),
    "light": mode_colors(LIGHT),
    "dark": mode_colors(DARK),
    "palette": group("Icon palette", {
        "purple": color("Purple", "#004eff"), "blue": color("Blue", "#3584e4"),
        "green": color("Green", "#2ec27e"), "yellow": color("Yellow", "#f6b40a"),
        "teal": color("Teal", "#00b0b5"), "pink": color("Pink", "#e56db1"),
    }, help="The colors feature-card icons and badges choose from."),
    "font_body": F("font", "Body font", "cantarell", "A built-in font, or an uploaded font file.", max=2000, choices=FONT_CHOICES),
    "font_heading": F("font", "Heading font", "cantarell", "A built-in font, or an uploaded font file.", max=2000, choices=FONT_CHOICES),
    "font_size": integer("Base text size (px)", 16, 13, 22, help="Everything on the page scales with it."),
    "heading_weight": select("Heading weight", [("600", "Semibold"), ("700", "Bold"), ("800", "Extra bold"), ("900", "Black")], "800"),
    "radius": integer("Corner rounding (px)", 18, 0, 32),
    "width": integer("Content width (px)", 1120, 880, 1440),
})


# ———————————————————————————— The document ————————————————————————————

DOCUMENT = group("Site", {
    # The Flatpak app ID and the remote name are repository settings, not
    # part of the site: Repository > App, PATCH /api/v1/repo/settings.
    "app": group("Name and links", {
        "name": text("App name", "Your App", max=80),
        "tagline": text("Tagline", "A short line about what it does.", max=160,
                        help="Used in the footer and as the page description."),
        "source_url": url("Source code", help="Used by {source_url}."),
        "issues_url": url("Bug reports", help="Used by {issues_url}."),
        "license": text("License", "", max=80, help="Shown in the footer, such as GPL-3.0-or-later."),
    }),
    "images": group("Images", {
        "icon": image("App icon", help="Square, at least 256 px. SVG or PNG."),
        "favicon": image("Browser tab icon", help="Defaults to the app icon."),
        "wordmark": image("Wordmark", help="Replaces the app name in the header and footer."),
        "wordmark_dark": image("Wordmark in dark mode", help="Optional."),
        "social": image("Social preview", help="1200 x 630, shown when a link to the site is shared."),
    }),
    "seo": group("Search and sharing", {
        "title": text("Page title", "{app_name}: {app_tagline}", max=160),
        "description": textarea("Description", "{app_tagline}", max=300),
    }),
    "nav": group("Header", {
        "links": items("Links", link_item(), [], max=8, item_label="link", title_field="label"),
        "icon_links": items("Icon links", group("Icon link", {
            "icon": icon(),
            "label": text("Label", max=60, help="Read aloud by screen readers."),
            "url": url("Link"),
        }), [], max=6, item_label="icon link", title_field="label"),
        "cta_label": text("Button label", "Get {app_name}", max=60,
                          help="Opens the install dialog. Leave empty to hide it."),
    }),
    "footer": group("Footer", {
        "tagline": text("Tagline", "{app_tagline}", max=200),
        "links": items("Links", link_item(), [], max=12, item_label="link", title_field="label"),
        "show_install_link": boolean("Link to the install dialog", True),
        "show_admin_link": boolean("Link to the admin sign-in", False),
        "made_by": group("Made by", {
            "label": text("Label", "Made by", max=40),
            "name": text("Name", "", max=80, help="Leave the name and logos empty to hide the line."),
            "url": url("Link"),
            "logo": image("Logo"),
            "logo_dark": image("Logo in dark mode"),
        }),
        "buttons": buttons("Buttons", [], max=6),
        "legal": text("Small print", "© {year} {app_name}", max=300),
    }),
    "install": group("Install dialog", {
        "title": text("Title", "Install {app_name}", max=120),
        "show_arch_picker": boolean("Ask Intel/AMD or ARM", True,
                                    help="Only shown when ARM builds exist."),
        "ref_method": boolean("Offer the install file (.flatpakref)", True),
        "bundle_method": boolean("Offer the full .flatpak download", True),
        "repo_method": boolean("Offer adding the repository", True),
        "rpm_method": boolean("Offer the dnf repository", True,
                              help="Shown once an RPM is published under Packages."),
        "deb_method": boolean("Offer the apt repository", True,
                              help="Shown once a Debian package is published under Packages."),
        "files_method": boolean("Offer other downloads", True,
                                help="AppImages, tarballs and other files published under Packages."),
        "flatpak_setup_url": url("Flatpak setup guide", "https://flatpak.org/setup/"),
        "footer": markdown("Small print", "Every option installs the same app and keeps it updated.", max=2000),
    }),
    "sections": items("Sections", group("Section", {}), max=60, item_label="section"),
    "pages": items("Pages", group("Page", {
        "slug": text("Address", max=48, help="The page lives at /<address>, such as /privacy."),
        "title": text("Title", max=160),
        "body": markdown("Text", max=100000),
        "published": boolean("Published", True),
        "in_footer": boolean("Linked in the footer", True),
        "in_nav": boolean("Linked in the header", False),
    }), max=40, item_label="page", title_field="title"),
    "theme": THEME,
})


# ———————————————————————————— Placeholder content ————————————————————————————

def _section(sid: str, stype: str, enabled: bool = True, **values) -> dict:
    out = {"id": sid, "type": stype, "enabled": enabled}
    fields = SECTION_TYPES[stype].fields
    for name, f in fields.items():
        out[name] = copy.deepcopy(values[name]) if name in values else f.default_value()
    unknown = set(values) - set(fields)
    assert not unknown, f"{stype}: {unknown}"
    return out


def _distros() -> list[dict]:
    install = "flatpak install --from {flatpakref_url}"
    return [
        {"name": "Fedora", "status": "ready", "status_text": "Works out of the box",
         "body": "Fedora ships Flatpak with Flathub ready to use. Install {app_name}:",
         "steps": [], "command": install},
        {"name": "Ubuntu", "status": "setup", "status_text": "A one-time setup, then it works",
         "body": "Ubuntu doesn't include Flatpak, so add it once:",
         "steps": [
             {"text": "**Install Flatpak** and the plugin that lets GNOME Software use it:",
              "command": "sudo apt install flatpak gnome-software-plugin-flatpak"},
             {"text": "**Add Flathub**, where the shared runtimes come from:",
              "command": "flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo"},
             {"text": "**Restart**, or sign out and back in.", "command": ""},
         ], "command": install},
        {"name": "Debian", "status": "setup", "status_text": "A one-time setup, then it works",
         "body": "Install Flatpak and add Flathub once:",
         "steps": [
             {"text": "**Install Flatpak:**", "command": "sudo apt install flatpak"},
             {"text": "**Add Flathub:**",
              "command": "flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo"},
             {"text": "**Restart**, or sign out and back in.", "command": ""},
         ], "command": install},
        {"name": "Linux Mint", "status": "ready", "status_text": "Works out of the box",
         "body": "Linux Mint ships Flatpak with Flathub ready to use. Install {app_name}:",
         "steps": [], "command": install},
        {"name": "Arch", "status": "setup", "status_text": "A one-time setup, then it works",
         "body": "Install Flatpak, which adds Flathub on its own:",
         "steps": [{"text": "**Install Flatpak:**", "command": "sudo pacman -S flatpak"}],
         "command": install},
    ]


def default_document() -> dict:
    """A new install's site: placeholder text in every section an app is
    likely to want, ready to be rewritten."""
    doc = DOCUMENT.default_value()
    doc["sections"] = [
        _section("top", "hero",
                 title="A short headline about what {app_name} does",
                 subtitle="One or two sentences that tell a visitor why they want it. "
                          "Edit this text, the colors, the icon and every section below in the admin.",
                 buttons=[{"label": "Source code", "url": "{source_url}", "icon": "github", "style": "ghost"}],
                 badges=[{"text": "Free and open source", "color": "green"},
                         {"text": "Updates through your app store", "color": "blue"},
                         {"text": "Flatpak", "color": "yellow"}]),
        _section("features", "features",
                 title="What it does",
                 cards=[
                     {"icon": "bolt", "color": "purple", "title": "Fast",
                      "body": "Say what makes it quick to start and pleasant to use."},
                     {"icon": "palette", "color": "blue", "title": "Looks at home",
                      "body": "Say how it fits the desktop it runs on."},
                     {"icon": "shield", "color": "green", "title": "Private",
                      "body": "Say what it does, and doesn't do, with the user's data."},
                     {"icon": "refresh", "color": "yellow", "title": "Always up to date",
                      "body": "Updates arrive through Flatpak, like any other app."},
                 ]),
        _section("screenshots", "gallery", enabled=False, title="A closer look"),
        _section("why", "band",
                 eyebrow="Why {app_name}", title="A reason to choose it.",
                 lead="Explain in a sentence or two what sets it apart.",
                 items=[{"title": "First reason", "body": "One line about it."},
                        {"title": "Second reason", "body": "One line about it."},
                        {"title": "Third reason", "body": "One line about it."}],
                 stat_value="0", stat_label="a number worth bragging about"),
        _section("about", "text", enabled=False, title="About {app_name}",
                 body="Tell the story behind the app, or what it stands for."),
        _section("install", "install",
                 lead="Install it from this site's Flatpak repository. Updates arrive "
                      "through your app store or `flatpak update`.",
                 requirements=[
                     {"title": "64-bit Linux.", "body": "Intel/AMD (`x86_64`), and ARM (`aarch64`) where a build is published."},
                     {"title": "Flatpak, with Flathub.", "body": "{app_name} installs from {site_host}, and its shared runtime comes from Flathub the first time."},
                 ],
                 distros=_distros()),
        _section("beta", "beta",
                 lead="The beta gets new features first, before they reach the stable release.",
                 warning="**Beta builds can be unstable.** Features may change or break between versions.",
                 body="The beta installs as the `beta` branch of the same app. Later betas arrive "
                      "through `flatpak update` like any other app. Install it from a terminal with:\n\n"
                      "`flatpak install --from {beta_flatpakref_url}`"),
        _section("whats-new", "releases"),
        _section("questions", "faq", enabled=False,
                 items=[{"question": "Is it free?", "answer": "Say how it is licensed."}]),
        _section("thanks", "people", enabled=False),
        _section("contact", "cta", icon="chat", title="Questions or bugs?",
                 lead="Tell us what went wrong, or what you would like it to do.",
                 buttons=[{"label": "Report a bug", "url": "{issues_url}", "icon": "github", "style": "dark"}]),
    ]
    doc["pages"] = [
        {"slug": "privacy", "title": "Privacy policy", "published": False, "in_footer": True, "in_nav": False,
         "body": "Say what {app_name} and this site collect, and what they don't."},
        {"slug": "terms", "title": "Terms of use", "published": False, "in_footer": True, "in_nav": False,
         "body": "The terms that apply to {app_name} and this site."},
    ]
    return doc


def new_section(stype: str, existing_ids: set[str]) -> dict:
    """An empty section of a type, with an id not yet used on the page."""
    base = stype if stype != "cta" else "call-to-action"
    sid, n = base, 2
    while sid in existing_ids:
        sid, n = f"{base}-{n}", n + 1
    return _section(sid, stype)


# ———————————————————————————— Validation ————————————————————————————

URL_RE = re.compile(r"^(https?://[^\s<>\"]+|mailto:[^\s<>\"]+|/[^\s<>\"]*|#[A-Za-z0-9_-]*|\{[a-z_]+\}[^\s<>\"]*)$")


def _check(value, f: F, path: str, errors: list, media_ok) -> Any:
    def err(message):
        errors.append({"path": path, "message": message})
        return f.default_value()

    if f.type == "group":
        if not isinstance(value, dict):
            return err("expected an object")
        out = {}
        for name, sub in f.fields.items():
            out[name] = _check(value[name], sub, f"{path}.{name}", errors, media_ok) if name in value else sub.default_value()
        for extra in set(value) - set(f.fields):
            moved = MOVED.get(f"{path}.{extra}")
            errors.append({"path": f"{path}.{extra}", "message": moved or "not a field here"})
        return out
    if f.type == "list":
        if not isinstance(value, list):
            return err("expected a list")
        if f.max is not None and len(value) > f.max:
            return err(f"at most {f.max} {f.item_label}s")
        return [_check(v, f.item, f"{path}[{i}]", errors, media_ok) for i, v in enumerate(value)]
    if f.type == "bool":
        if not isinstance(value, bool):
            return err("expected true or false")
        return value
    if f.type == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            if isinstance(value, str) and value.strip().lstrip("-").isdigit():
                value = int(value)
            else:
                return err("expected a whole number")
        if not f.min <= value <= f.max:
            return err(f"must be between {f.min} and {f.max}")
        return value
    if not isinstance(value, str):
        return err("expected text")
    value = value.replace("\r\n", "\n")
    if f.type in ("text", "url", "image", "color", "icon", "select", "font"):
        value = value.strip()
    if f.max is not None and len(value) > f.max:
        return err(f"at most {f.max} characters")
    if f.type == "text" and "\n" in value:
        return err("one line only")
    if f.type == "color" and not HEX_RE.match(value):
        return err("a color like #1a2b3c")
    if f.type == "select" and value not in [c[0] for c in f.choices]:
        return err("one of: " + ", ".join(c[0] for c in f.choices))
    if f.type == "url" and value and not URL_RE.match(value):
        return err("a link starting with https://, mailto:, / or #")
    if f.type in ("image", "icon", "font") and value:
        if f.type == "icon" and value in ICONS:
            return value
        if f.type == "font" and value in [c[0] for c in f.choices]:
            return value
        if value.startswith("/media/"):
            if not media_ok(value, f.type):
                return err("no uploaded file has this address")
            return value
        if f.type == "image" and URL_RE.match(value) and value.startswith(("https://", "http://", "/static/")):
            return value
        what = {"icon": "an icon name or an uploaded image", "font": "a built-in font or an uploaded font",
                "image": "an uploaded image (/media/…) or an https:// address"}[f.type]
        return err(f"expected {what}")
    return value


def _check_section(value, path: str, errors: list, media_ok) -> dict | None:
    if not isinstance(value, dict):
        errors.append({"path": path, "message": "expected an object"})
        return None
    stype = value.get("type")
    if stype not in SECTION_TYPES:
        errors.append({"path": f"{path}.type", "message": "one of: " + ", ".join(SECTION_TYPES)})
        return None
    sid = value.get("id")
    if not isinstance(sid, str) or not SLUG_RE.match(sid):
        errors.append({"path": f"{path}.id", "message": "lower-case letters, digits and dashes"})
        return None
    enabled = value.get("enabled", True)
    if not isinstance(enabled, bool):
        errors.append({"path": f"{path}.enabled", "message": "expected true or false"})
        enabled = True
    fields = SECTION_TYPES[stype].fields
    out = {"id": sid, "type": stype, "enabled": enabled}
    for name, f in fields.items():
        out[name] = _check(value[name], f, f"{path}.{name}", errors, media_ok) if name in value else f.default_value()
    for extra in set(value) - set(fields) - {"id", "type", "enabled"}:
        errors.append({"path": f"{path}.{extra}", "message": f"not a field of a {stype} section"})
    return out


def validate(doc: Any, media_ok=lambda url, kind: True) -> dict:
    """A cleaned copy of ``doc``, or ``Invalid`` listing every problem.

    Missing fields take their defaults, so a client may send only what it
    knows about. ``media_ok(url, kind)`` says whether an uploaded file exists.
    """
    errors: list[dict] = []
    if not isinstance(doc, dict):
        raise Invalid([{"path": "$", "message": "expected an object"}])
    body = {k: v for k, v in doc.items() if k not in ("sections", "pages")}
    out = _check(body, DOCUMENT, "$", errors, media_ok)
    out.pop("sections", None)
    out.pop("pages", None)
    # The two lists the generic walk can't describe: sections differ by type,
    # and both need ids that are unique.
    sections = doc.get("sections", default_document()["sections"])
    if not isinstance(sections, list):
        errors.append({"path": "$.sections", "message": "expected a list"})
        sections = []
    if len(sections) > DOCUMENT.fields["sections"].max:
        errors.append({"path": "$.sections", "message": "too many sections"})
        sections = []
    out["sections"], seen = [], set()
    for i, s in enumerate(sections):
        clean = _check_section(s, f"$.sections[{i}]", errors, media_ok)
        if clean is None:
            continue
        if clean["id"] in seen:
            errors.append({"path": f"$.sections[{i}].id", "message": f"\"{clean['id']}\" is used twice"})
        seen.add(clean["id"])
        out["sections"].append(clean)
    pages = _check(doc.get("pages", default_document()["pages"]), DOCUMENT.fields["pages"], "$.pages", errors, media_ok)
    slugs = set()
    for i, page in enumerate(pages):
        slug = page["slug"]
        if not SLUG_RE.match(slug):
            errors.append({"path": f"$.pages[{i}].slug", "message": "lower-case letters, digits and dashes"})
        elif slug in RESERVED_SLUGS:
            errors.append({"path": f"$.pages[{i}].slug", "message": f"/{slug} is used by Flatout itself"})
        elif slug in slugs:
            errors.append({"path": f"$.pages[{i}].slug", "message": f"/{slug} is used twice"})
        slugs.add(slug)
    out["pages"] = pages
    if errors:
        raise Invalid(errors)
    return out


# ———————————————————————————— For the editor and the API ————————————————————————————

def schema_json() -> dict:
    """The whole declaration as JSON: what the editor builds its forms from,
    and what an API client reads to learn the document's shape."""
    from .icons import ICONS as _icons
    return {
        "document": DOCUMENT.to_json(),
        "section_common": {k: f.to_json() for k, f in SECTION_COMMON.items()},
        "section_types": {
            k: {"label": t.label, "description": t.description,
                "fields": {n: f.to_json() for n, f in t.fields.items()}}
            for k, t in SECTION_TYPES.items()
        },
        "icons": {k: {"label": v["label"], "kind": v["kind"], "svg": v["svg"]} for k, v in _icons.items()},
        "placeholders": PLACEHOLDER_HELP,
        "reserved_slugs": sorted(RESERVED_SLUGS),
    }


PLACEHOLDER_HELP = {
    "{app_name}": "The app's name",
    "{app_tagline}": "The tagline",
    "{app_id}": "The Flatpak app ID",
    "{version}": "The newest stable version",
    "{beta_version}": "The newest beta version",
    "{site_url}": "This site's address",
    "{site_host}": "This site's host name",
    "{repo_url}": "The Flatpak repository's address",
    "{remote_name}": "The name flatpak remote-add uses",
    "{flatpakref_url}": "The stable install file",
    "{beta_flatpakref_url}": "The beta install file",
    "{flatpakrepo_url}": "The repository file",
    "{package_name}": "The package dnf and apt install",
    "{rpm_repo_file_url}": "The .repo file that adds the dnf repository",
    "{deb_sources_url}": "The .sources file that adds the apt repository",
    "{source_url}": "The source code link",
    "{issues_url}": "The bug report link",
    "{year}": "The current year",
}
