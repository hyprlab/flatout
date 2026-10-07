"""Install numbers, read from ordinary repository traffic.

Two signals, both requests Flatpak makes anyway, so the app itself never
reports anything:

* ``summary`` and ``summary.idx``: every installed copy fetches one of these
  on each update check, so distinct fetchers per day estimate the installs in
  use.
* ``objects/xx/<rest>.commitmeta``: fetched once by every client that pulls a
  commit, by delta or not, so distinct fetchers of a release's commit estimate
  how many installs took that release.

No address is stored. Uniqueness uses SHA-256 of a secret salt, the day and
the address, so the hash changes daily and can't be linked across days or
back to anyone.
"""
from __future__ import annotations

import hashlib
import secrets
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from flask import current_app
from sqlalchemy import func
from sqlalchemy.dialects.sqlite import insert

from .models import Release, StatDay, StatSeen, db

SEEN_DAYS = 60
_salt_lock = threading.Lock()
_salt: bytes | None = None


def _salt_bytes() -> bytes:
    global _salt
    with _salt_lock:
        if _salt is None:
            path = Path(current_app.config["DATA_DIR"]) / ".stats_salt"
            if path.exists():
                _salt = path.read_bytes()
            else:
                _salt = secrets.token_bytes(32)
                path.write_bytes(_salt)
                path.chmod(0o600)
        return _salt


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def classify(filename: str) -> tuple[str, str] | None:
    """Map a repository path to (metric, target), or None to ignore it. Only
    two shapes count; the thousands of object fetches in a pull never reach
    the database."""
    if filename in ("summary", "summary.idx"):
        return ("check", "summary")
    if filename.startswith("objects/") and filename.endswith(".commitmeta"):
        parts = filename[len("objects/"):-len(".commitmeta")].split("/")
        if len(parts) == 2 and len(parts[0]) == 2:
            return ("pull", parts[0] + parts[1])
    return None


def record(filename: str, address: str) -> None:
    """Count one repository request. Never raises: a counting problem must
    not break a download."""
    hit = classify(filename)
    if hit is None or not address:
        return
    metric, target = hit
    day = _today()
    try:
        visitor = hashlib.sha256(_salt_bytes() + day.encode() + address.encode()).hexdigest()[:16]
        db.session.execute(
            insert(StatDay).values(day=day, metric=metric, target=target, uniques=0, hits=1)
            .on_conflict_do_update(index_elements=["day", "metric", "target"],
                                   set_={"hits": StatDay.hits + 1})
        )
        new = db.session.execute(
            insert(StatSeen).values(day=day, metric=metric, target=target, visitor=visitor)
            .on_conflict_do_nothing()
        ).rowcount
        if new:
            db.session.query(StatDay).filter_by(day=day, metric=metric, target=target).update(
                {StatDay.uniques: StatDay.uniques + 1})
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.warning("couldn't count a repository request", exc_info=True)


def prune() -> int:
    cutoff = (date.today() - timedelta(days=SEEN_DAYS)).isoformat()
    n = StatSeen.query.filter(StatSeen.day < cutoff).delete()
    db.session.commit()
    return n


def installs_today() -> int:
    row = StatDay.query.filter_by(day=_today(), metric="check", target="summary").first()
    return row.uniques if row else 0


def snapshot(days: int = 30) -> dict:
    """Everything the Installs page and the API show, in one read."""
    start = (date.today() - timedelta(days=days - 1)).isoformat()
    rows = (StatDay.query.filter(StatDay.metric == "check", StatDay.day >= start)
            .order_by(StatDay.day).all())
    by_day = {r.day: r for r in rows}
    series = []
    for i in range(days):
        d = (date.today() - timedelta(days=days - 1 - i)).isoformat()
        r = by_day.get(d)
        series.append({"day": d, "installs": r.uniques if r else 0, "checks": r.hits if r else 0})
    week = [s["installs"] for s in series[-7:]]

    pulls = (db.session.query(StatDay.target, func.sum(StatDay.uniques), func.sum(StatDay.hits),
                              func.min(StatDay.day), func.max(StatDay.day))
             .filter(StatDay.metric == "pull").group_by(StatDay.target).all())
    commits = {r.commit: r for r in Release.query.filter(Release.commit.isnot(None))}
    per_release: dict[tuple, dict] = {}
    unmatched = 0
    for commit, uniques, hits, first, last in pulls:
        rel = commits.get(commit)
        if rel is None:
            unmatched += uniques or 0
            continue
        key = (rel.channel, rel.version)
        entry = per_release.setdefault(key, {
            "channel": rel.channel, "version": rel.version, "installs": 0, "downloads": 0,
            "first_seen": first, "last_seen": last, "arches": {},
            "published_at": (rel.published_at or rel.created_at).isoformat() + "Z",
        })
        entry["installs"] += uniques or 0
        entry["downloads"] += hits or 0
        entry["arches"][rel.arch] = entry["arches"].get(rel.arch, 0) + (uniques or 0)
        entry["first_seen"] = min(entry["first_seen"], first)
        entry["last_seen"] = max(entry["last_seen"], last)
    releases = sorted(per_release.values(), key=lambda e: e["published_at"], reverse=True)
    return {
        "days": series,
        "today": series[-1]["installs"],
        "average_7_days": round(sum(week) / len(week), 1) if week else 0,
        "peak": max((s["installs"] for s in series), default=0),
        "releases": releases,
        "pulls_without_release": unmatched,
    }
