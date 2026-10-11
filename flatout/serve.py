"""What Flatpak clients fetch.

    /repo/<path>                    the OSTree repository
    /flatpak/<app-id>.flatpakref    the install file for the stable channel
    /flatpak/<app-id>-beta.flatpakref
    /flatpak/<remote>.flatpakrepo   adds the repository without installing
    /flatpak/<remote>.gpg           the signing key on its own
    /download/<app-id>-<arch>.flatpak, /download/<app-id>-beta-<arch>.flatpak
                                    the newest uploaded bundle

And what dnf and apt fetch (packages.py):

    /rpm/<channel>/                 a dnf repository: repodata/, packages/
    /rpm/<remote>.repo, /rpm/<remote>-beta.repo
                                    the file that adds it, for /etc/yum.repos.d
    /rpm/<remote>.asc               the signing key, armored
    /deb/                           an apt archive: dists/<channel>, pool/<channel>
    /deb/<remote>.sources, /deb/<remote>-beta.sources
                                    the file that adds it, key included
    /deb/<remote>.gpg               the signing key, for a Signed-By of your own
    /download/<name>-<arch>.rpm, /download/<name>-<arch>.deb (-beta- for the beta)
    /download/<file>, /download/beta/<file>
                                    the newest package, AppImage or other file

And what AppImage updaters fetch (appimage.py):

    /download/<name>.AppImage.zsync, /download/beta/<name>.AppImage.zsync
                                    the newest AppImage's zsync file, which
                                    every AppImage uploaded names in its
                                    update information
    /appimage/<id>/<file>, /appimage/<id>/<file>.zsync
                                    one version, which that zsync file names

The install files are written on each request from the site's settings, so
they always match the current key, address and app.
"""
import secrets
from pathlib import Path

from flask import Blueprint, Response, abort, current_app, request, send_file, send_from_directory
from werkzeug.http import parse_range_header

from . import appimage, packages, releases, repo, site, stats
from .models import db

bp = Blueprint("serve", __name__)


def client_address() -> str:
    """The visitor's address, for counting only. CF-Connecting-IP is only read
    when a trusted proxy is configured (TRUST_PROXY): heard directly, any
    client could put any address there and bend the install numbers."""
    if current_app.config["TRUST_PROXY"] > 0:
        cf = request.headers.get("CF-Connecting-IP", "").strip()
        if cf:
            return cf
    return request.remote_addr or ""


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
    never changes, so the link on the site always gives the current one.
    Packages and other files have addresses of the same kind."""
    if not name.endswith(".flatpak"):
        return _package_download(name)
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


@bp.route("/download/beta/<name>")
def download_beta(name):
    return _package_download("beta/" + name)


def _package_download(name: str):
    pkg = packages.find_download(name)
    if pkg is None and name.endswith(".zsync"):
        return _latest_zsync(name[:-len(".zsync")])
    if pkg is None or not pkg.path:
        abort(404)
    stats.count("pkgpull", str(pkg.id), client_address())
    resp = send_from_directory(packages.root(), pkg.path, as_attachment=True, download_name=Path(pkg.path).name,
                               mimetype=_mimetype(pkg))
    resp.headers["Cache-Control"] = "no-cache"   # the next version takes this address over
    resp.headers.pop("Content-Encoding", None)
    return resp


def _mimetype(pkg) -> str | None:
    return "application/vnd.appimage" if pkg.format == "appimage" else None


def _latest_zsync(name: str) -> Response:
    """The zsync file of the newest AppImage of a name and channel: what
    every copy installed from here asks for when it checks for updates. Its
    URL is made absolute, to the version it describes, so a client between
    two releases gets the file its checksums are for."""
    pkg = packages.find_download(name)
    zsync = packages.zsync_path(pkg) if pkg is not None else None
    if zsync is None:
        abort(404)
    stats.count("pkgcheck", f"appimage-{pkg.channel}", client_address())
    url = f"{site.base_url()}/appimage/{pkg.id}/{Path(pkg.path).name}"
    resp = Response(appimage.with_url(zsync.read_bytes(), url), mimetype="application/x-zsync")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route("/appimage/<int:package_id>/<filename>")
def appimage_file(package_id, filename):
    """One version of an AppImage, and its own zsync file. A version's file
    never changes, so it may be cached for good."""
    pkg = db.session.get(packages.Package, package_id)
    if pkg is None or pkg.format != "appimage" or pkg.status not in packages.IN_REPO or not pkg.path:
        abort(404)
    served = Path(pkg.path).name
    if filename == served:
        # A delta update asks for the blocks that changed, in ranges; only
        # a whole download, or one starting at the top, counts as one.
        ranges = parse_range_header(request.headers.get("Range"))
        if ranges is None or ranges.ranges[0][0] == 0:
            stats.count("pkgpull", str(pkg.id), client_address())
        resp = _byteranges(packages.root() / pkg.path, ranges, _mimetype(pkg)) or \
            send_from_directory(packages.root(), pkg.path, as_attachment=True, mimetype=_mimetype(pkg))
    elif filename == served + ".zsync" and packages.zsync_path(pkg):
        resp = send_from_directory(packages.root(), pkg.path + ".zsync", mimetype="application/x-zsync")
    else:
        abort(404)
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    resp.headers.pop("Content-Encoding", None)
    return resp


MAX_RANGES = 256


def _byteranges(path: Path, ranges, mimetype: str) -> Response | None:
    """Several ranges of a file in one multipart/byteranges answer, which is
    how zsync (AppImageUpdate, AppImageLauncher) fetches the blocks that
    changed; Werkzeug serves one range at a time and refuses more. None for
    a request Werkzeug handles. Werkzeug's parser only accepts ranges in
    order and apart, so the answer is never much more than the file."""
    if ranges is None or ranges.units != "bytes" or len(ranges.ranges) < 2:
        return None
    size = path.stat().st_size
    spans = []
    for start, stop in ranges.ranges:
        if start < 0:
            start, stop = max(size + start, 0), size
        stop = size if stop is None else min(stop, size)
        if start < stop:
            spans.append((start, stop))
    if not spans or len(spans) > MAX_RANGES:
        # Nothing satisfiable, or a request built to be expensive: the
        # whole file, which a client asking for ranges also accepts.
        return send_file(path, mimetype=mimetype, conditional=False)
    boundary = secrets.token_hex(16)
    heads = [f"\r\n--{boundary}\r\nContent-Type: {mimetype}\r\nContent-Range: bytes {a}-{b - 1}/{size}\r\n\r\n"
             .encode() for a, b in spans]
    tail = f"\r\n--{boundary}--\r\n".encode()

    def body():
        with open(path, "rb") as f:
            for head, (start, stop) in zip(heads, spans):
                yield head
                f.seek(start)
                left = stop - start
                while left > 0:
                    chunk = f.read(min(left, 1 << 16))
                    if not chunk:
                        return
                    left -= len(chunk)
                    yield chunk
            yield tail

    resp = Response(body(), 206, mimetype=f"multipart/byteranges; boundary={boundary}")
    resp.headers["Content-Length"] = str(sum(map(len, heads)) + sum(b - a for a, b in spans) + len(tail))
    resp.headers["Accept-Ranges"] = "bytes"
    return resp


# ———————————————————————————— dnf and apt ————————————————————————————

def _count_pull(relpath: str) -> None:
    pkg = packages.Package.query.filter(packages.Package.path == relpath,
                                        packages.Package.status.in_(packages.IN_REPO)).first()
    if pkg is not None:
        stats.count("pkgpull", str(pkg.id), client_address())


def _repo_files(directory, filename: str, index: bool, immutable: bool) -> Response:
    """A file of a dnf or apt repository. Indexes change with every upload,
    so nothing caches them; files named by their hash never change."""
    resp = send_from_directory(directory, filename, conditional=True)
    if index:
        resp.headers["Cache-Control"] = "no-store"
    elif immutable:
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    else:
        # A package's name says its version, but a new signing key signs it again.
        resp.headers["Cache-Control"] = "public, max-age=3600"
    # dnf and apt check the hash of the compressed file and unpack it
    # themselves; a Content-Encoding header would have them unpack it twice.
    resp.headers.pop("Content-Encoding", None)
    return resp


def _title(doc: dict) -> str:
    return site.Renderer(doc).fill(doc["app"]["name"]) or "Flatout"


@bp.route("/rpm/<path:filename>")
def rpm_file(filename):
    if "/" not in filename:
        return _rpm_config(filename)
    channel, _, rest = filename.partition("/")
    if channel not in releases.CHANNELS:
        abort(404)
    if rest == "repodata/repomd.xml":
        stats.count("pkgcheck", f"rpm-{channel}", client_address())
    elif rest.startswith("packages/"):
        _count_pull(f"rpm/{filename}")
    return _repo_files(packages.rpm_dir(), filename, index=rest.startswith("repodata/repomd.xml"),
                       immutable=rest.startswith("repodata/"))


def _rpm_config(name: str) -> Response:
    doc = site.get("live")
    remote, base = releases.remote_name(doc), site.base_url()
    if name == f"{remote}.asc":
        key = repo.public_key_armored() if repo.key_fingerprint() else ""
        if not key:
            abort(404)
        return Response(key, mimetype="application/pgp-keys")
    for channel, suffix in (("stable", ""), ("beta", "-beta")):
        if name != f"{remote}{suffix}.repo":
            continue
        if not (packages.rpm_dir() / channel / "repodata" / "repomd.xml").exists():
            abort(404)
        # metadata_expire: dnf's default is two days; a day brings a release
        # sooner and makes the daily count of installs checking honest.
        return _keyfile_lines([
            f"[{remote}{suffix}]",
            f"name={_title(doc)}" + (" (beta)" if suffix else ""),
            f"baseurl={base}/rpm/{channel}/",
            "enabled=1",
            "gpgcheck=1",
            "repo_gpgcheck=1",
            f"gpgkey={base}/rpm/{remote}.asc",
            "metadata_expire=1d",
        ])
    abort(404)


@bp.route("/deb/<path:filename>")
def deb_file(filename):
    if "/" not in filename:
        return _deb_config(filename)
    parts = filename.split("/")
    if parts[0] not in ("dists", "pool") or len(parts) < 3 or parts[1] not in releases.CHANNELS:
        abort(404)
    if parts[0] == "dists" and len(parts) == 3 and parts[2] in ("InRelease", "Release"):
        stats.count("pkgcheck", f"deb-{parts[1]}", client_address())
    elif parts[0] == "pool":
        _count_pull(f"deb/{filename}")
    return _repo_files(packages.deb_dir(), filename, index=parts[0] == "dists" and "by-hash" not in parts,
                       immutable="by-hash" in parts)


def _deb_config(name: str) -> Response:
    doc = site.get("live")
    remote, base = releases.remote_name(doc), site.base_url()
    if name == f"{remote}.gpg":
        key = repo.public_key()
        if not key:
            abort(404)
        return Response(key, mimetype="application/pgp-keys",
                        headers={"Content-Disposition": f'attachment; filename="{remote}.gpg"'})
    for channel, suffix in (("stable", ""), ("beta", "-beta")):
        if name != f"{remote}{suffix}.sources":
            continue
        armored = repo.public_key_armored() if repo.key_fingerprint() else ""
        if not armored or not (packages.deb_dir() / "dists" / channel / "InRelease").exists():
            abort(404)
        # The key goes inside the file (deb822, apt 2.4 and later), so adding
        # the repository is one download. Blank lines are written " .".
        key_lines = [" " + (line if line.strip() else ".") for line in armored.strip().splitlines()]
        return _keyfile_lines([
            f"# {_title(doc)}" + (" (beta)" if suffix else "") + f", from {base}/",
            "Types: deb",
            f"URIs: {base}/deb",
            f"Suites: {channel}",
            "Components: main",
            "Signed-By:",
            *key_lines,
        ])
    abort(404)
