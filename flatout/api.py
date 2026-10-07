"""The JSON API, version 1, under /api/v1.

One set of endpoints serves three kinds of caller:

* the admin pages, signed in with a session and sending the X-CSRF header;
* scripts and CI jobs, with an API token: ``Authorization: Bearer fo_…``;
* AI agents, through the MCP server (mcp.py), which calls these same handlers.

Errors are ``{"error": "a sentence"}``; a document that doesn't validate adds
``"errors": [{"path": "$.sections[2].title", "message": "…"}]`` with status 422.
The OpenAPI description is at /api/v1/openapi.json (openapi.py).
"""
from __future__ import annotations

import functools
import hashlib
import secrets
from datetime import timedelta

from flask import Blueprint, g, jsonify, request
from flask_login import current_user

from . import __version__, media, site, site_schema
from .models import ApiToken, Media, db, utcnow

bp = Blueprint("api", __name__, url_prefix="/api/v1")

#: What a token may be allowed to do. Every token can read.
SCOPES = {
    "read": "Read the site, media, releases, the repository and install numbers",
    "site": "Change and publish the site, and upload or delete media",
    "releases": "Upload, promote, roll back and withdraw releases",
}
TOKEN_PREFIX = "fo_"


class ApiError(Exception):
    def __init__(self, status: int, message: str, errors: list | None = None):
        super().__init__(message)
        self.status, self.message, self.errors = status, message, errors


@bp.errorhandler(ApiError)
def _api_error(err: ApiError):
    body = {"error": err.message}
    if err.errors:
        body["errors"] = err.errors
    return jsonify(body), err.status


@bp.errorhandler(site_schema.Invalid)
def _invalid(err: site_schema.Invalid):
    first = err.errors[0]
    more = f" (and {len(err.errors) - 1} more)" if len(err.errors) > 1 else ""
    return jsonify(error=f"{first['path']}: {first['message']}{more}", errors=err.errors), 422


# ———————————————————————————— Who is calling ————————————————————————————

def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def token_from_header() -> str | None:
    header = request.headers.get("Authorization", "")
    if header[:7].lower() == "bearer ":
        return header[7:].strip()
    return None


def resolve_token(token: str) -> ApiToken | None:
    row = ApiToken.query.filter_by(token_hash=hash_token(token)).first()
    if row is None or (row.expires_at and row.expires_at < utcnow()):
        return None
    now = utcnow()
    # Written at most once a minute, so a busy script doesn't hold the lock.
    if row.last_used_at is None or now - row.last_used_at > timedelta(minutes=1):
        row.last_used_at = now
        db.session.commit()
    return row


def authenticate(scope: str) -> None:
    """Sets g.actor (a name for logs and history) or raises ApiError.

    A request with a bearer token is judged by the token alone, never by a
    session cookie riding along, so a token's scopes always hold.
    """
    token = token_from_header()
    if token is not None:
        row = resolve_token(token)
        if row is None:
            raise ApiError(401, "This API token is unknown, revoked or expired.")
        if scope != "read" and scope not in row.scope_set:
            raise ApiError(403, f'This token lacks the "{scope}" scope.')
        g.api_user = row.user
        g.actor = f"{row.user.display_name} (token {row.name})"
        return
    if not current_user.is_authenticated:
        raise ApiError(401, "Sign in, or send an API token: Authorization: Bearer <token>.")
    g.api_user = current_user._get_current_object()
    g.actor = current_user.display_name


def needs(scope: str):
    def wrap(view):
        @functools.wraps(view)
        def inner(*args, **kwargs):
            authenticate(scope)
            return view(*args, **kwargs)
        inner.api_scope = scope
        return inner
    return wrap


def body() -> dict:
    data = request.get_json(silent=True)
    if data is None:
        if request.data:
            raise ApiError(400, "The body isn't valid JSON.")
        return {}
    if not isinstance(data, dict):
        raise ApiError(400, "The body must be a JSON object.")
    return data


# ———————————————————————————— Index ————————————————————————————

@bp.route("")
@bp.route("/")
@needs("read")
def index():
    return jsonify(
        name="Flatout", version=__version__,
        openapi=f"{site.base_url()}/api/v1/openapi.json",
        mcp=f"{site.base_url()}/mcp",
        you=g.actor,
    )


# ———————————————————————————— The site ————————————————————————————

def _site_response(name: str = "draft", doc: dict | None = None):
    return jsonify(
        version=name,
        document=doc if doc is not None else site.get(name),
        has_unpublished_changes=site.has_changes(),
        updated=site.meta(name),
    )


@bp.route("/site/schema")
@needs("read")
def site_schema_view():
    return jsonify(site_schema.schema_json())


@bp.route("/site")
@needs("read")
def site_get():
    name = request.args.get("version", "draft")
    if name not in ("draft", "live"):
        raise ApiError(400, 'version is "draft" or "live".')
    return _site_response(name)


def _check_base() -> None:
    """Optional optimistic locking. A client that sends the draft's
    ``updated.updated_at`` it last saw as X-Draft-Base gets a 409, instead of
    overwriting, when someone else (a person, a script, an agent) has saved
    the draft since."""
    base = request.headers.get("X-Draft-Base")
    if base:
        current = site.meta("draft")
        if current["updated_at"] != base:
            raise ApiError(409, f"{current['updated_by']} changed the draft since you loaded it. "
                                "Reload to see their changes.")


@bp.route("/site", methods=["PUT"])
@needs("site")
def site_put():
    _check_base()
    doc = site.save_draft(body(), g.actor)
    return _site_response("draft", doc)


@bp.route("/site", methods=["PATCH"])
@needs("site")
def site_patch():
    """A JSON Merge Patch (RFC 7396) on the draft: objects merge, null
    removes a key (which then falls back to its default), lists replace."""
    _check_base()
    patched = site.merge_patch(site.get("draft"), body())
    doc = site.save_draft(patched, g.actor)
    return _site_response("draft", doc)


@bp.route("/site/publish", methods=["POST"])
@needs("site")
def site_publish():
    note = str(body().get("note") or "")
    doc = site.publish(g.actor, note)
    return _site_response("live", doc)


@bp.route("/site/discard", methods=["POST"])
@needs("site")
def site_discard():
    doc = site.discard_draft(g.actor)
    return _site_response("draft", doc)


@bp.route("/site/revisions")
@needs("read")
def site_revisions():
    return jsonify(revisions=site.revisions(100))


@bp.route("/site/revisions/<int:revision_id>/restore", methods=["POST"])
@needs("site")
def site_restore(revision_id):
    doc = site.restore_revision(revision_id, g.actor)
    if doc is None:
        raise ApiError(404, "There is no revision with that id.")
    return _site_response("draft", doc)


@bp.route("/site/preview")
@needs("read")
def site_preview():
    """The draft homepage as HTML, so an agent can read what it changed."""
    from .public import render_home
    return render_home(site.get("draft"), preview=True)


# ——— Sections, one at a time ———

def _find_section(doc: dict, section_id: str) -> int:
    for i, s in enumerate(doc["sections"]):
        if s["id"] == section_id:
            return i
    raise ApiError(404, f'There is no section "{section_id}".')


@bp.route("/site/sections")
@needs("read")
def sections_list():
    doc = site.get("live" if request.args.get("version") == "live" else "draft")
    return jsonify(sections=[
        {"id": s["id"], "type": s["type"], "enabled": s["enabled"],
         "title": s.get("title") or s.get("eyebrow") or "",
         "type_label": site_schema.SECTION_TYPES[s["type"]].label}
        for s in doc["sections"]
    ], types={k: {"label": t.label, "description": t.description} for k, t in site_schema.SECTION_TYPES.items()})


@bp.route("/site/sections", methods=["POST"])
@needs("site")
def sections_add():
    """Add a section: {"type": "faq", "after": "features", "values": {...}}.
    Without "after" it goes at the end; "position": 0 puts it first."""
    data = body()
    stype = data.get("type")
    if stype not in site_schema.SECTION_TYPES:
        raise ApiError(400, "type is one of: " + ", ".join(site_schema.SECTION_TYPES))
    doc = site.get("draft")
    section = site_schema.new_section(stype, {s["id"] for s in doc["sections"]})
    if data.get("id"):
        section["id"] = str(data["id"])
    values = data.get("values") or {}
    if not isinstance(values, dict):
        raise ApiError(400, "values must be an object.")
    section = site.merge_patch(section, values)
    if "after" in data:
        at = _find_section(doc, str(data["after"])) + 1
    elif isinstance(data.get("position"), int):
        at = max(0, min(len(doc["sections"]), data["position"]))
    else:
        at = len(doc["sections"])
    doc["sections"].insert(at, section)
    doc = site.save_draft(doc, g.actor)
    return jsonify(section=doc["sections"][at], position=at), 201


@bp.route("/site/sections/<section_id>")
@needs("read")
def section_get(section_id):
    doc = site.get("draft")
    return jsonify(section=doc["sections"][_find_section(doc, section_id)])


@bp.route("/site/sections/<section_id>", methods=["PATCH"])
@needs("site")
def section_patch(section_id):
    doc = site.get("draft")
    i = _find_section(doc, section_id)
    patch = body()
    patch.pop("type", None)   # a section keeps its type; add a new one instead
    doc["sections"][i] = site.merge_patch(doc["sections"][i], patch)
    doc = site.save_draft(doc, g.actor)
    return jsonify(section=doc["sections"][i])


@bp.route("/site/sections/<section_id>", methods=["DELETE"])
@needs("site")
def section_delete(section_id):
    doc = site.get("draft")
    removed = doc["sections"].pop(_find_section(doc, section_id))
    site.save_draft(doc, g.actor)
    return jsonify(removed=removed)


@bp.route("/site/sections/order", methods=["POST"])
@needs("site")
def sections_order():
    """{"ids": [...]}: every section's id, in the new order."""
    ids = body().get("ids")
    doc = site.get("draft")
    current = [s["id"] for s in doc["sections"]]
    if not isinstance(ids, list) or sorted(map(str, ids)) != sorted(current):
        raise ApiError(400, "ids must list every section's id exactly once.")
    by_id = {s["id"]: s for s in doc["sections"]}
    doc["sections"] = [by_id[i] for i in ids]
    doc = site.save_draft(doc, g.actor)
    return jsonify(order=[s["id"] for s in doc["sections"]])


# ———————————————————————————— Media ————————————————————————————

@bp.route("/media")
@needs("read")
def media_list():
    query = Media.query
    if request.args.get("kind") in ("image", "font"):
        query = query.filter_by(kind=request.args["kind"])
    used = site.media_in_use()
    return jsonify(media=[media.as_json(m, used) for m in query.order_by(Media.id.desc())])


@bp.route("/media", methods=["POST"])
@needs("site")
def media_upload():
    """Multipart with a "file" field (and optional "alt"), or JSON
    {"filename": "...", "data_base64": "...", "alt": "..."} for clients that
    can't send multipart, agents among them."""
    import base64
    if request.files.get("file"):
        upload = request.files["file"]
        data, name, alt = upload.read(), upload.filename or "", request.form.get("alt", "")
    else:
        payload = body()
        try:
            data = base64.b64decode(payload.get("data_base64") or "", validate=True)
        except ValueError:
            raise ApiError(400, "data_base64 isn't valid base64.")
        name, alt = str(payload.get("filename") or ""), str(payload.get("alt") or "")
    try:
        item = media.store(data, name, alt)
    except media.Rejected as err:
        raise ApiError(400, str(err))
    return jsonify(media=media.as_json(item, site.media_in_use())), 201


@bp.route("/media/<int:media_id>", methods=["PATCH"])
@needs("site")
def media_update(media_id):
    item = db.session.get(Media, media_id) or _missing("file")
    data = body()
    if "alt" in data:
        item.alt = str(data["alt"] or "")[:300]
    if "name" in data and str(data["name"]).strip():
        item.original_name = str(data["name"]).strip()[:255]
    db.session.commit()
    return jsonify(media=media.as_json(item, site.media_in_use()))


@bp.route("/media/<int:media_id>", methods=["DELETE"])
@needs("site")
def media_delete(media_id):
    item = db.session.get(Media, media_id) or _missing("file")
    if item.url in site.media_in_use():
        raise ApiError(409, "The site still uses this file. Remove it from the draft, publish, then delete it.")
    media.delete(item)
    return jsonify(deleted=media_id)


def _missing(what: str):
    raise ApiError(404, f"There is no {what} with that id.")


# ———————————————————————————— Tokens ————————————————————————————
# Managed from the admin with a session only: a token can't mint tokens.

def _token_json(t: ApiToken) -> dict:
    return {
        "id": t.id, "name": t.name, "prefix": t.prefix, "scopes": sorted(t.scope_set),
        "owner": t.user.display_name,
        "created_at": t.created_at.isoformat() + "Z",
        "last_used_at": t.last_used_at.isoformat() + "Z" if t.last_used_at else None,
        "expires_at": t.expires_at.isoformat() + "Z" if t.expires_at else None,
    }


def _session_only():
    if token_from_header() is not None:
        raise ApiError(403, "API tokens are managed in the admin, not with a token.")
    authenticate("read")


@bp.route("/tokens")
def tokens_list():
    _session_only()
    rows = ApiToken.query.order_by(ApiToken.created_at.desc()).all()
    return jsonify(tokens=[_token_json(t) for t in rows], scopes=SCOPES)


@bp.route("/tokens", methods=["POST"])
def tokens_create():
    _session_only()
    data = body()
    name = str(data.get("name") or "").strip()[:80]
    if not name:
        raise ApiError(400, "Give the token a name, such as the script or agent that will use it.")
    scopes = {"read"} | {s for s in (data.get("scopes") or []) if s in SCOPES}
    days = data.get("expires_in_days")
    expires = None
    if days not in (None, "", 0):
        try:
            days = int(days)
        except (TypeError, ValueError):
            raise ApiError(400, "expires_in_days must be a number.")
        if not 1 <= days <= 3650:
            raise ApiError(400, "expires_in_days must be between 1 and 3650.")
        expires = utcnow() + timedelta(days=days)
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    row = ApiToken(user_id=g.api_user.id, name=name, prefix=token[:10], token_hash=hash_token(token),
                   scopes=" ".join(sorted(scopes)), expires_at=expires)
    db.session.add(row)
    db.session.commit()
    # The only time the token itself is ever sent.
    return jsonify(token=token, details=_token_json(row)), 201


@bp.route("/tokens/<int:token_id>", methods=["DELETE"])
def tokens_revoke(token_id):
    _session_only()
    row = db.session.get(ApiToken, token_id) or _missing("token")
    db.session.delete(row)
    db.session.commit()
    return jsonify(revoked=token_id)


# ———————————————————————————— Releases ————————————————————————————

def _release_json(r, with_log: bool = False) -> dict:
    from .models import Job
    job = Job.query.filter_by(release_id=r.id).order_by(Job.id.desc()).first()
    out = {
        "id": r.id, "app_id": r.app_id, "arch": r.arch, "channel": r.channel,
        "version": r.version, "notes": r.notes, "status": r.status, "commit": r.commit,
        "runtime": r.runtime, "origin": r.origin, "from_release": r.origin_release_id,
        "bundle": {"available": bool(r.bundle_file), "size": r.bundle_size, "sha256": r.bundle_sha256},
        "error": r.error or None, "created_by": r.created_by,
        "created_at": r.created_at.isoformat() + "Z",
        "published_at": r.published_at.isoformat() + "Z" if r.published_at else None,
        "job": _job_json(job, with_log) if job else None,
    }
    return out


def _job_json(j, with_log: bool = True) -> dict:
    out = {"id": j.id, "kind": j.kind, "status": j.status, "release_id": j.release_id,
           "created_by": j.created_by, "created_at": j.created_at.isoformat() + "Z",
           "started_at": j.started_at.isoformat() + "Z" if j.started_at else None,
           "finished_at": j.finished_at.isoformat() + "Z" if j.finished_at else None}
    if with_log:
        out["log"] = j.log
    return out


def _ready_for_releases() -> None:
    from . import repo
    missing = [name for name, ok in repo.tools().items() if not ok]
    if missing:
        raise ApiError(503, f"{', '.join(missing)} isn't installed here. Run Flatout from its Docker image.")
    if not repo.key_fingerprint():
        raise ApiError(409, "The repository has no signing key yet. Create or import one first.")


def _channel(value, default="stable") -> str:
    from .releases import CHANNELS
    channel = str(value or default)
    if channel not in CHANNELS:
        raise ApiError(400, "channel is stable or beta.")
    return channel


@bp.route("/releases")
@needs("read")
def releases_list():
    from .models import Release
    query = Release.query
    if request.args.get("channel"):
        query = query.filter_by(channel=_channel(request.args["channel"]))
    if request.args.get("status"):
        query = query.filter_by(status=request.args["status"])
    limit = min(max(request.args.get("limit", 50, type=int), 1), 500)
    rows = query.order_by(Release.id.desc()).limit(limit).all()
    return jsonify(releases=[_release_json(r) for r in rows])


@bp.route("/releases", methods=["POST"])
@needs("releases")
def releases_create():
    """Upload a bundle: multipart with "file" (a .flatpak made by
    `flatpak build-bundle`), "channel", and optional "version" and "notes".
    Or JSON {"url": "https://…/app.flatpak", "channel": …} to have Flatout
    download it. Answers 202 at once; the import runs as a job."""
    import hashlib
    import uuid
    from . import jobs, repo
    from .models import Release
    _ready_for_releases()
    upload = request.files.get("file")
    if upload is not None:
        fields = request.form
    else:
        fields = body()
        if not fields.get("url"):
            raise ApiError(400, 'Send the bundle as a multipart "file" field, or JSON with a "url" to fetch it from.')
        if not str(fields["url"]).startswith(("https://", "http://")):
            raise ApiError(400, "url must be an http(s) address.")
    channel = _channel(fields.get("channel"))
    rel = Release(app_id="", arch="", channel=channel, status="queued", created_by=g.actor,
                  version=str(fields.get("version") or "").strip()[:60],
                  notes=str(fields.get("notes") or "")[:20000])
    db.session.add(rel)
    db.session.flush()
    payload = {}
    if upload is not None:
        name = f"incoming-{rel.id}-{uuid.uuid4().hex[:8]}.flatpak"
        dest = repo.bundles_dir() / name
        upload.save(dest)
        digest = hashlib.sha256()
        with open(dest, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
        rel.bundle_file, rel.bundle_size, rel.bundle_sha256 = name, dest.stat().st_size, digest.hexdigest()
        if not rel.bundle_size:
            db.session.rollback()
            dest.unlink(missing_ok=True)
            raise ApiError(400, "The uploaded file is empty.")
    else:
        payload["url"] = str(fields["url"])
    db.session.commit()
    job = jobs.enqueue("import", rel.id, payload, g.actor)
    return jsonify(release=_release_json(rel), job=_job_json(job)), 202


@bp.route("/releases/<int:release_id>")
@needs("read")
def release_get(release_id):
    from .models import Release
    rel = db.session.get(Release, release_id) or _missing("release")
    return jsonify(release=_release_json(rel, with_log=True))


@bp.route("/releases/<int:release_id>", methods=["PATCH"])
@needs("releases")
def release_update(release_id):
    """Change the notes or the version label shown on the site. The build
    itself can't change; upload a new one for that."""
    from .models import Release
    rel = db.session.get(Release, release_id) or _missing("release")
    data = body()
    if "notes" in data:
        rel.notes = str(data["notes"] or "")[:20000]
    if "version" in data and str(data["version"]).strip():
        rel.version = str(data["version"]).strip()[:60]
    db.session.commit()
    return jsonify(release=_release_json(rel))


@bp.route("/releases/<int:release_id>/rollback", methods=["POST"])
@needs("releases")
def release_rollback(release_id):
    """Make an earlier build the live one again, as a new commit (Flatpak
    won't update to an older one)."""
    from . import jobs
    from .models import Release
    _ready_for_releases()
    target = db.session.get(Release, release_id) or _missing("release")
    if target.status not in ("superseded", "live") or not target.commit:
        raise ApiError(409, "Only a build that was live can be brought back.")
    rel = Release(app_id=target.app_id, arch=target.arch, channel=target.channel, version=target.version,
                  notes=target.notes, status="queued", origin="rollback", origin_release_id=target.id,
                  bundle_file=target.bundle_file, bundle_size=target.bundle_size,
                  bundle_sha256=target.bundle_sha256, created_by=g.actor)
    db.session.add(rel)
    db.session.commit()
    job = jobs.enqueue("rollback", rel.id, {}, g.actor)
    return jsonify(release=_release_json(rel), job=_job_json(job)), 202


@bp.route("/releases/promote", methods=["POST"])
@needs("releases")
def releases_promote():
    """Copy the live builds of one channel to another, usually beta to
    stable: {"from": "beta", "to": "stable"}, optionally "arch" and "notes"."""
    from . import jobs
    from .models import Release
    _ready_for_releases()
    data = body()
    source, dest = _channel(data.get("from"), "beta"), _channel(data.get("to"), "stable")
    if source == dest:
        raise ApiError(400, "from and to must be different channels.")
    heads = Release.query.filter_by(channel=source, status="live").all()
    if data.get("arch"):
        heads = [h for h in heads if h.arch == data["arch"]]
    if not heads:
        raise ApiError(409, f"The {source} channel has no live build to promote.")
    created = []
    for head in heads:
        rel = Release(app_id=head.app_id, arch=head.arch, channel=dest, version=head.version,
                      notes=str(data["notes"]) if data.get("notes") is not None else head.notes,
                      status="queued", origin="promote", origin_release_id=head.id,
                      bundle_file=head.bundle_file, bundle_size=head.bundle_size,
                      bundle_sha256=head.bundle_sha256, created_by=g.actor)
        db.session.add(rel)
        db.session.flush()
        created.append(rel)
    db.session.commit()
    out = []
    for rel in created:
        job = jobs.enqueue("promote", rel.id, {}, g.actor)
        out.append({"release": _release_json(rel), "job": _job_json(job)})
    return jsonify(promoted=out), 202


@bp.route("/channels/<channel>/end", methods=["POST"])
@needs("releases")
def channel_end(channel):
    """Retire a channel. Installed copies are told no more updates will come,
    with the message given. The beta section disappears from the site."""
    from . import jobs
    from .models import Release
    _ready_for_releases()
    channel = _channel(channel)
    if not Release.query.filter_by(channel=channel, status="live").first():
        raise ApiError(409, f"The {channel} channel has no live builds.")
    message = str(body().get("message") or "").strip()[:300]
    job = jobs.enqueue("end", None, {"channel": channel, "message": message}, g.actor)
    return jsonify(job=_job_json(job)), 202


@bp.route("/jobs")
@needs("read")
def jobs_list():
    from .models import Job
    limit = min(max(request.args.get("limit", 30, type=int), 1), 200)
    rows = Job.query.order_by(Job.id.desc()).limit(limit).all()
    return jsonify(jobs=[_job_json(j, with_log=False) for j in rows])


@bp.route("/jobs/<int:job_id>")
@needs("read")
def job_get(job_id):
    from .models import Job
    job = db.session.get(Job, job_id) or _missing("job")
    return jsonify(job=_job_json(job))


# ———————————————————————————— The repository ————————————————————————————

REPO_SETTINGS = {
    "app_id": "The Flatpak app ID the site installs, such as org.example.App; empty uses the newest upload's",
    "remote_name": "What `flatpak remote-add` calls this repository; empty uses the app's name in lower case",
    "public_url": "The site's public address, such as https://app.example.org",
    "runtime_repo": "Where installs fetch the runtime from (a .flatpakrepo address)",
    "prune_depth": "How many past builds of each channel to keep for rollback (1-100)",
}


def _repo_json() -> dict:
    from . import releases, repo
    from .models import Release, get_setting
    doc = site.get("live")
    info = releases.public_info(doc, site.base_url())
    return {
        "app_id": info["app_id"], "remote_name": info["remote_name"],
        # Live releases of another app ID than the site's: neither the site
        # nor its install files show them until the two agree.
        "unmatched_app_ids": info["unmatched_app_ids"],
        # Every app ID uploaded so far, for choosing one.
        "release_app_ids": sorted({r.app_id for r in Release.query if r.app_id}),
        "urls": {k: info[k] for k in ("repo_url", "flatpakref_url", "beta_flatpakref_url", "flatpakrepo_url")},
        "signing_key": repo.key_info(),
        "tools": repo.tools(),
        "stable": info["stable"], "beta": info["beta"],
        # What is set; empty means the default described in REPO_SETTINGS.
        "settings": {
            "app_id": get_setting("app_id") or "",
            "remote_name": get_setting("remote_name") or "",
            "public_url": get_setting("public_url") or "",
            "runtime_repo": get_setting("runtime_repo") or "https://dl.flathub.org/repo/flathub.flatpakrepo",
            "prune_depth": int(get_setting("prune_depth") or 10),
        },
        "detected_url": site.base_url() if not get_setting("public_url") else None,
    }


@bp.route("/repo")
@needs("read")
def repo_get():
    return jsonify(_repo_json())


@bp.route("/repo/settings", methods=["PATCH"])
@needs("releases")
def repo_settings():
    """Any of app_id, remote_name, public_url, runtime_repo, prune_depth.
    Everything is checked before anything is saved; an empty string returns
    a setting to its default."""
    import re
    from .models import set_setting
    from .releases import APP_ID_RE, REMOTE_RE
    data = body()
    unknown = set(data) - set(REPO_SETTINGS)
    if unknown:
        raise ApiError(400, f"Not a repository setting: {', '.join(sorted(unknown))}.")
    changes = {}
    if "app_id" in data:
        value = str(data["app_id"] or "").strip()
        if value and not APP_ID_RE.match(value):
            raise ApiError(400, "app_id must be an app ID like org.example.App.")
        changes["app_id"] = value
    if "remote_name" in data:
        value = str(data["remote_name"] or "").strip()
        if value and not REMOTE_RE.match(value):
            raise ApiError(400, "remote_name may use letters, digits, dots, dashes and underscores.")
        changes["remote_name"] = value
    if "public_url" in data:
        value = str(data["public_url"] or "").strip().rstrip("/")
        if value and not re.match(r"^https?://[^\s/]+(/[^\s]*)?$", value):
            raise ApiError(400, "public_url must start with https:// (or http://).")
        changes["public_url"] = value
    if "runtime_repo" in data:
        value = str(data["runtime_repo"] or "").strip()
        if value and not value.startswith(("https://", "http://")):
            raise ApiError(400, "runtime_repo must be an http(s) address.")
        changes["runtime_repo"] = value
    if "prune_depth" in data:
        try:
            depth = int(data["prune_depth"])
        except (TypeError, ValueError):
            raise ApiError(400, "prune_depth must be a number.")
        if not 1 <= depth <= 100:
            raise ApiError(400, "prune_depth must be between 1 and 100.")
        changes["prune_depth"] = str(depth)
    for key, value in changes.items():
        set_setting(key, value)
    return jsonify(_repo_json())


@bp.route("/repo/key", methods=["POST"])
@needs("releases")
def repo_key():
    """{"action": "generate", "name": "…", "email": "…"} or
    {"action": "import", "armored": "-----BEGIN PGP PRIVATE KEY BLOCK-----…"}.
    Replacing a key that has signed releases needs "replace": true, and every
    install will have to add the repository again."""
    from . import jobs, repo
    data = body()
    if not repo.tools()["gpg"]:
        raise ApiError(503, "gpg isn't installed here. Run Flatout from its Docker image.")
    if repo.key_fingerprint() and not data.get("replace"):
        raise ApiError(409, "The repository already has a signing key. Installs trust that key; replacing it "
                            "means every install has to add the repository again. Send \"replace\": true to do it anyway.")
    action = data.get("action")
    if action == "generate":
        name = str(data.get("name") or site.get("live")["app"]["name"] or "Flatout").strip()[:120]
        job = jobs.enqueue("keygen", None, {"name": f"{name} repository", "email": str(data.get("email") or "")[:200]}, g.actor)
        return jsonify(job=_job_json(job)), 202
    if action == "import":
        try:
            fpr = repo.import_key(str(data.get("armored") or ""))
        except repo.RepoError as err:
            raise ApiError(400, str(err))
        if repo.refs():
            jobs.enqueue("summary", None, {}, g.actor)
        return jsonify(signing_key=repo.key_info(), fingerprint=fpr)
    raise ApiError(400, 'action is "generate" or "import".')


@bp.route("/repo/key/public")
@needs("read")
def repo_key_public():
    from . import repo
    armored = repo.public_key_armored() if repo.key_fingerprint() else ""
    if not armored:
        raise ApiError(404, "There is no signing key yet.")
    return jsonify(fingerprint=repo.key_fingerprint(), armored=armored)


@bp.route("/repo/key/secret")
def repo_key_secret():
    """The secret key, for the owner's backup. Session only: a token can't
    take the key away."""
    from . import repo
    _session_only()
    try:
        return jsonify(fingerprint=repo.key_fingerprint(), armored=repo.secret_key_armored())
    except repo.RepoError as err:
        raise ApiError(404, str(err))


@bp.route("/repo/rebuild", methods=["POST"])
@needs("releases")
def repo_rebuild():
    """Re-sign the summary and regenerate deltas, after changing the address
    or the app's name, say."""
    from . import jobs
    _ready_for_releases()
    job = jobs.enqueue("summary", None, {}, g.actor)
    return jsonify(job=_job_json(job)), 202


# ———————————————————————————— Install numbers ————————————————————————————

@bp.route("/stats")
@needs("read")
def stats_get():
    from . import stats
    days = min(max(request.args.get("days", 30, type=int), 7), 365)
    return jsonify(stats.snapshot(days))


# ———————————————————————————— The description ————————————————————————————

@bp.route("/openapi.json")
def openapi():
    """This API's OpenAPI 3.1 description. Public: it holds no data, and an
    agent needs it before it has a token to use."""
    from . import openapi as spec
    return jsonify(spec.spec())
