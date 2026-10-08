"""Releases as the public site sees them.

The repository work (importing, signing, promoting) is in repo.py and jobs.py.
This module answers the read-only questions the site and the API ask: which
version is current on each channel, which architectures it was built for, and
where the install files are. What the dnf and apt repositories hold comes
from packages.public_info, under "packages".
"""
from __future__ import annotations

import re
from collections import OrderedDict

from .models import Release, get_setting

CHANNELS = ("stable", "beta")
# Debian and RPM name some architectures their own way.
ARCH_NAMES = {"x86_64": "Intel/AMD", "aarch64": "ARM", "amd64": "Intel/AMD", "arm64": "ARM",
              "noarch": "any computer", "all": "any computer"}


def arch_order(arches) -> list[str]:
    """x86_64 first: most machines are, and the install dialog picks the
    first one unless it sees an ARM machine."""
    return sorted(set(arches), key=lambda a: (a != "x86_64", a))


def heads(channel: str) -> list[Release]:
    """The live build of each architecture on a channel."""
    rows = Release.query.filter_by(channel=channel, status="live").all()
    return sorted(rows, key=lambda r: (r.arch != "x86_64", r.arch))


def version_key(version: str) -> tuple:
    """Orders versions the way people read them: 1.10 after 1.9. Numbers
    compare as numbers, anything else as text after them."""
    return tuple((0, int(part), "") if part.isdigit() else (1, 0, part.lower())
                 for part in re.findall(r"\d+|[A-Za-z]+", version or ""))


def _when(r: Release):
    return r.published_at or r.created_at


def _summary(rows: list[Release]) -> dict | None:
    """A channel's live builds. Its version is the newest any architecture
    has; architectures still on an older one are listed in ``behind``, so
    the site never claims a version a machine can't get."""
    if not rows:
        return None
    top = max(rows, key=lambda r: (version_key(r.version), _when(r)))
    notes = top.notes
    if not notes:
        # Each architecture is its own upload; the notes may be on another.
        other = (Release.query.filter(Release.app_id == top.app_id, Release.channel == top.channel,
                                      Release.version == top.version, Release.notes != "",
                                      Release.status.in_(("live", "superseded")))
                 .order_by(Release.published_at.desc(), Release.id.desc()).first())
        notes = other.notes if other else ""
    return {
        "version": top.version,
        "published_at": _when(top).isoformat() + "Z",
        "notes": notes,
        "arches": arch_order(r.arch for r in rows),
        "bundles": {r.arch: bool(r.bundle_file) for r in rows},
        "builds": {r.arch: {"version": r.version, "published_at": _when(r).isoformat() + "Z",
                            "bundle": bool(r.bundle_file)} for r in rows},
        "behind": arch_order(r.arch for r in rows if r.version != top.version),
    }


def history(channel: str | None = "stable", limit: int = 20) -> list[dict]:
    """Published versions, newest first, one entry per version however many
    architectures it was built for. Rollbacks and promotions are left out:
    they bring back or move a build, they don't make a new version."""
    query = Release.query.filter(Release.status.in_(("live", "superseded")),
                                 Release.origin != "rollback")
    if channel:
        query = query.filter_by(channel=channel)
    rows = query.order_by(Release.published_at.desc(), Release.id.desc()).limit(limit * 4).all()
    seen: "OrderedDict[tuple, dict]" = OrderedDict()
    for r in rows:
        key = (r.channel, r.version)
        entry = seen.get(key)
        if entry is None:
            seen[key] = {
                "version": r.version, "channel": r.channel, "notes": r.notes,
                "published_at": (r.published_at or r.created_at).isoformat() + "Z",
                "arches": [r.arch],
            }
        elif r.arch not in entry["arches"]:
            entry["arches"].append(r.arch)
            if not entry["notes"] and r.notes:
                entry["notes"] = r.notes
    return list(seen.values())[:limit]


APP_ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*(\.[A-Za-z_][A-Za-z0-9_-]*){2,}$")
REMOTE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,59}$")


def app_id_for() -> str:
    """The app ID the site installs: the one set under Repository > App, or
    the one the newest upload carried."""
    configured = get_setting("app_id")
    if configured:
        return configured
    newest = Release.query.order_by(Release.id.desc()).first()
    return newest.app_id if newest else ""


def beta_app_id_for(app_id: str) -> str:
    """The app ID beta builds use: a separate app (org.example.App.Beta, which
    installs beside the stable one) when set under Repository > App, or else
    the site's own app on the beta branch."""
    return get_setting("beta_app_id") or app_id


def remote_name(doc: dict) -> str:
    """What `flatpak remote-add` calls this repository: the one set under
    Repository > App, or the app's name in lower case."""
    configured = get_setting("remote_name")
    if configured:
        return configured
    slug = re.sub(r"[^a-z0-9]+", "-", (doc["app"].get("name") or "").lower()).strip("-")
    return slug or "flatout"


def public_info(doc: dict, base: str) -> dict:
    from . import packages
    app_id = app_id_for()
    beta_id = beta_app_id_for(app_id)
    remote = remote_name(doc)
    stable_heads, beta_heads = heads("stable"), heads("beta")
    stable_rows = [r for r in stable_heads if not app_id or r.app_id == app_id]
    beta_rows = [r for r in beta_heads if not beta_id or r.app_id == beta_id]
    file_id = app_id or "app"
    # Live builds of another app than the one the site names: the site and
    # its install files leave them out, which the admin has to say out loud.
    # Those only on the beta channel are fixed by the beta app ID instead.
    stable_off = {r.app_id for r in stable_heads if app_id and r.app_id != app_id}
    beta_off = {r.app_id for r in beta_heads if beta_id and r.app_id != beta_id}
    return {
        "app_id": app_id,
        "beta_app_id": beta_id,
        "remote_name": remote,
        "repo_url": f"{base}/repo/",
        "flatpakref_url": f"{base}/flatpak/{file_id}.flatpakref",
        "beta_flatpakref_url": f"{base}/flatpak/{file_id}-beta.flatpakref",
        "flatpakrepo_url": f"{base}/flatpak/{remote}.flatpakrepo",
        "stable": _summary(stable_rows),
        "beta": _summary(beta_rows),
        "history": history("stable", 20) + ([] if stable_rows else history("beta", 5)),
        "arches": arch_order([r.arch for r in stable_rows + beta_rows]),
        "arch_names": ARCH_NAMES,
        "bundle_url": f"{base}/download/{file_id}-{{arch}}.flatpak",
        "beta_bundle_url": f"{base}/download/{file_id}-beta-{{arch}}.flatpak",
        "unmatched_app_ids": sorted(stable_off | beta_off),
        "unmatched_beta_app_ids": sorted(beta_off - stable_off),
        # The dnf and apt repositories and other downloads (packages.py).
        "packages": packages.public_info(base, remote),
    }
