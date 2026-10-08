"""Repository jobs, run one at a time in a background thread.

Importing a bundle or regenerating static deltas can take minutes, and the
repository takes one writer at a time, so requests only queue a job
(``enqueue``) and answer at once; this thread does the work and records what
happened in the job's log. Gunicorn runs one worker process (see the
Dockerfile), so there is exactly one of these threads.

A job interrupted by a restart is marked failed on the next start rather than
run again blindly: its release may be half imported, and the person who
started it should look before trying again.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import shutil
import socket
import threading
import time
import traceback
import urllib.parse
import urllib.request
from pathlib import Path

from flask import Flask

from . import repo
from .models import Job, Package, Release, db, end_worker_transaction, get_setting, utcnow, worker

log = logging.getLogger(__name__)

_wake = threading.Event()
_started = False
_lock = threading.Lock()
_secrets: dict[int, str] = {}   # job id -> passphrase, see enqueue()
BUNDLES_KEPT = 3     # per channel and architecture, for the download button


def enqueue(kind: str, release_id: int | None = None, payload: dict | None = None, who: str = "",
            secret: str | None = None, package_id: int | None = None) -> Job:
    """Queue a job. ``secret`` (a backup's passphrase) is never stored: it
    waits in memory for its job, and a restart loses it with the job."""
    job = Job(kind=kind, release_id=release_id, package_id=package_id, payload=json.dumps(payload or {}),
              created_by=who)
    db.session.add(job)
    db.session.flush()
    if secret is not None:
        _secrets[job.id] = secret
    db.session.commit()
    _wake.set()
    return job


def start(app: Flask) -> None:
    global _started
    with _lock:
        if _started:
            return
        _started = True
    with app.app_context():
        for job in Job.query.filter_by(status="running"):
            job.status = "failed"
            job.log += "\nInterrupted: Flatout restarted while this job ran. Check the release, then try again."
            job.finished_at = utcnow()
            if job.release_id:
                rel = db.session.get(Release, job.release_id)
                if rel and rel.status == "processing":
                    rel.status = "failed"
                    rel.error = "Interrupted by a restart."
            if job.package_id:
                _package_failed(db.session.get(Package, job.package_id), "Interrupted by a restart.")
        db.session.commit()
    threading.Thread(target=_loop, args=(app,), daemon=True, name="flatout-jobs").start()


def _loop(app: Flask) -> None:
    worker.active = True
    while True:
        _wake.wait(timeout=10)
        _wake.clear()
        while True:
            with app.app_context():
                job = Job.query.filter_by(status="queued").order_by(Job.id).first()
                if job is None:
                    break
                run_job(job)
                db.session.remove()


def run_all_queued(app: Flask) -> None:
    """For tests and the CLI: work through the queue in this thread."""
    with app.app_context():
        while True:
            job = Job.query.filter_by(status="queued").order_by(Job.id).first()
            if job is None:
                return
            run_job(job)


def run_job(job: Job) -> None:
    lines: list[str] = []
    job.status = "running"
    job.started_at = utcnow()
    db.session.commit()
    started = time.monotonic()
    try:
        handler = HANDLERS[job.kind]
        handler(job, json.loads(job.payload or "{}"), lines)
        job.status = "done"
    except Exception as err:   # every failure ends up in the job's log
        db.session.rollback()
        job = db.session.get(Job, job.id)
        job.status = "failed"
        message = str(err) if isinstance(err, repo.RepoError) else f"{type(err).__name__}: {err}"
        lines.append("FAILED: " + message)
        if not isinstance(err, repo.RepoError):
            lines.append(traceback.format_exc())
        if job.release_id:
            rel = db.session.get(Release, job.release_id)
            if rel and rel.status in ("queued", "processing"):
                rel.status = "failed"
                rel.error = message
        if job.package_id:
            _package_failed(db.session.get(Package, job.package_id), message)
        log.warning("job %s (%s) failed: %s", job.id, job.kind, message)
    lines.append(f"Finished in {time.monotonic() - started:.1f}s.")
    job.log = (job.log + "\n" if job.log else "") + "\n".join(lines)
    job.finished_at = utcnow()
    db.session.commit()


def _package_failed(pkg: Package | None, message: str) -> None:
    """An upload that never made it into its repository: say why, and drop
    the file it left in incoming/. A package already published keeps its
    status (a withdrawal that failed leaves it where it was)."""
    if pkg is None or pkg.status not in ("queued", "processing"):
        return
    from . import packages
    if pkg.path and pkg.path.startswith("incoming/"):
        (packages.root() / pkg.path).unlink(missing_ok=True)
        pkg.path = None
    pkg.status = "failed"
    pkg.error = message


# ———————————————————————————— Handlers ————————————————————————————

def _summary(lines: list) -> None:
    from . import site
    doc = site.get("live")
    app = doc["app"]
    repo.update_summary(lines, title=app["name"], comment=app["tagline"], homepage=site.base_url())


def _go_live(rel: Release) -> None:
    """Make a release the live build of its channel and architecture; the one
    it replaces stays in history as superseded."""
    for old in Release.query.filter_by(app_id=rel.app_id, arch=rel.arch, channel=rel.channel, status="live"):
        if old.id != rel.id:
            old.status = "superseded"
    rel.status = "live"
    rel.published_at = utcnow()
    rel.error = ""


def _prune_bundles(rel: Release) -> None:
    """Keep the newest few bundles per channel and architecture; older
    releases stay listed and can still be rolled back to, they just lose
    their download."""
    keep = (Release.query.filter_by(app_id=rel.app_id, arch=rel.arch, channel=rel.channel)
            .filter(Release.bundle_file.isnot(None))
            .order_by(Release.id.desc()).all())
    still_needed = {r.bundle_file for r in Release.query.filter_by(status="live") if r.bundle_file}
    for old in keep[BUNDLES_KEPT:]:
        if old.bundle_file in still_needed:
            continue
        shared = Release.query.filter(Release.bundle_file == old.bundle_file, Release.id != old.id).count()
        if not shared:
            (repo.bundles_dir() / old.bundle_file).unlink(missing_ok=True)
        old.bundle_file = None


def _assert_public(url: str) -> None:
    """A fetched URL must name a public address: a private, loopback or
    link-local one would let whoever asked for the file read the internal
    network through Flatout itself."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise repo.RepoError("url must be an http(s) address.")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80),
                                    proto=socket.IPPROTO_TCP)
    except socket.gaierror as err:
        raise repo.RepoError(f"{parts.hostname} does not resolve: {err}")
    for info in infos:
        if not ipaddress.ip_address(info[4][0].split("%", 1)[0]).is_global:
            raise repo.RepoError(f"{parts.hostname} is not a public address, so it can't be fetched.")


class _PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Checks every hop too: a public address may redirect to a private one."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _assert_public(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetch(url: str, dest: Path, lines: list) -> tuple[int, str]:
    """Download a bundle from a URL (a CI artifact, a GitHub release asset).
    Only a public address is fetched, on every redirect hop."""
    import hashlib
    from flask import current_app
    limit = current_app.config["MAX_CONTENT_LENGTH"]
    _assert_public(url)
    end_worker_transaction()
    lines.append(f"Downloading {url}")
    digest, size = hashlib.sha256(), 0
    req = urllib.request.Request(url, headers={"User-Agent": "Flatout"})
    opener = urllib.request.build_opener(_PublicRedirectHandler)
    with opener.open(req, timeout=60) as resp, open(dest, "wb") as out:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise repo.RepoError(f"The download is larger than the {limit // (1024 * 1024)} MB limit.")
            digest.update(chunk)
            out.write(chunk)
    lines.append(f"Downloaded {size} bytes.")
    return size, digest.hexdigest()


def handle_import(job: Job, payload: dict, lines: list) -> None:
    rel = db.session.get(Release, job.release_id)
    rel.status = "processing"
    db.session.commit()
    if not rel.bundle_file and payload.get("url"):
        name = f"incoming-{rel.id}.flatpak"
        size, sha = _fetch(payload["url"], repo.bundles_dir() / name, lines)
        rel.bundle_file, rel.bundle_size, rel.bundle_sha256 = name, size, sha
        db.session.commit()
    bundle = repo.bundles_dir() / (rel.bundle_file or "")
    if not rel.bundle_file or not bundle.exists():
        raise repo.RepoError("The uploaded bundle is missing.")
    info = repo.import_bundle(bundle, rel.channel, lines)
    expected = get_setting("app_id")
    if expected and rel.channel == "beta":
        expected = get_setting("beta_app_id") or expected
    rel.app_id, rel.arch = info["app_id"], info["arch"]
    rel.source_ref, rel.commit, rel.runtime = info["source_ref"], info["commit"], info["runtime"]
    if not rel.version:
        rel.version = info["version"] or utcnow().strftime("%Y.%m.%d")
    # Name the stored bundle after what it is, now that that's known.
    final = f"{info['app_id']}-{rel.channel}-{info['arch']}-{rel.id}.flatpak"
    shutil.move(str(bundle), str(repo.bundles_dir() / final))
    rel.bundle_file = final
    lines.append(f"Imported {info['source_ref']} as app/{info['app_id']}/{info['arch']}/{rel.channel}, "
                 f"version {rel.version}, commit {info['commit'][:12]}.")
    if expected and expected != info["app_id"]:
        lines.append(f"Note: the site's app ID is {expected} but this bundle is {info['app_id']}, so the site "
                     "and its install files won't show it until the two match (Repository > App).")
    _go_live(rel)
    db.session.commit()
    _summary(lines)
    _prune_bundles(rel)
    db.session.commit()


def handle_recommit(job: Job, payload: dict, lines: list) -> None:
    """Promote (copy another channel's build) or roll back (copy an older
    commit of the same channel). The release row already says which."""
    rel = db.session.get(Release, job.release_id)
    source = db.session.get(Release, rel.origin_release_id)
    if source is None or not source.commit:
        raise repo.RepoError("The release to copy from no longer exists.")
    if not repo.has_commit(source.commit):
        raise repo.RepoError(f"Commit {source.commit[:12]} has been pruned from the repository's history.")
    rel.status = "processing"
    db.session.commit()
    target = f"app/{source.app_id}/{source.arch}/{rel.channel}"
    rel.commit = repo.recommit(source.commit, target, lines)
    rel.app_id, rel.arch, rel.runtime, rel.source_ref = source.app_id, source.arch, source.runtime, source.source_ref
    lines.append(f"{'Promoted' if rel.origin == 'promote' else 'Rolled back'} {source.channel} {source.version} "
                 f"({source.arch}) to {rel.channel}: commit {rel.commit[:12]}.")
    _go_live(rel)
    db.session.commit()
    _summary(lines)


def handle_end(job: Job, payload: dict, lines: list) -> None:
    """Retire a channel: an end-of-life commit tells installed copies, with
    the message, that no more updates will come."""
    channel, message = payload["channel"], payload.get("message") or "This channel has ended."
    heads = Release.query.filter_by(channel=channel, status="live").all()
    if not heads:
        raise repo.RepoError(f"The {channel} channel has no live builds.")
    for head in heads:
        target = f"app/{head.app_id}/{head.arch}/{channel}"
        commit = repo.recommit(target, target, lines, end_of_life=message)
        head.status = "ended"
        lines.append(f"Ended {target}: commit {commit[:12]}.")
    db.session.commit()
    _summary(lines)


def handle_summary(job: Job, payload: dict, lines: list) -> None:
    from . import packages
    _summary(lines)
    if packages.any_published():
        packages.index_all(lines, resign=bool(payload.get("resign")))


def handle_package(job: Job, payload: dict, lines: list) -> None:
    """An uploaded package or file: into its channel's repository, or its
    downloads."""
    from . import packages
    pkg = db.session.get(Package, job.package_id)
    pkg.status = "processing"
    db.session.commit()
    if not pkg.path and payload.get("url"):
        pkg.path = f"incoming/incoming-{pkg.id}"
        db.session.commit()
        _fetch(payload["url"], packages.incoming_dir() / f"incoming-{pkg.id}", lines)
    src = packages.root() / (pkg.path or "")
    if not pkg.path or not src.exists():
        raise repo.RepoError("The uploaded file is missing.")
    packages.publish_upload(pkg, src, payload.get("filename") or "", lines)
    db.session.commit()


def handle_package_promote(job: Job, payload: dict, lines: list) -> None:
    from . import packages
    pkg = db.session.get(Package, job.package_id)
    pkg.status = "processing"
    db.session.commit()
    packages.publish_copy(pkg, lines)
    db.session.commit()


def handle_package_withdraw(job: Job, payload: dict, lines: list) -> None:
    from . import packages
    packages.withdraw(db.session.get(Package, job.package_id), lines)
    db.session.commit()


def handle_backup(job: Job, payload: dict, lines: list) -> None:
    from . import backup
    passphrase = _secrets.pop(job.id, None)
    if passphrase is None:
        raise repo.RepoError("The passphrase was lost when Flatout restarted. Start the backup again.")
    backup.create(passphrase, lines, job_id=job.id)


def handle_keygen(job: Job, payload: dict, lines: list) -> None:
    fpr = repo.generate_key(payload["name"], payload.get("email", ""), lines)
    lines.append(f"Created signing key {fpr}.")
    if repo.refs():
        lines.append("Re-signing the repository summary with the new key.")
        _summary(lines)
    from . import packages
    if packages.any_published():
        lines.append("Re-signing the packages and their indexes with the new key.")
        packages.index_all(lines, resign=True)


HANDLERS = {
    "import": handle_import,
    "promote": handle_recommit,
    "rollback": handle_recommit,
    "end": handle_end,
    "summary": handle_summary,
    "keygen": handle_keygen,
    "backup": handle_backup,
    "package": handle_package,
    "package-promote": handle_package_promote,
    "package-withdraw": handle_package_withdraw,
}
