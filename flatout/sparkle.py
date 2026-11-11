"""macOS apps and Sparkle: what an upload says about itself, the key that
signs updates, and the appcast a Mac app checks.

Sparkle (https://sparkle-project.org) is how most Mac apps outside the App
Store update themselves. The app names an appcast, an RSS feed, in its
Info.plist (SUFeedURL), and the public half of an EdDSA key (SUPublicEDKey).
Each item in the feed gives a version and a download, with the Ed25519
signature of the download's bytes; Sparkle installs the newest item it can
run whose signature holds.

Flatout keeps the key (DATA_DIR/sparkle/), signs every Mac upload with it,
and writes the appcast on each request from what is published:

    /sparkle/appcast.xml        the stable channel
    /sparkle/appcast-beta.xml   the stable and beta channels together, for a
                                beta build, or an app with a "get betas"
                                setting that switches its feed

The beta feed carries both channels so a beta tester moves on to the final
release when it is newer. The stable feed carries no beta items at all, not
even under <sparkle:channel>: Sparkle 1 ignores that element and would offer
them to everyone.

What an upload is comes from inside it: the app's Info.plist (its build
number, CFBundleVersion, which Sparkle compares, the version people read,
CFBundleShortVersionString, and LSMinimumSystemVersion) and its executable's
architectures. A .zip or a tarball is read here; a .pkg is a xar archive,
read here too; a .dmg needs 7-Zip. Whatever can't be read can be given with
the upload.
"""
from __future__ import annotations

import base64
import bz2
import mmap
import os
import plistlib
import re
import shutil
import struct
import subprocess
import tarfile
import zipfile
import zlib
from datetime import timezone
from email.utils import format_datetime
from pathlib import Path
from xml.etree import ElementTree
from xml.sax.saxutils import escape, quoteattr

from flask import current_app

from .models import end_worker_transaction
from .repo import RepoError

NS = "http://www.andymatuschak.org/xml-namespaces/sparkle"
FEEDS = {"stable": "appcast.xml", "beta": "appcast-beta.xml"}
# The archives Sparkle installs from, by the ending of their name.
EXTENSIONS = (".dmg", ".zip", ".pkg", ".mpkg", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz", ".tar.xz", ".txz")
# Of two uploads of one build for one machine, the one the appcast offers:
# an archive Sparkle unpacks, before a disk image, before an installer.
PREFERENCE = {"zip": 0, "tar": 1, "dmg": 2, "pkg": 3}
ARCHES = ("universal", "arm64", "x86_64")
ARCH_NAMES = {"universal": "Apple silicon and Intel", "arm64": "Apple silicon", "x86_64": "Intel"}
BUILD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,59}$")
SYSTEM_RE = re.compile(r"^\d{1,2}(\.\d{1,3}){0,2}$")
PLIST_MAX = 4 << 20       # an Info.plist bigger than this is no Info.plist
TOC_MAX = 64 << 20        # a xar archive's table of contents, unpacked
HEAD = 4096               # enough of an executable to tell its architectures
TIMEOUT = 600


# ———————————————————————————— The key ————————————————————————————

def key_path() -> Path:
    return Path(current_app.config["DATA_DIR"]) / "sparkle" / "ed25519.key"


def _private():
    """The private key, or None. It is kept as Sparkle's generate_keys -x
    writes it: the 32-byte seed, in base64."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    try:
        seed = base64.b64decode(key_path().read_text().strip(), validate=True)
    except (OSError, ValueError):
        return None
    return Ed25519PrivateKey.from_private_bytes(seed) if len(seed) == 32 else None


def has_key() -> bool:
    return _private() is not None


def public_key() -> str:
    """What goes in the app's Info.plist as SUPublicEDKey: the 32-byte public
    key in base64, as generate_keys prints it. Empty without a key."""
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    key = _private()
    if key is None:
        return ""
    return base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()


def secret_key() -> str:
    """The private key as generate_keys -x writes it, which generate_keys -f
    takes back into a Mac's keychain."""
    if not has_key():
        raise RepoError("There is no Sparkle key yet.")
    return key_path().read_text().strip()


def _store(seed: bytes) -> str:
    path = key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(base64.b64encode(seed).decode() + "\n")
    os.replace(tmp, path)
    return public_key()


def generate() -> str:
    """A new key; returns its public half."""
    return _store(os.urandom(32))


def import_key(text: str) -> str:
    """A key exported with Sparkle's generate_keys -x: the seed in base64.
    Returns its public half."""
    text = re.sub(r"\s+", "", text or "")
    try:
        seed = base64.b64decode(text, validate=True)
    except ValueError:
        seed = b""
    if len(seed) == 96:
        raise RepoError("This is a key in Sparkle's older format, which only Sparkle's own tools can sign with. "
                        "Make a new key here, and put its public key in the next version of the app.")
    if len(seed) != 32:
        raise RepoError("That isn't a Sparkle private key. Export one with Sparkle's generate_keys -x key.txt "
                        "and paste what the file holds.")
    return _store(seed)


def ensure_key(log: list) -> None:
    if not has_key():
        log.append("Made a Sparkle signing key. Put its public key in the app's Info.plist as SUPublicEDKey "
                   "(Repository > Signing and addresses has it), or import the key the app already trusts there; "
                   "every Mac build published is then signed again with that one.")
        generate()


def sign(path: Path) -> str:
    """The EdDSA signature Sparkle checks: Ed25519 over the whole file, in
    base64, as sign_update writes it. The file is mapped rather than read,
    so a large disk image doesn't sit in memory."""
    key = _private()
    if key is None:
        raise RepoError("There is no Sparkle key to sign with.")
    with open(path, "rb") as f:
        if not os.fstat(f.fileno()).st_size:
            raise RepoError("The file is empty.")
        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as data:
            return base64.b64encode(key.sign(data)).decode()


def verify(path: Path, signature: str, public: str) -> bool:
    """Whether a signature holds for a file under a public key, as Sparkle
    checks it. For the tests, and for anyone checking by hand."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public))
    with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as data:
        try:
            key.verify(base64.b64decode(signature), data)
            return True
        except InvalidSignature:
            return False


# ———————————————————————————— Versions ————————————————————————————

def _char_type(c: str) -> int:
    """Sparkle's SUStandardVersionComparator: 0 a digit, 1 a letter (anything
    else), 2 a separator (a dot, a space or punctuation)."""
    if "0" <= c <= "9":
        return 0
    if c == "." or c.isspace() or (c.isascii() and not c.isalnum()):
        return 2
    return 1


def _parts(version: str) -> list[str]:
    parts: list[str] = []
    last = -1
    for c in version:
        kind = _char_type(c)
        if parts and kind == last and kind != 2:
            parts[-1] += c
        else:
            parts.append(c)
        last = kind
    return parts


def compare(a: str, b: str) -> int:
    """Sparkle's own comparison of two versions, ported from
    SUStandardVersionComparator: runs of digits by value, runs of letters as
    text, a number newer than letters, and 1.0 newer than 1.0b1 but older
    than 1.0.1. 1 if a is newer, -1 if b is, 0 if they are the same."""
    pa, pb = _parts(a), _parts(b)
    for x, y in zip(pa, pb):
        tx, ty = _char_type(x[0]), _char_type(y[0])
        if tx == ty:
            if tx == 0 and int(x) != int(y):
                return 1 if int(x) > int(y) else -1
            if tx == 1 and x != y:
                return 1 if x > y else -1
        elif tx != 1 and ty == 1:
            return 1
        elif tx == 1 and ty != 1:
            return -1
        else:
            return 1 if tx == 0 else -1
    if len(pa) == len(pb):
        return 0
    longer = 1 if len(pa) > len(pb) else -1
    extra = (pa if longer == 1 else pb)[min(len(pa), len(pb))]
    # More letters make it a pre-release of the shorter one; more numbers, a later version.
    return -longer if _char_type(extra[0]) == 1 else longer


# ———————————————————————————— What a file is ————————————————————————————

def _ext(name: str) -> str:
    low = name.lower()
    return next((e for e in sorted(EXTENSIONS, key=len, reverse=True) if low.endswith(e)), "")


def kind_of(name: str) -> str:
    """zip, tar, dmg or pkg, by a file's name."""
    ext = _ext(name)
    if ext in (".pkg", ".mpkg"):
        return "pkg"
    if ext == ".dmg":
        return "dmg"
    if ext == ".zip":
        return "zip"
    return "tar" if ext else ""


def _app_plist(name: str) -> str | None:
    """The app a path's Info.plist belongs to, if it is one: X.app/Contents/Info.plist."""
    match = re.match(r"^(?:\./)?((?:[^/]+/)*?[^/]+\.app)/Contents/Info\.plist$", name)
    if not match or "__MACOSX/" in name:
        return None
    return match.group(1)


def _depth(app: str) -> int:
    return app.count(".app/") * 100 + app.count("/")


def sniff(path: Path, filename: str = "") -> str:
    """The kind of Mac upload a file is (zip, tar, dmg or pkg), or "" for
    anything else. A disk image and an installer say so in their bytes; a
    zip or a tarball is a Mac app when an .app (or, for a zip, an installer)
    is inside it."""
    with open(path, "rb") as f:
        head = f.read(8)
        f.seek(0, os.SEEK_END)
        size = f.tell()
        if size >= 512:
            f.seek(size - 512)
            if f.read(4) == b"koly":
                return "dmg"
    if head[:4] == b"xar!" and kind_of(filename) == "pkg":
        return "pkg"
    if head[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(path) as z:
                for name in z.namelist():
                    if _app_plist(name) or re.match(r"^[^/]+\.m?pkg$", name, re.I):
                        return "zip"
        except (zipfile.BadZipFile, OSError):
            return ""
        return ""
    if head[:2] == b"\x1f\x8b" or head[:3] == b"BZh" or head[:6] == b"\xfd7zXZ\x00" or kind_of(filename) == "tar":
        try:
            with tarfile.open(path, "r|*") as tar:
                for i, member in enumerate(tar):
                    if ".app/" in member.name + "/" and re.search(r"(^|/)[^/]+\.app(/|$)", member.name):
                        return "tar"
                    if i > 40:
                        break
        except (tarfile.TarError, OSError, EOFError, zlib.error, ValueError):
            return ""
    return ""


def _macho_arches(head: bytes) -> set[str]:
    """The architectures in a Mach-O executable's first bytes: a universal
    binary lists them in its header, a thin one names its own."""
    names = {0x01000007: "x86_64", 0x0100000C: "arm64", 7: "i386", 12: "arm"}
    if len(head) < 8:
        return set()
    magic = struct.unpack(">I", head[:4])[0]
    if magic in (0xCAFEBABE, 0xCAFEBABF):
        count = struct.unpack(">I", head[4:8])[0]
        step = 20 if magic == 0xCAFEBABE else 32
        out = set()
        for i in range(min(count, 16)):
            at = 8 + i * step
            if at + 4 > len(head):
                break
            out.add(names.get(struct.unpack(">I", head[at:at + 4])[0], ""))
        return out - {""}
    if head[:4] in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe"):
        return {names.get(struct.unpack("<I", head[4:8])[0], "")} - {""}
    return set()


def _arch(arches: set[str]) -> str:
    if {"arm64", "x86_64"} <= arches:
        return "universal"
    if "arm64" in arches:
        return "arm64"
    if "x86_64" in arches:
        return "x86_64"
    return ""


def _from_plist(data: bytes) -> dict:
    try:
        plist = plistlib.loads(data)
    except Exception:   # plistlib raises several kinds for a damaged file
        return {}
    if not isinstance(plist, dict):
        return {}

    def get(key):
        value = plist.get(key)
        return str(value).strip() if isinstance(value, (str, int, float)) else ""

    return {"bundle_version": get("CFBundleVersion"), "short_version": get("CFBundleShortVersionString"),
            "minimum_system_version": get("LSMinimumSystemVersion"), "bundle_id": get("CFBundleIdentifier"),
            "executable": get("CFBundleExecutable"),
            "name": get("CFBundleDisplayName") or get("CFBundleName")}


def _read_zip(path: Path) -> dict:
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        apps = sorted((a for a in map(_app_plist, names) if a), key=_depth)
        if not apps:
            pkgs = [n for n in names if re.match(r"^[^/]+\.m?pkg$", n, re.I)]
            if pkgs:
                with z.open(pkgs[0]) as f:
                    return dict(_read_xar(f), installer=True)
            return {}
        app = apps[0]
        info = z.getinfo(f"{app}/Contents/Info.plist")
        if info.file_size > PLIST_MAX:
            return {}
        out = _from_plist(z.read(info))
        exe = f"{app}/Contents/MacOS/{out.get('executable', '')}"
        if out.get("executable") and exe in names:
            with z.open(exe) as f:
                out["arch"] = _arch(_macho_arches(f.read(HEAD)))
        return out


def _read_tar(path: Path) -> dict:
    """One pass over the tarball, in whatever order it was written: every
    app's Info.plist and the start of every executable, then the outermost
    app's."""
    plists, heads = {}, {}
    with tarfile.open(path, "r|*") as tar:
        for member in tar:
            if not member.isfile():
                continue
            name = member.name[2:] if member.name.startswith("./") else member.name
            app = _app_plist(name)
            if app and member.size <= PLIST_MAX:
                plists[app] = tar.extractfile(member).read()
            elif "/Contents/MacOS/" in name and len(heads) < 200:
                heads[name] = tar.extractfile(member).read(HEAD)
    if not plists:
        return {}
    app = min(plists, key=_depth)
    out = _from_plist(plists[app])
    head = heads.get(f"{app}/Contents/MacOS/{out.get('executable', '')}")
    if head:
        out["arch"] = _arch(_macho_arches(head))
    return out


# — An installer package: a xar archive, its table of contents zlib-packed XML.

def _xar_files(toc: ElementTree.Element, prefix: str = ""):
    for f in toc.findall("file"):
        name = prefix + (f.findtext("name") or "")
        yield name, f
        yield from _xar_files(f, name + "/")


def _xar_data(fh, heap: int, f: ElementTree.Element) -> bytes:
    data = f.find("data")
    if data is None:
        return b""
    length, offset = int(data.findtext("length") or 0), int(data.findtext("offset") or 0)
    if length > PLIST_MAX:
        return b""
    fh.seek(heap + offset)
    raw = fh.read(length)
    encoding = data.find("encoding")
    style = (encoding.get("style") if encoding is not None else "") or ""
    if "gzip" in style:
        return _inflate(raw, PLIST_MAX)
    if "bzip2" in style:
        return bz2.BZ2Decompressor().decompress(raw, PLIST_MAX)
    return raw


def _inflate(raw: bytes, limit: int) -> bytes:
    """zlib, but never more than ``limit`` bytes out, whatever the archive
    claims: a small file can unpack to a huge one."""
    return zlib.decompressobj().decompress(raw, limit)


def _read_xar(fh) -> dict:
    """An installer package's versions: the app it installs, from the
    component's PackageInfo, and the oldest macOS it allows, from the
    Distribution file of a product archive."""
    head = fh.read(28)
    if len(head) < 28 or head[:4] != b"xar!":
        return {}
    size, _version, packed, unpacked = struct.unpack(">HHQQ", head[4:24])
    if unpacked > TOC_MAX or packed > TOC_MAX:
        return {}
    fh.seek(size)
    toc = ElementTree.fromstring(_inflate(fh.read(packed), TOC_MAX)).find("toc")
    if toc is None:
        return {}
    heap = size + packed
    out: dict = {"installer": True}
    bundles, pkg_version = [], ""
    for name, f in _xar_files(toc):
        leaf = name.rsplit("/", 1)[-1]
        if leaf not in ("PackageInfo", "Distribution"):
            continue
        try:
            doc = ElementTree.fromstring(_xar_data(fh, heap, f) or b"<x/>")
        except (ElementTree.ParseError, zlib.error, OSError, ValueError):
            continue
        if leaf == "Distribution":
            for os_version in doc.iter("os-version"):
                if os_version.get("min"):
                    out["minimum_system_version"] = os_version.get("min")
                    break
            for opts in doc.iter("options"):
                hosts = set((opts.get("hostArchitectures") or "").replace(" ", "").split(",")) - {""}
                if hosts:
                    out["arch"] = _arch(hosts)
            product = doc.find("product")
            if product is not None and product.get("version"):
                pkg_version = pkg_version or product.get("version")
        else:
            pkg_version = pkg_version or doc.get("version") or ""
            for b in doc.iter("bundle"):
                path = (b.get("path") or "").lstrip("./")
                if path.endswith(".app") and b.get("CFBundleVersion"):
                    bundles.append((_depth(path + "/"), b))
    if bundles:
        b = min(bundles, key=lambda x: x[0])[1]
        out.update(bundle_version=b.get("CFBundleVersion") or "",
                   short_version=b.get("CFBundleShortVersionString") or "", bundle_id=b.get("id") or "")
    elif pkg_version:
        out.update(bundle_version=pkg_version, short_version=pkg_version)
    return out


# — A disk image: 7-Zip reads its file system, HFS+ or APFS.

def seven_zip() -> str | None:
    return shutil.which("7zz") or shutil.which("7z")


def _7z(args: list[str], limit: int | None = None) -> bytes:
    """7-Zip's output; with ``limit``, only that much of it, and 7-Zip is
    stopped there (an executable can be large). -spd: names are names, never
    wildcards."""
    end_worker_transaction()
    proc = subprocess.Popen([seven_zip(), *args], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        if limit is None:
            out, _ = proc.communicate(timeout=TIMEOUT)
            return out if proc.returncode == 0 else b""
        out = proc.stdout.read(limit)
        return out
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def _read_dmg(path: Path) -> dict:
    if not seven_zip():
        return {"problem": "7-Zip isn't installed here, so a disk image can't be opened"}
    listing = _7z(["l", "-slt", "-ba", "-spd", "--", str(path)]).decode("utf-8", "replace")
    names = [line[7:] for line in listing.splitlines() if line.startswith("Path = ")]
    apps = sorted({a for a in map(_app_plist, names) if a}, key=_depth)
    if not apps:
        pkgs = [n for n in names if re.match(r"^(?:[^/]+/)?[^/]+\.m?pkg$", n, re.I)]
        if pkgs:
            return {"problem": "the disk image holds an installer package; upload the .pkg itself"}
        return {"problem": "7-Zip found no app in the disk image"}
    app = apps[0]
    out = _from_plist(_7z(["e", "-so", "-spd", "--", str(path), f"{app}/Contents/Info.plist"]))
    if out.get("executable"):
        head = _7z(["e", "-so", "-spd", "--", str(path), f"{app}/Contents/MacOS/{out['executable']}"], limit=HEAD)
        out["arch"] = _arch(_macho_arches(head))
    return out


def read(path: Path, kind: str) -> dict:
    """What a Mac upload says about itself. Keys, each possibly empty:
    bundle_version, short_version, minimum_system_version, bundle_id, name,
    arch (universal, arm64 or x86_64), installer (an installer package), and
    problem: why the versions couldn't be read."""
    try:
        if kind == "zip":
            out = _read_zip(path)
        elif kind == "tar":
            out = _read_tar(path)
        elif kind == "pkg":
            with open(path, "rb") as f:
                out = _read_xar(f)
        elif kind == "dmg":
            out = _read_dmg(path)
        else:
            out = {}
    except (OSError, ValueError, EOFError, zipfile.BadZipFile, tarfile.TarError, zlib.error,
            ElementTree.ParseError, struct.error) as err:
        out = {"problem": f"it couldn't be read ({err})"}
    if not out.get("bundle_version") and not out.get("problem"):
        out["problem"] = "it holds no Info.plist with a CFBundleVersion"
    return out


def mac_arch(given: str) -> str:
    """An architecture as the appcast names it, from what an upload or its
    name says: universal, arm64 or x86_64, or "" for unknown."""
    low = (given or "").lower()
    if re.search(r"universal", low):
        return "universal"
    if re.search(r"arm64|aarch64|apple.?silicon", low):
        return "arm64"
    if re.search(r"x86[_-]?64|amd64|intel", low):
        return "x86_64"
    return ""


def download_name(filename: str, version: str, arch: str) -> str:
    """A Mac upload's download name: its file name without the version and
    with any character a URL would have to escape made a dash, and the
    architecture added for a build that runs on only one kind of Mac.
    My App 1.2.0.dmg becomes My-App.dmg."""
    ext = _ext(filename)
    stem = filename[:-len(ext)] if ext else filename
    if version:
        stem = re.sub(r"[-_. ]?[vV]?" + re.escape(version), "", stem, count=1)
    stem = re.sub(r"[^A-Za-z0-9._+~-]+", "-", stem).strip("-._") or "App"
    if arch in ("arm64", "x86_64") and not mac_arch(stem):
        stem = f"{stem}-{arch}"
    return stem + ext.lower()


def served_name(filename: str) -> str:
    """The file's own name, the version kept, made safe for a URL."""
    ext = _ext(filename)
    stem = filename[:-len(ext)] if ext else filename
    return (re.sub(r"[^A-Za-z0-9._+~-]+", "-", stem).strip("-._") or "App") + ext.lower()


# ———————————————————————————— The appcast ————————————————————————————

def feed_url(base: str, channel: str = "stable") -> str:
    return f"{base}/sparkle/{FEEDS[channel]}"


def items(channels: tuple[str, ...]) -> list:
    """The published Mac builds the feed offers, newest first: of one build
    number for one kind of Mac, a single file, preferring an archive Sparkle
    unpacks; for the same build, the Apple silicon one first, since Sparkle
    takes the first of equal versions it can run and an Intel Mac skips it."""
    import functools
    from .models import Package
    rows = Package.query.filter(Package.format == "macos", Package.channel.in_(channels),
                                Package.status.in_(("live", "superseded"))).all()
    chosen: dict[tuple, object] = {}
    for row in rows:
        key = (row.evr, row.arch if row.arch == "arm64" else "")
        best = chosen.get(key)
        rank = (PREFERENCE.get(kind_of(row.path or ""), 9), row.channel != "stable", -row.id)
        if best is None or rank < (PREFERENCE.get(kind_of(best.path or ""), 9), best.channel != "stable", -best.id):
            chosen[key] = row

    def cmp(x, y):
        return compare(x.evr, y.evr) or ((x.arch == "arm64") - (y.arch == "arm64"))

    return sorted(chosen.values(), key=functools.cmp_to_key(cmp), reverse=True)


def _notes(pkg) -> str:
    """A build's release notes: its own, or those given with another file of
    the same version on its channel."""
    if pkg.notes.strip():
        return pkg.notes
    from .models import Package, Release
    from .releases import version_key
    key = version_key(pkg.version)
    for model in (Package, Release):
        for row in model.query.filter(model.channel == pkg.channel, model.notes != "",
                                      model.status.in_(("live", "superseded"))).order_by(model.id.desc()).limit(200):
            if version_key(row.version) == key:
                return row.notes
    return ""


def appcast(channel: str, base: str) -> str:
    """The feed for a channel, written from what is published now."""
    from . import site
    renderer = site.Renderer(site.get("live"))
    title = renderer.fill(renderer.doc["app"]["name"]) or "Flatout"
    from .releases import release_notes
    # The release notes page lists Flatpak releases; with none, it has
    # nothing to show a Mac.
    notes_page = renderer.release_notes_url() if release_notes() else ""
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        f'<rss version="2.0" xmlns:sparkle="{NS}" xmlns:dc="http://purl.org/dc/elements/1.1/">',
        "<channel>",
        f"<title>{escape(title + (' (beta)' if channel == 'beta' else ''))}</title>",
        f"<link>{escape(base)}/</link>",
        f"<description>{escape('Updates for ' + title)}</description>",
        "<language>en</language>",
    ]
    for pkg in items(("stable", "beta") if channel == "beta" else ("stable",)):
        filename = Path(pkg.path).name
        when = (pkg.published_at or pkg.created_at).replace(tzinfo=timezone.utc)
        lines += ["<item>",
                  f"<title>{escape(f'{title} {pkg.version}')}</title>",
                  f"<pubDate>{format_datetime(when)}</pubDate>",
                  f"<sparkle:version>{escape(pkg.evr)}</sparkle:version>",
                  f"<sparkle:shortVersionString>{escape(pkg.version)}</sparkle:shortVersionString>"]
        if pkg.min_os:
            lines.append(f"<sparkle:minimumSystemVersion>{escape(pkg.min_os)}</sparkle:minimumSystemVersion>")
        if pkg.arch == "arm64":
            lines.append("<sparkle:hardwareRequirements>arm64</sparkle:hardwareRequirements>")
        if notes_page:
            lines.append(f"<sparkle:fullReleaseNotesLink>{escape(base + notes_page)}</sparkle:fullReleaseNotesLink>")
        notes = _notes(pkg)
        if notes:
            # HTML, which Sparkle shows above its Install button. Escaped
            # rather than in CDATA, so nothing in the notes can end it.
            lines.append(f"<description>{escape(str(renderer.md(notes)))}</description>")
        attrs = {"url": f"{base}/macos/{pkg.id}/{filename}", "length": str(pkg.size or 0),
                 "type": "application/octet-stream", "sparkle:edSignature": pkg.signature or ""}
        if kind_of(filename) == "pkg" or pkg.control == "package":
            attrs["sparkle:installationType"] = "package"
        lines.append("<enclosure " + " ".join(f"{k}={quoteattr(v)}" for k, v in attrs.items()) + "/>")
        lines.append("</item>")
    lines += ["</channel>", "</rss>"]
    return "\n".join(lines) + "\n"


def info(base: str) -> dict:
    """What the admin and the API show: the public key and the feeds, and
    the lines a Mac app's Info.plist needs."""
    public = public_key()
    plist = ""
    if public:
        plist = (f"<key>SUFeedURL</key>\n<string>{escape(feed_url(base))}</string>\n"
                 f"<key>SUPublicEDKey</key>\n<string>{public}</string>")
    return {"public_key": public or None, "appcast_url": feed_url(base), "beta_appcast_url": feed_url(base, "beta"),
            "info_plist": plist or None, "reads_disk_images": seven_zip() is not None}


def sign_all(log: list) -> int:
    """Sign every published Mac build again, after the key changed."""
    from . import packages
    from .models import Package, db
    count = 0
    for pkg in Package.query.filter(Package.format == "macos", Package.status.in_(("live", "superseded"))).all():
        path = packages.root() / (pkg.path or "")
        if pkg.path and path.exists():
            pkg.signature = sign(path)
            count += 1
    db.session.commit()
    log.append(f"Signed {count} Mac build{'s' if count != 1 else ''} with the Sparkle key.")
    return count
