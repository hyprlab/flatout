"""Install numbers, read from ordinary repository traffic.

Two signals, both requests Flatpak makes anyway, so the app itself never
reports anything:

* ``summary`` and ``summary.idx``: every installed copy fetches one of these
  on each update check, so distinct fetchers per day estimate the installs in
  use.
* ``objects/xx/<rest>.commitmeta``: fetched once by every client that pulls a
  commit, by delta or not, so distinct fetchers of a release's commit estimate
  how many installs took that release.

The dnf and apt repositories count the same way: every install with the
repository added fetches its index (repomd.xml, InRelease) when it checks for
updates, and each package download counts once per requester and day.

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

from .models import Package, Release, StatDay, StatSeen, db

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
    if hit is not None:
        count(*hit, address)


def count(metric: str, target: str, address: str) -> None:
    """Count one request for a target, once per requester per day. Never
    raises. The dnf and apt repositories count here directly (serve.py):
    their index as ``pkgcheck``, a package's download as ``pkgpull``."""
    if not address:
        return
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


def _version_key(version: str) -> tuple:
    from .releases import version_key
    return version_key(version)


def snapshot(days: int = 30) -> dict:
    """Everything the Installs page and the API show, in one read. ``days``
    is the chart's range; the summary numbers use fixed windows, named in
    their descriptions."""
    today = date.today()
    start = (today - timedelta(days=days - 1)).isoformat()
    week_start = (today - timedelta(days=6)).isoformat()
    month_start = (today - timedelta(days=29)).isoformat()
    fortnight_start = (today - timedelta(days=13)).isoformat()

    # ———— Update checks: installs in use ————
    rows = (StatDay.query.filter(StatDay.metric == "check", StatDay.day >= start)
            .order_by(StatDay.day).all())
    by_day = {r.day: r for r in rows}
    series = []
    for i in range(days):
        d = (today - timedelta(days=days - 1 - i)).isoformat()
        r = by_day.get(d)
        series.append({"day": d, "installs": r.uniques if r else 0, "checks": r.hits if r else 0})
    week = [s["installs"] for s in series[-7:]]
    busiest = max(series, key=lambda s: s["installs"]) if series else None
    counting_since = db.session.query(func.min(StatDay.day)).filter(StatDay.metric == "check").scalar()
    week_peak = (db.session.query(func.max(StatDay.uniques))
                 .filter(StatDay.metric == "check", StatDay.day >= week_start).scalar()) or 0

    # ———— Pulls: which build each install downloaded ————
    pulls = (db.session.query(StatDay.target, func.sum(StatDay.uniques), func.sum(StatDay.hits),
                              func.min(StatDay.day), func.max(StatDay.day))
             .filter(StatDay.metric == "pull").group_by(StatDay.target).all())
    recent = dict(db.session.query(StatDay.target, func.sum(StatDay.uniques))
                  .filter(StatDay.metric == "pull", StatDay.day >= month_start)
                  .group_by(StatDay.target).all())
    fortnight = dict(db.session.query(StatDay.target, func.sum(StatDay.uniques))
                     .filter(StatDay.metric == "pull", StatDay.day >= fortnight_start)
                     .group_by(StatDay.target).all())
    commits = {r.commit: r for r in Release.query.filter(Release.commit.isnot(None))}

    per_release: dict[tuple, dict] = {}
    builds, arches = [], {}
    unmatched = 0
    pulled_lately: dict[tuple, int] = {}
    for commit, uniques, hits, first, last in pulls:
        uniques, hits = uniques or 0, hits or 0
        rel = commits.get(commit)
        builds.append({
            "commit": commit, "version": rel.version if rel else None, "channel": rel.channel if rel else None,
            "arch": rel.arch if rel else None, "origin": rel.origin if rel else None,
            "installs": uniques, "downloads": hits, "first_seen": first, "last_seen": last,
            "published_at": (rel.published_at or rel.created_at).isoformat() + "Z" if rel else None,
        })
        if rel is None:
            unmatched += uniques
            continue
        key = (rel.channel, rel.version)
        entry = per_release.setdefault(key, {
            "channel": rel.channel, "version": rel.version, "installs": 0, "downloads": 0,
            "first_seen": first, "last_seen": last, "arches": {},
            "published_at": (rel.published_at or rel.created_at).isoformat() + "Z",
        })
        entry["installs"] += uniques
        entry["downloads"] += hits
        entry["arches"][rel.arch] = entry["arches"].get(rel.arch, 0) + uniques
        entry["first_seen"] = min(entry["first_seen"], first)
        entry["last_seen"] = max(entry["last_seen"], last)
        arch = arches.setdefault(rel.arch or "unknown", {"arch": rel.arch or "unknown", "installs_30_days": 0,
                                                         "installs": 0, "downloads": 0})
        arch["installs"] += uniques
        arch["downloads"] += hits
        arch["installs_30_days"] += recent.get(commit) or 0
        pulled_lately[key] = pulled_lately.get(key, 0) + (fortnight.get(commit) or 0)
    releases = sorted(per_release.values(), key=lambda e: e["published_at"], reverse=True)
    builds.sort(key=lambda b: (b["published_at"] or "", b["installs"]), reverse=True)   # unlabelled last

    # ———— The summary ————
    def latest(channel: str) -> dict | None:
        """The channel's current version and the installs that took it, on
        every architecture, since it was published."""
        live = Release.query.filter_by(channel=channel, status="live").all()
        if not live:
            return None
        top = max(live, key=lambda r: (_version_key(r.version), r.published_at or r.created_at))
        entry = per_release.get((channel, top.version), {})
        published = min((r.published_at or r.created_at) for r in Release.query.filter_by(
            channel=channel, version=top.version).filter(Release.status.in_(("live", "superseded"))))
        return {"version": top.version, "published_at": published.isoformat() + "Z",
                "installs": entry.get("installs", 0), "downloads": entry.get("downloads", 0),
                "arches": entry.get("arches", {})}

    stable, beta = latest("stable"), latest("beta")
    # Every install checks for updates daily, so the busiest recent day is
    # the best estimate of how many exist. Before checks have built up, the
    # most-pulled recent release says at least that many exist.
    lately = max(pulled_lately.values(), default=0)
    if week_peak >= lately and week_peak > 0:
        base, basis = week_peak, "checks"
    elif lately > 0:
        base, basis = lately, "release"
    else:
        base, basis = 0, "none"
    on_latest = (stable or {}).get("installs", 0) + (beta or {}).get("installs", 0)
    month_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
    shipped = {(r.channel, r.version) for r in Release.query.filter(
        Release.published_at >= month_ago, Release.origin != "rollback",
        Release.status.in_(("live", "superseded", "ended")))}

    return {
        "packages": _packages(today.isoformat(), week_start),
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "counting_since": counting_since,
        "days": series,
        "today": series[-1]["installs"] if series else 0,
        "average_7_days": round(sum(week) / len(week), 1) if week else 0,
        "peak": busiest["installs"] if busiest else 0,
        "peak_day": busiest["day"] if busiest and busiest["installs"] else None,
        "install_base": base,
        "install_base_basis": basis,
        "latest_stable": stable,
        "latest_beta": beta,
        "older_installs": max(base - on_latest, 0),
        "releases_30_days": {"total": len(shipped),
                             "stable": sum(1 for c, _ in shipped if c == "stable"),
                             "beta": sum(1 for c, _ in shipped if c == "beta")},
        "arches": sorted(arches.values(), key=lambda a: -a["installs"]),
        "releases": releases,
        "builds": builds,
        "pulls_without_release": unmatched,
    }


def _packages(today: str, week_start: str) -> dict:
    """The dnf and apt repositories: installs checking each one (distinct
    requesters of its index per day, today and at the busiest this week),
    and each package's downloads."""
    repositories: dict[str, dict] = {}
    for row in StatDay.query.filter(StatDay.metric == "pkgcheck", StatDay.day >= week_start):
        fmt, _, channel = row.target.partition("-")
        entry = repositories.setdefault(row.target, {"format": fmt, "channel": channel, "today": 0, "peak_7_days": 0})
        entry["peak_7_days"] = max(entry["peak_7_days"], row.uniques)
        if row.day == today:
            entry["today"] = row.uniques
    pulls = (db.session.query(StatDay.target, func.sum(StatDay.uniques), func.sum(StatDay.hits),
                              func.min(StatDay.day), func.max(StatDay.day))
             .filter(StatDay.metric == "pkgpull").group_by(StatDay.target).all())
    ids = [int(t) for t, *_ in pulls if t.isdigit()]
    rows = {str(p.id): p for p in Package.query.filter(Package.id.in_(ids))} if ids else {}
    downloads = []
    for target, uniques, hits, first, last in pulls:
        pkg = rows.get(target)
        if pkg is None:
            continue
        downloads.append({
            "id": pkg.id, "name": pkg.name, "version": pkg.version, "format": pkg.format, "channel": pkg.channel,
            "arch": pkg.arch, "status": pkg.status, "installs": uniques or 0, "downloads": hits or 0,
            "first_seen": first, "last_seen": last,
            "published_at": (pkg.published_at or pkg.created_at).isoformat() + "Z",
        })
    downloads.sort(key=lambda d: (d["published_at"], d["installs"]), reverse=True)
    order = {"rpm-stable": 0, "rpm-beta": 1, "deb-stable": 2, "deb-beta": 3}
    return {"repositories": sorted(repositories.values(), key=lambda r: order.get(f"{r['format']}-{r['channel']}", 9)),
            "downloads": downloads}
