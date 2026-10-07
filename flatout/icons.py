"""The icon set the site editor offers.

Feature cards, buttons and header links pick an icon by name. Each one is the
inner markup of a 24x24 SVG: ``stroke`` icons are outlines drawn with
``currentColor``; ``fill`` icons are solid shapes (the brand marks). A field
that takes an icon also accepts an uploaded image instead, by its URL.
"""

ICONS: dict[str, dict] = {
    # ——— General ———
    "bolt": {"label": "Lightning", "kind": "stroke",
             "svg": '<path d="M13 3 5 13.5h6l-1 7.5 9-10.5h-6L13 3Z"/>'},
    "shield": {"label": "Shield", "kind": "stroke",
               "svg": '<path d="M12 3 5 6v5c0 4.4 3 8.3 7 9.5 4-1.2 7-5.1 7-9.5V6l-7-3Z"/><path d="m8.8 12 2.2 2.2 4.2-4.4"/>'},
    "lock": {"label": "Lock", "kind": "stroke",
             "svg": '<rect x="5" y="10.5" width="14" height="10" rx="2"/><path d="M8.5 10.5V7.5a3.5 3.5 0 0 1 7 0v3"/>'},
    "star": {"label": "Star", "kind": "stroke",
             "svg": '<path d="m12 3.5 2.6 5.3 5.9.9-4.25 4.1 1 5.8L12 16.9l-5.25 2.7 1-5.8L3.5 9.7l5.9-.9L12 3.5Z"/>'},
    "heart": {"label": "Heart", "kind": "stroke",
              "svg": '<path d="M12 20s-7.5-4.6-7.5-10.2A4.2 4.2 0 0 1 12 7.3a4.2 4.2 0 0 1 7.5 2.5C19.5 15.4 12 20 12 20Z"/>'},
    "sparkle": {"label": "Sparkle", "kind": "stroke",
                "svg": '<path d="m11 3 1.8 5.2L18 10l-5.2 1.8L11 17l-1.8-5.2L4 10l5.2-1.8L11 3Z"/><path d="m18.5 15 .7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7.7-1.8Z"/>'},
    "rocket": {"label": "Rocket", "kind": "stroke",
               "svg": '<path d="M9.5 14.5 7 12c1.5-5 5.5-8.5 12.5-8.5 0 7-3.5 11-8.5 12.5l-1.5-1.5Z"/><circle cx="14.5" cy="9.5" r="1.5"/><path d="M7 12H4l2.5-3H10M12 17v3l3-2.5V14M6.5 16.5 4 20"/>'},
    "code": {"label": "Code", "kind": "stroke",
             "svg": '<path d="M8.5 7 3.5 12l5 5M15.5 7l5 5-5 5M13.5 4.5l-3 15"/>'},
    "terminal": {"label": "Terminal", "kind": "stroke",
                 "svg": '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="m7.5 9.5 3 2.5-3 2.5M12.5 15h4"/>'},
    "palette": {"label": "Palette", "kind": "stroke",
                "svg": '<path d="M12 3.5a8.5 8.5 0 1 0 0 17c1.2 0 1.8-.9 1.4-2-.5-1.3.4-2.5 1.8-2.5H17a3.5 3.5 0 0 0 3.5-3.5c0-5-3.8-9-8.5-9Z"/><circle cx="7.5" cy="11.5" r="1"/><circle cx="10" cy="7.5" r="1"/><circle cx="14.5" cy="7.5" r="1"/>'},
    "gear": {"label": "Gear", "kind": "stroke",
             "svg": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z"/>'},
    "search": {"label": "Search", "kind": "stroke",
               "svg": '<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4.5 4.5"/>'},
    "cloud": {"label": "Cloud", "kind": "stroke",
              "svg": '<path d="M7 18.5h10a4 4 0 0 0 .5-8 6 6 0 0 0-11.5 1.5A3.3 3.3 0 0 0 7 18.5Z"/>'},
    "layers": {"label": "Layers", "kind": "stroke",
               "svg": '<path d="M12 4 3.5 8.5 12 13l8.5-4.5L12 4ZM3.5 12.5 12 17l8.5-4.5M3.5 16.5 12 21l8.5-4.5"/>'},
    "keyboard": {"label": "Keyboard", "kind": "stroke",
                 "svg": '<rect x="3" y="6.5" width="18" height="11" rx="2"/><path d="M7 10h.01M10.5 10h.01M14 10h.01M17 10h.01M7.5 14h9"/>'},
    "image": {"label": "Image", "kind": "stroke",
              "svg": '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="m20.5 16-5-5-8.5 8.5"/>'},
    "bell": {"label": "Bell", "kind": "stroke",
             "svg": '<path d="M6 16.5V11a6 6 0 0 1 12 0v5.5l1.5 2h-15l1.5-2ZM10 20.5a2 2 0 0 0 4 0"/>'},
    "mail": {"label": "Mail", "kind": "stroke",
             "svg": '<rect x="3.5" y="5.5" width="17" height="13" rx="2"/><path d="m4 7 8 6 8-6"/>'},
    "chat": {"label": "Chat", "kind": "stroke",
             "svg": '<path d="M5 5.5h14A1.5 1.5 0 0 1 20.5 7v8a1.5 1.5 0 0 1-1.5 1.5h-8L6.5 20v-3.5H5A1.5 1.5 0 0 1 3.5 15V7A1.5 1.5 0 0 1 5 5.5Z"/>'},
    "user": {"label": "Person", "kind": "stroke",
             "svg": '<circle cx="12" cy="8.5" r="3.8"/><path d="M4.5 20c.9-3.6 4-5.5 7.5-5.5s6.6 1.9 7.5 5.5"/>'},
    "users": {"label": "People", "kind": "stroke",
              "svg": '<circle cx="9" cy="8.5" r="3.3"/><path d="M3.5 19c.6-3.2 2.9-5 5.5-5s4.9 1.8 5.5 5M16 5.4a3.2 3.2 0 0 1 0 6.2M17.5 14.2c1.6.6 2.7 2.2 3 4.8"/>'},
    "globe": {"label": "Globe", "kind": "stroke",
              "svg": '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.4 2.4 3.5 5.3 3.5 8.5s-1.1 6.1-3.5 8.5c-2.4-2.4-3.5-5.3-3.5-8.5s1.1-6.1 3.5-8.5Z"/>'},
    "translate": {"label": "Languages", "kind": "stroke",
                  "svg": '<path d="M4 5.5h9M8.5 4v1.5c0 4-2 7-4.5 8.5M6.5 9c1 2 3 3.5 5 4M13 20l3.5-8 3.5 8M14.2 17.5h4.6"/>'},
    "download": {"label": "Download", "kind": "stroke",
                 "svg": '<path d="M12 4v11M7 10.5l5 5 5-5M5 19.5h14"/>'},
    "package": {"label": "Package", "kind": "stroke",
                "svg": '<path d="M12 3 4 7v10l8 4 8-4V7l-8-4ZM4 7l8 4 8-4M12 11v10"/>'},
    "refresh": {"label": "Updates", "kind": "stroke",
                "svg": '<path d="M20 12a8 8 0 1 1-2.3-5.6M20 3.5V7h-3.5"/>'},
    "check": {"label": "Check", "kind": "stroke",
              "svg": '<path d="m5 12.5 4.5 4.5L19 7.5"/>'},
    "clock": {"label": "Clock", "kind": "stroke",
              "svg": '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>'},
    "book": {"label": "Book", "kind": "stroke",
             "svg": '<path d="M4.5 5.5c2.5-1 5-1 7.5.5v13c-2.5-1.5-5-1.5-7.5-.5v-13ZM19.5 5.5c-2.5-1-5-1-7.5.5v13c2.5-1.5 5-1.5 7.5-.5v-13Z"/>'},
    "home": {"label": "Home", "kind": "stroke",
             "svg": '<path d="M4 11 12 4l8 7M6 9.5V20h12V9.5"/>'},
    "link": {"label": "Link", "kind": "stroke",
             "svg": '<path d="M10.5 13.5a4 4 0 0 0 6 .4l2.6-2.6a4 4 0 1 0-5.7-5.7l-1.3 1.3M13.5 10.5a4 4 0 0 0-6-.4l-2.6 2.6a4 4 0 1 0 5.7 5.7l1.3-1.3"/>'},
    "eye": {"label": "Eye", "kind": "stroke",
            "svg": '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z"/><circle cx="12" cy="12" r="3"/>'},
    "monitor": {"label": "Desktop", "kind": "stroke",
                "svg": '<rect x="3.5" y="4.5" width="17" height="11.5" rx="1.5"/><path d="M8.5 20h7M12 16v4"/>'},
    "phone": {"label": "Phone", "kind": "stroke",
              "svg": '<rect x="7" y="3" width="10" height="18" rx="2"/><path d="M11 17.5h2"/>'},
    "tag": {"label": "Tag", "kind": "stroke",
            "svg": '<path d="M3.5 12.5v-8h8l9 9-8 8-9-9Z"/><circle cx="8" cy="9" r="1.3"/>'},
    "folder": {"label": "Folder", "kind": "stroke",
               "svg": '<path d="M3.5 7A1.5 1.5 0 0 1 5 5.5h4.5l2 2.5H19A1.5 1.5 0 0 1 20.5 9.5V18a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 18V7Z"/>'},
    "wrench": {"label": "Wrench", "kind": "stroke",
               "svg": '<path d="M14.7 6.3a4 4 0 0 0 5 5L11 20a2.1 2.1 0 0 1-3-3l8.7-8.7a4 4 0 0 1-2-2Z"/>'},
    "rss": {"label": "Feed", "kind": "stroke",
            "svg": '<path d="M5 11a8 8 0 0 1 8 8M5 5a14 14 0 0 1 14 14"/><circle cx="6" cy="18" r="1.2"/>'},
    "matrix": {"label": "Matrix", "kind": "stroke",
               "svg": '<path d="M6 4H4v16h2M18 4h2v16h-2M8 15v-5M8 11a2 2 0 0 1 4 0v4M12 11a2 2 0 0 1 4 0v4"/>'},
    # ——— Brands, solid ———
    "github": {"label": "GitHub", "kind": "fill",
               "svg": '<path d="M12 2A10 10 0 0 0 8.8 21.5c.5.1.7-.2.7-.5v-1.7c-2.8.6-3.4-1.3-3.4-1.3-.5-1.2-1.1-1.5-1.1-1.5-.9-.6.1-.6.1-.6 1 .1 1.5 1 1.5 1 .9 1.5 2.3 1.1 2.9.8.1-.7.4-1.1.6-1.4-2.2-.3-4.6-1.1-4.6-4.9 0-1.1.4-2 1-2.7-.1-.3-.4-1.3.1-2.7 0 0 .8-.3 2.7 1a9.4 9.4 0 0 1 5 0c1.9-1.3 2.7-1 2.7-1 .5 1.4.2 2.4.1 2.7.6.7 1 1.6 1 2.7 0 3.8-2.4 4.6-4.6 4.9.4.3.7.9.7 1.9v2.8c0 .3.2.6.7.5A10 10 0 0 0 12 2Z"/>'},
    "discord": {"label": "Discord", "kind": "fill",
                "svg": '<path d="M20.317 4.37a19.79 19.79 0 0 0-4.885-1.515.074.074 0 0 0-.079.037c-.21.375-.444.865-.608 1.25a18.27 18.27 0 0 0-5.487 0 12.64 12.64 0 0 0-.617-1.25.077.077 0 0 0-.079-.037A19.736 19.736 0 0 0 3.677 4.37a.07.07 0 0 0-.032.027C.533 9.046-.32 13.58.099 18.058a.082.082 0 0 0 .031.056 19.9 19.9 0 0 0 5.993 3.03.078.078 0 0 0 .084-.028 14.09 14.09 0 0 0 1.226-1.994.076.076 0 0 0-.041-.106 13.107 13.107 0 0 1-1.872-.892.077.077 0 0 1-.008-.128c.126-.094.252-.192.372-.291a.074.074 0 0 1 .077-.01c3.928 1.793 8.18 1.793 12.062 0a.074.074 0 0 1 .078.009c.12.099.246.198.373.292a.077.077 0 0 1-.006.127 12.299 12.299 0 0 1-1.873.892.077.077 0 0 0-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 0 0 .084.028 19.839 19.839 0 0 0 6.002-3.03.077.077 0 0 0 .032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 0 0-.031-.03zM8.02 15.33c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.956-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.956 2.418-2.157 2.418zm7.975 0c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.955-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.946 2.418-2.157 2.418z"/>'},
    "coffee": {"label": "Coffee cup", "kind": "fill",
               "svg": '<path d="M3 8h13v6a5 5 0 0 1-5 5H8a5 5 0 0 1-5-5V8Zm15 0h1.4a3.6 3.6 0 0 1 0 7.2h-2.9A7 7 0 0 0 18 12.4V8Zm0 2.1v2.3c0 .6-.1 1.2-.2 1.8h1.6a1.6 1.6 0 0 0 0-3.2H18Z"/><path d="M6.5 2.2c-.5.9-.5 1.7 0 2.6M10 2.2c-.5.9-.5 1.7 0 2.6M13.5 2.2c-.5.9-.5 1.7 0 2.6" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>'},
}

# Every place an icon is chosen offers "none" first.
ICON_CHOICES = [""] + list(ICONS)


def icon_svg(name: str, css_class: str = "") -> str:
    """The <svg> for a named icon, or "" for an unknown name."""
    icon = ICONS.get(name or "")
    if not icon:
        return ""
    kind = icon["kind"]
    classes = f"ico ico--{kind} {css_class}".strip()
    return f'<svg class="{classes}" viewBox="0 0 24 24" aria-hidden="true">{icon["svg"]}</svg>'
