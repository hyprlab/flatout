"""Packages: the dnf and apt repositories, and other downloads.

Beside the Flatpak repository, Flatout publishes RPMs in a dnf repository,
Debian packages in an apt repository, and any other file (an AppImage, a
tarball) as a plain download, each on the stable and beta channels, signed
with the repository's own key.

Layout under DATA_DIR/packages:

    incoming/                 uploads waiting for their job
    rpm/<channel>/packages/   the RPMs, signed               } /rpm/<channel>/,
    rpm/<channel>/repodata/   createrepo_c's index            } a dnf baseurl
    deb/pool/<channel>/       the Debian packages             } /deb/, an apt archive
    deb/dists/<channel>/      Packages, Release, InRelease    } with a suite per channel
    files/<channel>/<id>/     everything else

How an upload is published:

1. Its first bytes say what it is. An RPM is read with ``rpm -qp`` and signed
   with ``rpmsign``, so dnf's gpgcheck holds whoever built it; a Debian
   package is read with ``dpkg-deb``.
2. It moves into its channel. A version already there is refused: the
   repository would offer two different files under one name and version,
   and clients that cached one would reject the other.
3. ``settle`` decides what is live: the newest version of each name and
   architecture, compared the way rpm or dpkg compare them rather than by
   upload order, because that is what dnf and apt install. Older versions
   stay for a downgrade, up to the number kept, then are pruned.
4. The channel's index is rewritten and signed: ``createrepo_c`` and a
   detached signature of repomd.xml for dnf; for apt, Packages and Release
   written here from the stored control paragraphs, then InRelease and
   Release.gpg. apt reads the index by hash, so a client halfway through an
   update never sees a Packages file that doesn't match its Release.

Neither dnf nor apt moves an install to an older version, so there is no
rollback as with Flatpak: withdrawing a bad version keeps new installs from
getting it, and installed copies stay on it until a newer one is published.

Every function that changes something runs in the job thread (jobs.py).
"""
from __future__ import annotations

import functools
import gzip
import hashlib
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

from flask import current_app

from . import repo
from .models import Package, db, int_setting, utcnow
from .repo import RepoError

FORMATS = ("rpm", "deb", "file")
FORMAT_NAMES = {"rpm": "RPM", "deb": "Debian", "file": "File"}
IN_REPO = ("live", "superseded")
KEPT = 3            # versions of each package kept per channel, unless set (packages_kept)
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+~-]{0,200}$")
BY_HASH_KEPT = 4    # earlier apt indexes kept, for clients that read the previous Release


def root() -> Path:
    return Path(current_app.config["DATA_DIR"]) / "packages"


def incoming_dir() -> Path:
    path = root() / "incoming"
    path.mkdir(parents=True, exist_ok=True)
    return path


def rpm_dir() -> Path:
    return root() / "rpm"


def deb_dir() -> Path:
    return root() / "deb"


def tools() -> dict:
    """Which formats can be published here. Plain files need nothing."""
    have = {name: shutil.which(name) is not None for name in ("rpm", "rpmsign", "createrepo_c", "dpkg-deb", "gpg")}
    return {"rpm": have["rpm"] and have["rpmsign"] and have["createrepo_c"] and have["gpg"],
            "deb": have["dpkg-deb"] and have["gpg"]}


def not_ready(fmt: str) -> str | None:
    """Why a format can't be published now, as a sentence, or None."""
    if fmt == "file":
        return None
    if not tools()[fmt]:
        need = "rpm, rpmsign and createrepo_c" if fmt == "rpm" else "dpkg-deb"
        return f"{need} and gpg aren't installed here. Run Flatout from its Docker image."
    if not repo.key_fingerprint():
        return "The repository has no signing key yet. Create one under Signing and addresses."
    return None


def sniff(path: Path, filename: str = "") -> str:
    """rpm, deb, flatpak or file, from the first bytes (and, for a Flatpak
    bundle, which has no magic number of its own, the name)."""
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:4] == b"\xed\xab\xee\xdb":
        return "rpm"
    if head[:8] == b"!<arch>\n" and head[8:21] == b"debian-binary":
        return "deb"
    if filename.lower().endswith(".flatpak"):
        return "flatpak"
    return "file"


def filename_from_url(url: str) -> str:
    return unquote(urlparse(url).path.rsplit("/", 1)[-1])


# ———————————————————————————— Versions ————————————————————————————

def _rpmvercmp(a: str, b: str) -> int:
    """rpm's own comparison (rpmvercmp.c): alternating runs of digits and
    letters, numbers by value, ~ before anything, ^ after the base version."""
    if a == b:
        return 0
    i = j = 0

    def alnum(s, k):
        return k < len(s) and s[k].isascii() and s[k].isalnum()

    while i < len(a) or j < len(b):
        while i < len(a) and not alnum(a, i) and a[i] not in "~^":
            i += 1
        while j < len(b) and not alnum(b, j) and b[j] not in "~^":
            j += 1
        ca, cb = a[i] if i < len(a) else "", b[j] if j < len(b) else ""
        if ca == "~" or cb == "~":
            if ca != "~":
                return 1
            if cb != "~":
                return -1
            i, j = i + 1, j + 1
            continue
        if ca == "^" or cb == "^":
            if not ca:
                return -1
            if not cb:
                return 1
            if ca != "^":
                return 1
            if cb != "^":
                return -1
            i, j = i + 1, j + 1
            continue
        if not (ca and cb):
            break
        si, sj = i, j
        numeric = ca.isdigit()
        kind = str.isdigit if numeric else str.isalpha
        while i < len(a) and a[i].isascii() and kind(a[i]):
            i += 1
        while j < len(b) and b[j].isascii() and kind(b[j]):
            j += 1
        if sj == j:
            # Different kinds of segment: a number is newer than letters.
            return 1 if numeric else -1
        x, y = a[si:i], b[sj:j]
        if numeric:
            x, y = int(x), int(y)
        if x != y:
            return 1 if x > y else -1
    if i >= len(a) and j >= len(b):
        return 0
    return -1 if i >= len(a) else 1


def _split_evr(evr: str) -> tuple[int, str, str]:
    """[epoch:]version[-release]. The epoch ends at the first colon; the
    release starts at the last dash (a Debian version may hold dashes)."""
    epoch, _, rest = evr.partition(":") if ":" in evr else ("0", "", evr)
    version, _, release = rest.rpartition("-") if "-" in rest else (rest, "", "")
    try:
        return int(epoch or 0), version, release
    except ValueError:
        return 0, version, release


def compare_rpm(a: str, b: str) -> int:
    ea, va, ra = _split_evr(a)
    eb, vb, rb = _split_evr(b)
    if ea != eb:
        return 1 if ea > eb else -1
    return _rpmvercmp(va, vb) or _rpmvercmp(ra, rb)


def _deb_order(c: str) -> int:
    if c.isdigit():
        return 0
    if c.isascii() and c.isalpha():
        return ord(c)
    if c == "~":
        return -1
    return ord(c) + 256 if c else 0


def _deb_verrevcmp(a: str, b: str) -> int:
    """dpkg's comparison (lib/dpkg/version.c): letters before other
    characters, ~ before everything, even the end."""
    i = j = 0
    while i < len(a) or j < len(b):
        first_diff = 0
        while (i < len(a) and not a[i].isdigit()) or (j < len(b) and not b[j].isdigit()):
            ac = _deb_order(a[i]) if i < len(a) else 0
            bc = _deb_order(b[j]) if j < len(b) else 0
            if ac != bc:
                return ac - bc
            i, j = i + 1, j + 1
        while i < len(a) and a[i] == "0":
            i += 1
        while j < len(b) and b[j] == "0":
            j += 1
        while i < len(a) and a[i].isdigit() and j < len(b) and b[j].isdigit():
            if not first_diff:
                first_diff = ord(a[i]) - ord(b[j])
            i, j = i + 1, j + 1
        if i < len(a) and a[i].isdigit():
            return 1
        if j < len(b) and b[j].isdigit():
            return -1
        if first_diff:
            return first_diff
    return 0


def compare_deb(a: str, b: str) -> int:
    ea, va, ra = _split_evr(a)
    eb, vb, rb = _split_evr(b)
    if ea != eb:
        return 1 if ea > eb else -1
    result = _deb_verrevcmp(va, vb) or _deb_verrevcmp(ra, rb)
    return (result > 0) - (result < 0)


def newest_first(rows: list[Package]) -> list[Package]:
    """The order a package manager ranks them: by version for packages, by
    upload for plain files, which have no version to go by."""
    def cmp(x: Package, y: Package) -> int:
        if x.format == "rpm":
            result = compare_rpm(x.evr, y.evr)
        elif x.format == "deb":
            result = compare_deb(x.evr, y.evr)
        else:
            result = 0
        return result or (x.id > y.id) - (x.id < y.id)
    return sorted(rows, key=functools.cmp_to_key(cmp), reverse=True)


# ———————————————————————————— Reading packages ————————————————————————————

def read_rpm(path: Path) -> dict:
    out = repo.run(["rpm", "-qp", "--nosignature", "--nodigest", "--qf",
                    "%{NAME}|%{EPOCHNUM}|%{VERSION}|%{RELEASE}|%{ARCH}|%{SOURCERPM}|%{SUMMARY}", str(path)],
                   stdout_only=True)
    parts = out.strip().split("|", 6)
    if len(parts) != 7:
        raise RepoError("rpm couldn't read the package's name and version.")
    name, epoch, version, release, arch, source, summary = parts
    if source == "(none)":
        raise RepoError("This is a source RPM. Upload the built package (.x86_64.rpm, .noarch.rpm…).")
    evr = f"{version}-{release}" if epoch in ("", "0") else f"{epoch}:{version}-{release}"
    return {"name": name, "version": version, "evr": evr, "arch": arch, "summary": summary[:500], "control": "",
            "filename": f"{name}-{version}-{release}.{arch}.rpm"}


def read_deb(path: Path) -> dict:
    control = repo.run(["dpkg-deb", "--field", str(path)], stdout_only=True).strip("\n")
    fields = {}
    for line in control.splitlines():
        if line and not line[0].isspace() and ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip().lower()] = value.strip()
    name, full, arch = fields.get("package", ""), fields.get("version", ""), fields.get("architecture", "")
    if not (name and full and arch):
        raise RepoError("The package's control file has no Package, Version or Architecture.")
    # What people call the version: no epoch, no Debian revision.
    upstream = full.split(":", 1)[-1]
    upstream = upstream.rsplit("-", 1)[0] if "-" in upstream else upstream
    return {"name": name, "version": upstream, "evr": full, "arch": arch,
            "summary": fields.get("description", "")[:500], "control": control,
            "filename": f"{name}_{full.split(':', 1)[-1]}_{arch}.deb"}


def guess_arch(filename: str) -> str:
    match = re.search(r"(x86[_-]64|amd64|aarch64|arm64|armhf|armv7l|i[3-6]86|noarch)", filename.lower())
    if not match:
        return ""
    return {"amd64": "x86_64", "x86-64": "x86_64", "arm64": "aarch64"}.get(match.group(1), match.group(1))


def guess_version(filename: str) -> str:
    match = re.search(r"\d+(?:\.\d+)+(?:-(?:alpha|beta|rc)(?:\.?\d+)?)?", filename)
    return match.group(0) if match else ""


# ———————————————————————————— Signing ————————————————————————————

def _fpr() -> str:
    fpr = repo.key_fingerprint()
    if not fpr:
        raise RepoError("The repository has no signing key. Create one under Signing and addresses.")
    return fpr


def sign_rpm(path: Path, log: list) -> None:
    """Sign the package itself with the repository's key, replacing any
    signature it came with, so the one key in the .repo file covers it."""
    repo.run(["rpmsign", "--addsign", "--define", f"_gpg_name {_fpr()}", "--define", f"_gpg_path {repo.gnupg_dir()}",
              "--define", "_gpg_sign_cmd_extra_args --batch --pinentry-mode loopback", str(path)], log)


def _gpg_sign(src: Path, dest: Path, log: list, clear: bool = False) -> None:
    """A signature written beside the final name, then moved over it, so a
    client never reads half a file."""
    tmp = dest.with_name(dest.name + ".tmp")
    repo.run(["gpg", "--batch", "--yes", "--pinentry-mode", "loopback", "--passphrase", "",
              "--local-user", _fpr(), "--digest-algo", "SHA512",
              *(["--clearsign"] if clear else ["--armor", "--detach-sign"]),
              "--output", str(tmp), str(src)], log)
    os.replace(tmp, dest)


def _hash(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return path.stat().st_size, digest.hexdigest()


# ———————————————————————————— Publishing ————————————————————————————

def _target(pkg: Package, filename: str) -> str:
    """Where a package lives, relative to the packages directory."""
    if pkg.format == "rpm":
        return f"rpm/{pkg.channel}/packages/{filename}"
    if pkg.format == "deb":
        return f"deb/pool/{pkg.channel}/{filename}"
    return f"files/{pkg.channel}/{pkg.id}/{filename}"


def _clash(pkg: Package) -> None:
    if pkg.format == "file":
        return
    other = Package.query.filter(Package.id != pkg.id, Package.format == pkg.format, Package.channel == pkg.channel,
                                 Package.name == pkg.name, Package.arch == pkg.arch, Package.evr == pkg.evr,
                                 Package.status.in_(IN_REPO)).first()
    if other:
        raise RepoError(f"{pkg.name} {pkg.evr} ({pkg.arch}) is already in the {pkg.channel} repository. "
                        "Build it with a new version or release number, or withdraw the one there first.")


def _place(pkg: Package, src: Path, relpath: str, link: bool = False) -> None:
    dest = root() / relpath
    dest.parent.mkdir(parents=True, exist_ok=True)
    # A file left by a job that failed half way: no row claims it (_clash).
    dest.unlink(missing_ok=True)
    if link:
        try:
            os.link(src, dest)
        except OSError:
            shutil.copy2(src, dest)
    else:
        shutil.move(str(src), str(dest))
    pkg.path = relpath


def _remove_file(pkg: Package) -> None:
    if pkg.path:
        path = root() / pkg.path
        path.unlink(missing_ok=True)
        if pkg.format == "file":
            try:
                path.parent.rmdir()   # files/<channel>/<id>/
            except OSError:
                pass


def publish_upload(pkg: Package, src: Path, original: str, log: list) -> None:
    """Read, sign and place an uploaded file, then make it live (or not, if
    a newer version is already there) and reindex its channel."""
    fmt = sniff(src, original)
    if fmt == "flatpak":
        raise RepoError("This is a Flatpak bundle. Upload it under Releases instead.")
    problem = not_ready(fmt)
    if problem:
        raise RepoError(problem)
    pkg.format = fmt
    if fmt == "rpm":
        info = read_rpm(src)
    elif fmt == "deb":
        info = read_deb(src)
    else:
        name = pkg.name or original
        if not NAME_RE.match(name or ""):
            raise RepoError("Give the file a name of letters, digits, dots, dashes and underscores, "
                            "such as App-x86_64.AppImage.")
        # A fixed download name usually drops the version; the uploaded
        # file's own name may still say it.
        info = {"name": name, "version": guess_version(original) or guess_version(name), "evr": "",
                "arch": guess_arch(name) or guess_arch(original), "summary": "", "control": "", "filename": name}
    pkg.name, pkg.evr, pkg.summary, pkg.control = info["name"], info["evr"], info["summary"], info["control"]
    pkg.version = pkg.version or info["version"] or utcnow().strftime("%Y.%m.%d")
    pkg.arch = pkg.arch if fmt == "file" and pkg.arch else info["arch"]
    if not NAME_RE.match(info["filename"]):
        raise RepoError(f"{info['filename']} isn't a name Flatout can serve.")
    _clash(pkg)
    if fmt == "rpm":
        sign_rpm(src, log)
    _place(pkg, src, _target(pkg, info["filename"]))
    pkg.size, pkg.sha256 = _hash(root() / pkg.path)
    pkg.published_at = utcnow()
    pkg.status = "superseded"   # in the repository; settle says whether it is the newest
    pkg.error = ""
    log.append(f"Added {pkg.path} ({FORMAT_NAMES[fmt]}, {pkg.name} {pkg.evr or pkg.version}, "
               f"{pkg.arch or 'any architecture'}).")
    settle(pkg, log)
    index(pkg.format, pkg.channel, log)


def publish_copy(pkg: Package, log: list) -> None:
    """Promotion: the source's file, in this row's channel. Hard-linked, so
    a promoted package takes no more disk."""
    source = db.session.get(Package, pkg.origin_package_id)
    if source is None or source.status not in IN_REPO or not source.path or not (root() / source.path).exists():
        raise RepoError("The package to copy is no longer published.")
    problem = not_ready(source.format)
    if problem:
        raise RepoError(problem)
    for field in ("format", "name", "evr", "arch", "summary", "control", "size", "sha256"):
        setattr(pkg, field, getattr(source, field))
    pkg.version = pkg.version or source.version
    _clash(pkg)
    _place(pkg, root() / source.path, _target(pkg, Path(source.path).name), link=True)
    pkg.published_at = utcnow()
    pkg.status = "superseded"
    log.append(f"Copied {source.path} to {pkg.path}.")
    settle(pkg, log)
    index(pkg.format, pkg.channel, log)


def withdraw(pkg: Package, log: list) -> None:
    if pkg.status not in IN_REPO:
        raise RepoError("Only a published package can be withdrawn.")
    was_live = pkg.status == "live"
    _remove_file(pkg)
    pkg.status = "withdrawn"
    log.append(f"Withdrew {pkg.path}.")
    head = settle(pkg, log)
    index(pkg.format, pkg.channel, log)
    if was_live and pkg.format != "file":
        tool = "dnf" if pkg.format == "rpm" else "apt"
        log.append(f"Installed copies keep {pkg.version} until a newer version is published; {tool} doesn't "
                   "go back to an older one on its own." + (f" New installs get {head.version}." if head else ""))


def settle(pkg: Package, log: list) -> Package | None:
    """Rank one package's versions on its channel: the newest is live, the
    next few stay for a downgrade, the rest are pruned. Returns the live one."""
    query = Package.query.filter(Package.format == pkg.format, Package.channel == pkg.channel,
                                 Package.name == pkg.name, Package.status.in_(IN_REPO))
    if pkg.format != "file":
        query = query.filter(Package.arch == pkg.arch)
    rows = newest_first(query.all())
    kept = int_setting("packages_kept", KEPT)
    for i, row in enumerate(rows):
        if i == 0:
            if row.status != "live" and row.id != pkg.id:
                log.append(f"{row.name} {row.version} ({row.arch or 'any'}) is the newest on {row.channel} now.")
            row.status = "live"
        elif i < kept:
            if row.id == pkg.id and pkg.status == "superseded" and pkg.origin == "upload":
                log.append(f"{rows[0].version} is newer and stays the one installed; {row.version} is there for "
                           "anyone who asks for it by version.")
            row.status = "superseded"
        else:
            _remove_file(row)
            row.status = "pruned"
            log.append(f"Pruned {row.path}: past the {kept} versions kept.")
    db.session.commit()
    return rows[0] if rows else None


# ———————————————————————————— Indexes ————————————————————————————

def index(fmt: str, channel: str, log: list) -> None:
    if fmt == "rpm":
        index_rpm(channel, log)
    elif fmt == "deb":
        index_deb(channel, log)


def index_rpm(channel: str, log: list) -> None:
    base = rpm_dir() / channel
    (base / "packages").mkdir(parents=True, exist_ok=True)
    # Gzip, which every dnf and yum reads; old index files linger for a day,
    # for a client that read the previous repomd.xml a moment ago.
    repo.run(["createrepo_c", "--quiet", "--update", "--no-database", "--general-compress-type=gz",
              "--retain-old-md-by-age=1d", str(base)], log)
    _gpg_sign(base / "repodata" / "repomd.xml", base / "repodata" / "repomd.xml.asc", log)
    log.append(f"Signed the {channel} dnf repository's index.")


def _title() -> str:
    from . import site
    return site.get("live")["app"]["name"] or "Flatout"


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def index_deb(channel: str, log: list) -> None:
    """Packages and Release are plain text, written here from what each
    package's control file said when it was uploaded; only signing needs a
    tool. Each index is also kept under by-hash/ with a few before it."""
    rows = Package.query.filter(Package.format == "deb", Package.channel == channel,
                                Package.status.in_(IN_REPO)).all()
    rows.sort(key=lambda r: (r.name, r.arch))
    arches = sorted({r.arch for r in rows} - {"all"}) or ["amd64", "arm64"]
    dist = deb_dir() / "dists" / channel
    entries = []
    for arch in arches:
        stanzas = [f"{r.control}\nFilename: {r.path[len('deb/'):]}\nSize: {r.size}\nSHA256: {r.sha256}\n"
                   for r in rows if r.arch in (arch, "all")]
        text = "\n".join(stanzas).encode()
        for name, data in (("Packages", text), ("Packages.gz", gzip.compress(text, mtime=0))):
            rel = f"main/binary-{arch}/{name}"
            digest = hashlib.sha256(data).hexdigest()
            by_hash = dist / f"main/binary-{arch}/by-hash/SHA256"
            _write(by_hash / digest, data)
            _write(dist / rel, data)
            entries.append((digest, len(data), rel))
            older = sorted((p for p in by_hash.iterdir() if p.name != digest and not p.name.endswith(".tmp")),
                           key=lambda p: p.stat().st_mtime, reverse=True)
            for stale in older[BY_HASH_KEPT * 2:]:   # Packages and Packages.gz share the folder
                stale.unlink(missing_ok=True)
    title = re.sub(r"[\r\n]", " ", _title())
    release = "\n".join([
        f"Origin: {title}", f"Label: {title}", f"Suite: {channel}", f"Codename: {channel}",
        "Date: " + datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S UTC"),
        f"Architectures: {' '.join(arches)}", "Components: main", "Acquire-By-Hash: yes",
        "SHA256:", *(f" {d} {size:>16} {rel}" for d, size, rel in entries),
    ]) + "\n"
    staged = dist / "Release.new"
    _write(staged, release.encode())
    _gpg_sign(staged, dist / "InRelease", log, clear=True)
    _gpg_sign(staged, dist / "Release.gpg", log)
    os.replace(staged, dist / "Release")
    log.append(f"Signed the {channel} apt suite: {len(rows)} package{'s' if len(rows) != 1 else ''} "
               f"for {', '.join(arches)}.")


def index_all(log: list, resign: bool = False) -> None:
    """Rewrite and sign every index, after the signing key changed. With
    ``resign``, the RPMs are signed again too, so the new key covers them."""
    if resign and tools()["rpm"]:
        for pkg in Package.query.filter(Package.format == "rpm", Package.status.in_(IN_REPO)):
            path = root() / (pkg.path or "")
            if path.exists():
                sign_rpm(path, log)
                pkg.size, pkg.sha256 = _hash(path)
        db.session.commit()
    for fmt in ("rpm", "deb"):
        if not tools()[fmt]:
            continue
        channels = {c for (c,) in db.session.query(Package.channel).filter(Package.format == fmt).distinct()}
        for channel in sorted(channels):
            index(fmt, channel, log)


def any_published() -> bool:
    return db.session.query(Package.id).filter(Package.format.in_(("rpm", "deb"))).first() is not None


# ———————————————————————————— What the site reads ————————————————————————————

def download_name(pkg: Package) -> str:
    """The address under /download/ that always gives the newest of a
    package, for a link that never needs changing."""
    beta = "-beta" if pkg.channel == "beta" else ""
    if pkg.format == "file":
        return f"beta/{pkg.name}" if beta else pkg.name
    return f"{pkg.name}{beta}-{pkg.arch}.{pkg.format}"


def find_download(name: str) -> Package | None:
    """The live package a /download/ address names, or None."""
    live = Package.query.filter(Package.status == "live")
    if name.startswith("beta/"):
        return live.filter(Package.format == "file", Package.channel == "beta", Package.name == name[5:]).first()
    found = live.filter(Package.format == "file", Package.channel == "stable", Package.name == name).first()
    if found:
        return found
    stem, dot, fmt = name.rpartition(".")
    if not dot or fmt not in ("rpm", "deb"):
        return None
    pkg_name, _, arch = stem.rpartition("-")
    found = live.filter(Package.format == fmt, Package.channel == "stable", Package.name == pkg_name,
                        Package.arch == arch).first()
    if found is None and pkg_name.endswith("-beta"):
        found = live.filter(Package.format == fmt, Package.channel == "beta", Package.name == pkg_name[:-5],
                            Package.arch == arch).first()
    return found


def _arch_key(arch: str) -> tuple:
    return (arch not in ("x86_64", "amd64"), arch)


def _summary(rows: list[Package], base: str) -> dict | None:
    """One format on one channel: the main package (the one uploaded most
    recently, when a repository holds several) and its newest version."""
    if not rows:
        return None
    newest = max(rows, key=lambda r: (r.published_at or r.created_at, r.id))
    main = [r for r in rows if r.name == newest.name]
    return {
        "name": newest.name,
        "names": sorted({r.name for r in rows}),
        "version": newest.version,
        "published_at": (newest.published_at or newest.created_at).isoformat() + "Z",
        "arches": sorted({r.arch for r in main}, key=_arch_key),
        "downloads": {r.arch: f"{base}/download/{download_name(r)}" for r in sorted(main, key=lambda r: _arch_key(r.arch))},
    }


def public_info(base: str, remote: str) -> dict:
    live = Package.query.filter(Package.status == "live").order_by(Package.id).all()

    def rows(fmt, channel):
        return [r for r in live if r.format == fmt and r.channel == channel]

    def files(channel):
        return [{"name": r.name, "version": r.version, "arch": r.arch, "size": r.size,
                 "url": f"{base}/download/{download_name(r)}",
                 "published_at": (r.published_at or r.created_at).isoformat() + "Z"}
                for r in sorted(rows("file", channel), key=lambda r: (_arch_key(r.arch), r.name))]

    return {
        "rpm": {
            "stable": _summary(rows("rpm", "stable"), base), "beta": _summary(rows("rpm", "beta"), base),
            "repo_url": f"{base}/rpm/stable/", "beta_repo_url": f"{base}/rpm/beta/",
            "repo_file_url": f"{base}/rpm/{remote}.repo", "beta_repo_file_url": f"{base}/rpm/{remote}-beta.repo",
            "key_url": f"{base}/rpm/{remote}.asc",
        },
        "deb": {
            "stable": _summary(rows("deb", "stable"), base), "beta": _summary(rows("deb", "beta"), base),
            "repo_url": f"{base}/deb",
            "sources_url": f"{base}/deb/{remote}.sources", "beta_sources_url": f"{base}/deb/{remote}-beta.sources",
            "key_url": f"{base}/deb/{remote}.gpg",
        },
        "files": {"stable": files("stable"), "beta": files("beta")},
    }
