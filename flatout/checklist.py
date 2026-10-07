"""The Overview's getting-started list.

Each step is detected from the instance (``auto``) and can be ticked or
unticked by hand, which wins (``override``). Clicking back to what was
detected hands the step back to detection. The list can be hidden by anyone
who would rather set things up their own way. One list for the instance,
kept in the settings table.
"""
from __future__ import annotations

import json

from flask import url_for

from . import repo, site, site_schema
from .models import Release, get_setting, set_setting

STEPS = ("name", "theme", "homepage", "key", "release", "publish")


def _stored() -> dict:
    try:
        data = json.loads(get_setting("setup_checklist") or "{}")
    except ValueError:
        data = {}
    overrides = {k: v for k, v in (data.get("overrides") or {}).items() if k in STEPS and isinstance(v, bool)}
    return {"hidden": bool(data.get("hidden")), "overrides": overrides}


def _detected() -> dict:
    draft = site.get("draft")
    defaults = site_schema.default_document()
    published = bool(site.revisions(1))
    return {
        "name": draft["app"]["name"] != defaults["app"]["name"] and bool(draft["images"]["icon"]),
        "theme": draft["theme"] != defaults["theme"],
        "homepage": draft["sections"] != defaults["sections"],
        "key": repo.key_fingerprint() is not None,
        "release": Release.query.filter_by(status="live").first() is not None,
        "publish": published and site.status() == "published",
    }


def _text() -> dict:
    return {
        "name": ("Name the app and give it an icon", url_for("admin.site") + "#group/app",
                 "Content > Name and links, and Images.",
                 "The app's name differs from the placeholder and it has an icon."),
        "theme": ("Choose the colors and fonts", url_for("admin.theme"),
                  "Theme. Every color has a light and a dark version.",
                  "Any theme setting differs from the default."),
        "homepage": ("Write the homepage", url_for("admin.site"),
                     "Content. Turn sections on or off, reorder them, add new ones.",
                     "The homepage's sections differ from the starting ones."),
        "key": ("Create the repository's signing key", url_for("admin.repository"),
                "Flatpak checks every update against it.",
                "The repository has a signing key."),
        "release": ("Upload the first release", url_for("admin.releases"),
                    "A .flatpak bundle, made with flatpak build-bundle.",
                    "A release is live on either channel."),
        "publish": ("Publish the site", url_for("admin.site"),
                    "Until then visitors see a coming-soon page.",
                    "The site has been published and its status is Live."),
    }


def state() -> dict:
    stored, auto, text = _stored(), _detected(), _text()
    steps = []
    for key in STEPS:
        override = stored["overrides"].get(key)
        title, url, hint, rule = text[key]
        steps.append({
            "id": key, "title": title, "url": url, "hint": hint,
            "detects": rule,
            "auto": auto[key],
            "override": override,
            "done": auto[key] if override is None else override,
        })
    return {"hidden": stored["hidden"], "steps": steps, "all_done": all(s["done"] for s in steps)}


def update(hidden: bool | None = None, steps: dict | None = None) -> dict:
    """``steps`` maps a step id to true (done), false (not done) or None
    (back to what is detected). A choice equal to the detection is stored
    as None, so the step follows detection again."""
    stored, auto = _stored(), _detected()
    if hidden is not None:
        stored["hidden"] = bool(hidden)
    for key, value in (steps or {}).items():
        if value is None or value == auto[key]:
            stored["overrides"].pop(key, None)
        else:
            stored["overrides"][key] = value
    set_setting("setup_checklist", json.dumps(stored))
    return state()
