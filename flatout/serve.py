"""What Flatpak clients fetch.

    /repo/<path>                    the OSTree repository
    /flatpak/<app-id>.flatpakref    the install file for the stable channel
    /flatpak/<app-id>-beta.flatpakref
    /flatpak/<remote>.flatpakrepo   adds the repository without installing
    /flatpak/<remote>.gpg           the signing key on its own
    /download/<app-id>-<arch>.flatpak, /download/<app-id>-beta-<arch>.flatpak
                                    the newest uploaded bundle

The install files are written on each request from the site's settings, so
they always match the current key, address and app.
"""
from flask import Blueprint, Response, abort, request, send_from_directory

from . import releases, repo, site, stats

bp = Blueprint("serve", __name__)


def client_address() -> str:
    """The visitor's address, for counting only. Behind Cloudflare the
    CF-Connecting-IP header is the real one; otherwise ProxyFix has already
    put the right address in remote_addr (TRUST_PROXY)."""
    return request.headers.get("CF-Connecting-IP", "").strip() or request.remote_addr or ""


@bp.route("/repo/<path:filename>")
def repo_file(filename):
    stats.record(filename, client_address())
    resp = send_from_directory(repo.repo_dir(), filename, conditional=True)
    # The summary and commit metadata change with every release: never cache
    # them anywhere, or a CDN would serve stale updates and hide the
    # requests that count installs. Content objects never change.
    if filename.startswith(("summary", "refs/")) or filename.endswith((".commitmeta", ".sig")) or filename == "config":
        resp.headers["Cache-Control"] = "no-store"
    elif filename.startswith(("objects/", "deltas/", "delta-indexes/")):
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    # Werkzeug labels .gz files Content-Encoding: gzip, which makes clients
    # unpack them on the way in; libostree wants the raw bytes and unpacks
    # them itself, so the header has to go.
    resp.headers.pop("Content-Encoding", None)
    return resp


def _keyfile_lines(lines: list[str]) -> Response:
    return Response("\n".join(lines) + "\n", mimetype="text/plain")


def _ref_file(doc: dict, branch: str) -> Response:
    info = releases.public_info(doc, site.base_url())
    renderer = site.Renderer(doc)
    key = repo.public_key_base64()
    if not info["app_id"] or not key:
        abort(404)
    icon = renderer.icon_url
    resp = _keyfile_lines([
        "[Flatpak Ref]",
        f"Name={info['beta_app_id'] if branch == 'beta' else info['app_id']}",
        f"Branch={branch}",
        f"Title={renderer.fill(doc['app']['name'])}" + (" (beta)" if branch == "beta" else ""),
        f"Url={info['repo_url']}",
        f"SuggestRemoteName={info['remote_name']}",
        f"Homepage={site.base_url()}/",
        f"Comment={renderer.values['app_tagline']}",
        f"Icon={icon if icon.startswith('http') else site.base_url() + icon}",
        f"RuntimeRepo={_runtime_repo()}",
        "IsRuntime=false",
        f"GPGKey={key}",
    ])
    resp.mimetype = "application/vnd.flatpak.ref"
    return resp


def _runtime_repo() -> str:
    from .models import get_setting
    return get_setting("runtime_repo") or "https://dl.flathub.org/repo/flathub.flatpakrepo"


@bp.route("/flatpak/<name>")
def flatpak_file(name):
    doc = site.get("live")
    info = releases.public_info(doc, site.base_url())
    remote = info["remote_name"]
    if info["app_id"] and name == f"{info['app_id']}.flatpakref":
        return _ref_file(doc, "stable")
    if info["app_id"] and name == f"{info['app_id']}-beta.flatpakref":
        return _ref_file(doc, "beta")
    if name == f"{remote}.flatpakrepo":
        key = repo.public_key_base64()
        if not key:
            abort(404)
        renderer = site.Renderer(doc)
        icon = renderer.icon_url
        resp = _keyfile_lines([
            "[Flatpak Repo]",
            f"Title={renderer.fill(doc['app']['name'])}",
            f"Url={info['repo_url']}",
            f"Homepage={site.base_url()}/",
            f"Comment={renderer.values['app_tagline']}",
            f"Icon={icon if icon.startswith('http') else site.base_url() + icon}",
            "DefaultBranch=stable",
            f"GPGKey={key}",
        ])
        resp.mimetype = "application/vnd.flatpak.repo"
        return resp
    if name == f"{remote}.gpg":
        key = repo.public_key()
        if not key:
            abort(404)
        return Response(key, mimetype="application/pgp-keys",
                        headers={"Content-Disposition": f'attachment; filename="{remote}.gpg"'})
    abort(404)


@bp.route("/download/<name>")
def download(name):
    """The newest bundle of a channel and architecture, under a name that
    never changes, so the link on the site always gives the current one."""
    if not name.endswith(".flatpak"):
        abort(404)
    stem = name[:-len(".flatpak")]
    app_id, _, arch = stem.rpartition("-")
    channel = "stable"
    if app_id.endswith("-beta"):
        app_id, channel = app_id[:-len("-beta")], "beta"
    if channel == "beta" and app_id == releases.app_id_for():
        # The address names the site's app; a separate beta app has its own ID.
        app_id = releases.beta_app_id_for(app_id)
    head = next((r for r in releases.heads(channel) if r.app_id == app_id and r.arch == arch and r.bundle_file), None)
    if head is None:
        abort(404)
    return send_from_directory(repo.bundles_dir(), head.bundle_file, as_attachment=True, download_name=name,
                               mimetype="application/vnd.flatpak")
