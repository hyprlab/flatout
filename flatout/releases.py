"""Releases as the public site sees them.

The repository work (importing, signing, promoting) is in repo.py and jobs.py.
This module answers the read-only questions the site and the API ask: which
version is current on each channel, which architectures it was built for, and
where the install files are.
"""
from __future__ import annotations

from collections import OrderedDict

from .models import Release

CHANNELS = ("stable", "beta")
ARCH_NAMES = {"x86_64": "Intel/AMD", "aarch64": "ARM"}


def heads(channel: str) -> list[Release]:
    """The live build of each architecture on a channel."""
    return (Release.query.filter_by(channel=channel, status="live")
            .order_by(Release.arch).all())


def _summary(rows: list[Release]) -> dict | None:
    if not rows:
        return None
    newest = max(rows, key=lambda r: r.published_at or r.created_at)
    return {
        "version": newest.version,
        "published_at": (newest.published_at or newest.created_at).isoformat() + "Z",
        "notes": newest.notes,
        "arches": sorted({r.arch for r in rows}),
        "bundles": {r.arch: bool(r.bundle_file) for r in rows},
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


def app_id_for(doc: dict) -> str:
    """The app ID the site installs: the one set in the editor, or the one
    the newest upload carried."""
    if doc["app"].get("app_id"):
        return doc["app"]["app_id"]
    newest = Release.query.order_by(Release.id.desc()).first()
    return newest.app_id if newest else ""


def public_info(doc: dict, base: str) -> dict:
    from .site import remote_name

    app_id = app_id_for(doc)
    remote = remote_name(doc)
    all_heads = heads("stable") + heads("beta")
    stable_rows = [r for r in heads("stable") if not app_id or r.app_id == app_id]
    beta_rows = [r for r in heads("beta") if not app_id or r.app_id == app_id]
    file_id = app_id or "app"
    # Live builds of another app than the one the site names: the site and
    # its install files leave them out, which the admin has to say out loud.
    unmatched = sorted({r.app_id for r in all_heads if app_id and r.app_id != app_id})
    return {
        "app_id": app_id,
        "remote_name": remote,
        "repo_url": f"{base}/repo/",
        "flatpakref_url": f"{base}/flatpak/{file_id}.flatpakref",
        "beta_flatpakref_url": f"{base}/flatpak/{file_id}-beta.flatpakref",
        "flatpakrepo_url": f"{base}/flatpak/{remote}.flatpakrepo",
        "stable": _summary(stable_rows),
        "beta": _summary(beta_rows),
        "history": history("stable", 20) + ([] if stable_rows else history("beta", 5)),
        "arches": sorted({r.arch for r in stable_rows} | {r.arch for r in beta_rows}),
        "arch_names": ARCH_NAMES,
        "bundle_url": f"{base}/download/{file_id}-{{arch}}.flatpak",
        "beta_bundle_url": f"{base}/download/{file_id}-beta-{{arch}}.flatpak",
        "unmatched_app_ids": unmatched,
    }
