"""Mac apps and Sparkle: reading an upload's Info.plist, the EdDSA key and
signatures, the appcast, and the addresses Sparkle fetches.

The apps here are archives built below with an Info.plist and the start of a
Mach-O executable, the parts Flatout reads. A real disk image needs 7-Zip,
which is in the Docker image; here 7-Zip's answers are stood in for.
"""
import base64
import io
import plistlib
import struct
import tarfile
import zipfile
import zlib
from xml.etree import ElementTree

import pytest

from flatout import packages, sparkle
from flatout.models import Package, db

NS = {"sparkle": sparkle.NS}
ARM64, X86_64 = 0x0100000C, 0x01000007


def h(csrf):
    return {"X-CSRF": csrf}


def macho(*cpus: int) -> bytes:
    """The start of an executable: universal with a list of architectures,
    or thin."""
    if len(cpus) == 1:
        return b"\xcf\xfa\xed\xfe" + struct.pack("<I", cpus[0]) + bytes(64)
    return struct.pack(">II", 0xCAFEBABE, len(cpus)) + b"".join(struct.pack(">IIIII", c, 0, 0, 0, 0) for c in cpus)


def plist(build="42", short="1.4.0", minimum="12.0", exe="MyApp", ident="org.example.MyApp") -> bytes:
    return plistlib.dumps({"CFBundleVersion": build, "CFBundleShortVersionString": short,
                           "LSMinimumSystemVersion": minimum, "CFBundleIdentifier": ident,
                           "CFBundleExecutable": exe, "CFBundleName": "My App"}, fmt=plistlib.FMT_BINARY)


def mac_zip(build="42", short="1.4.0", cpus=(ARM64, X86_64), minimum="12.0", ident="org.example.MyApp") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("My App.app/Contents/Info.plist", plist(build, short, minimum, ident=ident))
        z.writestr("My App.app/Contents/MacOS/MyApp", macho(*cpus))
        # A framework's helper app: deeper, so not the one read.
        z.writestr("My App.app/Contents/Frameworks/Sparkle.framework/Versions/B/Updater.app/Contents/Info.plist",
                   plist("2", "2.6.0"))
        z.writestr("__MACOSX/My App.app/Contents/._Info.plist", b"\0")
    return buf.getvalue()


def mac_tar(build="43", short="1.4.1") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in (("MyApp.app/Contents/MacOS/MyApp", macho(ARM64)),   # before its Info.plist
                           ("MyApp.app/Contents/Info.plist", plist(build, short))):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def xar(files: dict[str, bytes]) -> bytes:
    """An installer package: a xar archive, its table of contents and each
    file zlib-packed."""
    heap, entries, at = b"", [], 0
    for path, data in files.items():
        packed = zlib.compress(data)
        entries.append((path, at, len(packed), len(data)))
        heap += packed
        at += len(packed)

    def entry(i, path, offset, length, size):
        parts = path.split("/")
        inner = (f"<name>{parts[-1]}</name><type>file</type><data><length>{length}</length><offset>{offset}</offset>"
                 f"<size>{size}</size><encoding style=\"application/x-gzip\"/></data>")
        for depth, folder in enumerate(reversed(parts[:-1])):
            inner = f"<file id=\"{i}0{depth}\"><name>{folder}</name><type>directory</type><file id=\"{i}\">{inner}</file></file>"
            return inner
        return f"<file id=\"{i}\">{inner}</file>"

    toc = ("<?xml version=\"1.0\"?><xar><toc>" +
           "".join(entry(i + 1, *e) for i, e in enumerate(entries)) + "</toc></xar>").encode()
    packed_toc = zlib.compress(toc)
    return struct.pack(">4sHHQQI", b"xar!", 28, 1, len(packed_toc), len(toc), 1) + packed_toc + heap


def mac_pkg(build="44", short="1.5.0") -> bytes:
    return xar({
        "Distribution": b'<installer-gui-script minSpecVersion="2"><allowed-os-versions><os-version min="13.0"/>'
                        b'</allowed-os-versions><options hostArchitectures="arm64,x86_64"/>'
                        b'<product version="1.5.0"/></installer-gui-script>',
        "MyApp.pkg/PackageInfo": f'<pkg-info version="{short}" identifier="org.example.MyApp.pkg"><bundle-version>'
                                 f'<bundle CFBundleShortVersionString="{short}" CFBundleVersion="{build}" '
                                 f'id="org.example.MyApp" path="./MyApp.app"/></bundle-version></pkg-info>'.encode(),
    })


def upload(client, csrf, data: bytes, filename: str, **fields):
    form = {"file": (io.BytesIO(data), filename), **fields}
    return client.post("/api/v1/packages", data=form, headers=h(csrf), content_type="multipart/form-data")


@pytest.fixture()
def run_jobs(app):
    from flatout import jobs
    return lambda: jobs.run_all_queued(app)


def feed(client, channel="stable"):
    resp = client.get(f"/sparkle/{sparkle.FEEDS[channel]}")
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "no-store"
    return ElementTree.fromstring(resp.data).find("channel")


def published(client, csrf, run_jobs, data, filename, **fields):
    resp = upload(client, csrf, data, filename, **fields)
    assert resp.status_code == 202, resp.get_json()
    run_jobs()
    pkg = client.get(f"/api/v1/packages/{resp.get_json()['package']['id']}").get_json()["package"]
    assert pkg["status"] in ("live", "superseded"), pkg
    return pkg


# ———————————————————————————— Versions ————————————————————————————

@pytest.mark.parametrize("newer,older", [
    ("2", "1"), ("10", "9"), ("1.0.1", "1.0"), ("1.0", "1.0b1"), ("1.0b2", "1.0b1"), ("1.0rc1", "1.0b9"),
    ("1.10", "1.9"), ("2.0", "1.99.99"), ("1.0.0.1", "1.0"), ("143", "142"),
])
def test_versions_compare_as_sparkle_compares_them(newer, older):
    assert sparkle.compare(newer, older) == 1
    assert sparkle.compare(older, newer) == -1
    assert sparkle.compare(newer, newer) == 0


# ———————————————————————————— The key ————————————————————————————

def test_a_key_signs_as_sparkle_checks(app, tmp_path):
    with app.app_context():
        assert not sparkle.has_key() and sparkle.public_key() == ""
        public = sparkle.generate()
        assert len(base64.b64decode(public)) == 32
        assert len(base64.b64decode(sparkle.secret_key())) == 32
        assert oct(sparkle.key_path().stat().st_mode & 0o777) == "0o600"
        f = tmp_path / "App.zip"
        f.write_bytes(mac_zip())
        signature = sparkle.sign(f)
        assert len(base64.b64decode(signature)) == 64
        assert sparkle.verify(f, signature, public)
        f.write_bytes(mac_zip() + b"!")
        assert not sparkle.verify(f, signature, public)


def test_a_key_from_generate_keys_is_taken(app):
    seed = bytes(range(32))
    with app.app_context():
        public = sparkle.import_key(base64.b64encode(seed).decode() + "\n")
        assert sparkle.secret_key() == base64.b64encode(seed).decode()
        # The public key Ed25519 derives from that seed (RFC 8032).
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        expected = Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        assert base64.b64decode(public) == expected
        with pytest.raises(Exception, match="older format"):
            sparkle.import_key(base64.b64encode(bytes(96)).decode())
        with pytest.raises(Exception, match="isn't a Sparkle private key"):
            sparkle.import_key("not a key")


# ———————————————————————————— What a file is ————————————————————————————

def test_mac_apps_are_known_from_inside(tmp_path):
    cases = {"App.zip": mac_zip(), "App.tar.gz": mac_tar(), "App.pkg": mac_pkg(),
             "App.dmg": b"\0" * 4096 + b"koly" + bytes(508)}
    for name, data in cases.items():
        f = tmp_path / name
        f.write_bytes(data)
        assert packages.sniff(f, name) == "macos", name
    plain = tmp_path / "linux.zip"
    with zipfile.ZipFile(plain, "w") as z:
        z.writestr("app/bin/app", b"\x7fELF")
    assert packages.sniff(plain, "linux.zip") == "file"
    tarball = tmp_path / "linux.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        info = tarfile.TarInfo("app/bin/app")
        tar.addfile(info, io.BytesIO(b""))
    assert packages.sniff(tarball, "linux.tar.gz") == "file"
    # An installer's xar without the name isn't taken for one.
    other = tmp_path / "archive.xar"
    other.write_bytes(mac_pkg())
    assert packages.sniff(other, "archive.xar") == "file"


def test_the_info_plist_and_machines_are_read(tmp_path):
    f = tmp_path / "a.zip"
    f.write_bytes(mac_zip())
    meta = sparkle.read(f, "zip")
    assert (meta["bundle_version"], meta["short_version"], meta["minimum_system_version"], meta["arch"]) == \
        ("42", "1.4.0", "12.0", "universal")
    f = tmp_path / "a.tar.gz"
    f.write_bytes(mac_tar())
    meta = sparkle.read(f, "tar")
    assert (meta["bundle_version"], meta["short_version"], meta["arch"]) == ("43", "1.4.1", "arm64")
    f = tmp_path / "a.pkg"
    f.write_bytes(mac_pkg())
    meta = sparkle.read(f, "pkg")
    assert (meta["bundle_version"], meta["short_version"], meta["minimum_system_version"], meta["arch"],
            meta["installer"]) == ("44", "1.5.0", "13.0", "universal", True)
    f = tmp_path / "bad.zip"
    f.write_bytes(b"PK\x03\x04 not really")
    assert sparkle.read(f, "zip")["problem"]


def test_a_disk_image_is_read_with_seven_zip(tmp_path, monkeypatch):
    listing = ("Path = MyApp.app\nFolder = +\n\nPath = MyApp.app/Contents/Info.plist\n\n"
               "Path = MyApp.app/Contents/MacOS/MyApp\n\nPath = Applications\n").encode()
    asked = []

    def fake(args, limit=None):
        asked.append(args)
        if args[0] == "l":
            return listing
        return plist("50", "2.0") if args[-1].endswith("Info.plist") else macho(X86_64)[:limit]

    monkeypatch.setattr(sparkle, "seven_zip", lambda: "/usr/bin/7z")
    monkeypatch.setattr(sparkle, "_7z", fake)
    meta = sparkle.read(tmp_path / "x.dmg", "dmg")
    assert (meta["bundle_version"], meta["short_version"], meta["arch"]) == ("50", "2.0", "x86_64")
    assert all("-spd" in a and "--" in a for a in asked)
    monkeypatch.setattr(sparkle, "seven_zip", lambda: None)
    assert "7-Zip" in sparkle.read(tmp_path / "x.dmg", "dmg")["problem"]


@pytest.mark.parametrize("filename,version,arch,expected", [
    ("My App 1.4.0.dmg", "1.4.0", "universal", "My-App.dmg"),
    ("MyApp-1.4.0.zip", "1.4.0", "arm64", "MyApp-arm64.zip"),
    ("MyApp-1.4.0-arm64.zip", "1.4.0", "arm64", "MyApp-arm64.zip"),
    ("MyApp_v2.0.tar.xz", "2.0", "x86_64", "MyApp-x86_64.tar.xz"),
    ("MyApp.pkg", "", "universal", "MyApp.pkg"),
])
def test_a_mac_app_is_named_without_its_version(filename, version, arch, expected):
    assert sparkle.download_name(filename, version, arch) == expected


# ———————————————————————————— Publishing and the appcast ————————————————————————————

def test_an_uploaded_mac_app_is_in_the_appcast(client, csrf, admin, run_jobs):
    pkg = published(client, csrf, run_jobs, mac_zip(), "My App 1.4.0.zip", notes="**Faster** sync.")
    assert (pkg["format"], pkg["name"], pkg["version"], pkg["evr"], pkg["arch"]) == \
        ("macos", "My-App.zip", "1.4.0", "42", "universal")
    assert pkg["sparkle"]["minimum_system_version"] == "12.0"
    assert "Made a Sparkle signing key" in pkg["job"]["log"]
    repo = client.get("/api/v1/repo").get_json()
    public = repo["sparkle"]["public_key"]
    assert "<key>SUPublicEDKey</key>" in repo["sparkle"]["info_plist"]
    assert repo["sparkle"]["appcast_url"].endswith("/sparkle/appcast.xml")

    item = feed(client).find("item")
    assert item.findtext("sparkle:version", namespaces=NS) == "42"
    assert item.findtext("sparkle:shortVersionString", namespaces=NS) == "1.4.0"
    assert item.findtext("sparkle:minimumSystemVersion", namespaces=NS) == "12.0"
    assert item.find("sparkle:hardwareRequirements", NS) is None
    assert "<strong>Faster</strong>" in item.findtext("description")
    enclosure = item.find("enclosure")
    url = enclosure.get("url")
    assert url.endswith(f"/macos/{pkg['id']}/My-App-1.4.0.zip")
    assert int(enclosure.get("length")) == len(mac_zip())
    signature = enclosure.get(f"{{{sparkle.NS}}}edSignature")
    assert signature == pkg["sparkle"]["ed_signature"]

    # The file at the feed's address is the one signed, and never changes.
    resp = client.get(url[url.index("/macos/"):])
    assert resp.status_code == 200 and resp.data == mac_zip()
    assert "immutable" in resp.headers["Cache-Control"]
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    Ed25519PublicKey.from_public_bytes(base64.b64decode(public)).verify(base64.b64decode(signature), resp.data)
    assert client.get(f"/macos/{pkg['id']}/other.zip").status_code == 404
    # And the site's address always gives the newest.
    assert client.get("/download/My-App.zip").data == mac_zip()


def test_the_feed_offers_newest_first_and_one_file_per_build(client, csrf, admin, run_jobs):
    published(client, csrf, run_jobs, mac_zip("42", "1.4.0"), "MyApp-1.4.0.zip")
    published(client, csrf, run_jobs, mac_zip("43", "1.4.1"), "MyApp-1.4.1.dmg.zip")
    # The same build as a disk image beside the zip: the zip is offered.
    dmg = b"\0" * 4096 + b"koly" + bytes(508)
    published(client, csrf, run_jobs, dmg, "MyApp-1.4.1.dmg", build="43", version="1.4.1")
    # An Apple silicon build of the same: listed first, for Apple silicon only.
    published(client, csrf, run_jobs, mac_zip("43", "1.4.1", cpus=(ARM64,)), "MyApp-1.4.1-arm64.zip")
    items = feed(client).findall("item")
    seen = [(i.findtext("sparkle:version", namespaces=NS), i.findtext("sparkle:hardwareRequirements", namespaces=NS),
             i.find("enclosure").get("url").rsplit(".", 1)[-1]) for i in items]
    assert seen == [("43", "arm64", "zip"), ("43", None, "zip"), ("42", None, "zip")]


def test_a_build_already_there_is_refused(client, csrf, admin, run_jobs):
    published(client, csrf, run_jobs, mac_zip("42"), "MyApp-1.4.0.zip")
    resp = upload(client, csrf, mac_zip("42"), "MyApp-1.4.0.zip")
    run_jobs()
    pkg = client.get(f"/api/v1/packages/{resp.get_json()['package']['id']}").get_json()["package"]
    assert pkg["status"] == "failed" and "higher build number" in pkg["error"]


def test_an_unreadable_build_needs_its_number(client, csrf, admin, run_jobs):
    dmg = b"\0" * 4096 + b"koly" + bytes(508)
    resp = upload(client, csrf, dmg, "MyApp-2.0.dmg")
    assert resp.get_json()["package"]["format"] == "macos"
    run_jobs()
    pkg = client.get(f"/api/v1/packages/{resp.get_json()['package']['id']}").get_json()["package"]
    assert pkg["status"] == "failed" and "CFBundleVersion" in pkg["error"]
    assert upload(client, csrf, dmg, "MyApp-2.0.dmg", build="has space").status_code == 400
    assert upload(client, csrf, dmg, "MyApp-2.0.dmg", minimum_system_version="Sonoma").status_code == 400
    pkg = published(client, csrf, run_jobs, dmg, "MyApp-2.0.dmg", build="200", minimum_system_version="13.0")
    assert (pkg["evr"], pkg["version"], pkg["name"], pkg["sparkle"]["minimum_system_version"]) == \
        ("200", "2.0", "MyApp.dmg", "13.0")
    assert client.get("/download/MyApp.dmg").headers["Content-Type"] == "application/x-apple-diskimage"


def test_an_installer_package_installs_as_one(client, csrf, admin, run_jobs):
    pkg = published(client, csrf, run_jobs, mac_pkg(), "MyApp-1.5.0.pkg")
    assert (pkg["evr"], pkg["version"], pkg["sparkle"]["minimum_system_version"]) == ("44", "1.5.0", "13.0")
    enclosure = feed(client).find("item/enclosure")
    assert enclosure.get(f"{{{sparkle.NS}}}installationType") == "package"


def test_a_zipped_installer_installs_as_one(client, csrf, admin, run_jobs):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("MyApp.pkg", mac_pkg("45", "1.5.1"))
    pkg = published(client, csrf, run_jobs, buf.getvalue(), "MyApp-1.5.1.zip")
    assert (pkg["format"], pkg["evr"], pkg["summary"]) == ("macos", "45", "org.example.MyApp")
    enclosure = feed(client).find("item/enclosure")
    assert enclosure.get(f"{{{sparkle.NS}}}installationType") == "package"


def test_a_package_that_unpacks_too_far_is_not_read(tmp_path):
    bomb = xar({"MyApp.pkg/PackageInfo": b"<pkg-info version='1'>" + b" " * (sparkle.PLIST_MAX * 2) + b"</pkg-info>"})
    f = tmp_path / "bomb.pkg"
    f.write_bytes(bomb)
    assert sparkle.read(f, "pkg")["problem"]


def test_the_beta_feed_has_both_channels_and_the_stable_one_only_stable(client, csrf, admin, run_jobs):
    published(client, csrf, run_jobs, mac_zip("42", "1.4.0"), "MyApp-1.4.0.zip")
    published(client, csrf, run_jobs, mac_zip("50", "1.5.0-beta.1"), "MyApp-1.5.0-beta.1.zip", channel="beta")
    assert [i.findtext("sparkle:version", namespaces=NS) for i in feed(client).findall("item")] == ["42"]
    beta = feed(client, "beta")
    assert beta.findtext("title").endswith("(beta)")
    assert [i.findtext("sparkle:version", namespaces=NS) for i in beta.findall("item")] == ["50", "42"]
    assert client.get("/download/beta/MyApp.zip").status_code == 200
    # Promoted, the build is on stable too, signed the same, and listed once in the beta feed.
    resp = client.post("/api/v1/packages/promote", json={"from": "beta", "to": "stable"}, headers=h(csrf))
    assert resp.status_code == 202
    run_jobs()
    assert [i.findtext("sparkle:version", namespaces=NS) for i in feed(client).findall("item")] == ["50", "42"]
    assert [i.findtext("sparkle:version", namespaces=NS) for i in feed(client, "beta").findall("item")] == ["50", "42"]
    assert client.post("/api/v1/packages/promote", json={"from": "beta", "to": "stable"},
                       headers=h(csrf)).status_code == 409


def test_a_beta_app_of_its_own_is_offered_only_its_own_builds(client, csrf, admin, run_jobs):
    # The beta installs beside the stable app, so the stable app's file is no update for it.
    published(client, csrf, run_jobs, mac_zip("42", "1.4.0"), "MyApp-1.4.0.zip")
    published(client, csrf, run_jobs, mac_zip("50", "1.5.0-beta.1", ident="org.example.MyApp.Beta"),
              "MyApp-Beta-1.5.0-beta.1.zip", channel="beta")
    published(client, csrf, run_jobs, mac_zip("51", "1.5.0"), "MyApp-1.5.0.zip")
    assert [i.findtext("sparkle:version", namespaces=NS) for i in feed(client).findall("item")] == ["51", "42"]
    assert [i.findtext("sparkle:version", namespaces=NS) for i in feed(client, "beta").findall("item")] == ["50"]
    published(client, csrf, run_jobs, mac_zip("60", "1.6.0-beta.1", ident="org.example.MyApp.Beta"),
              "MyApp-Beta-1.6.0-beta.1.zip", channel="beta")
    assert [i.findtext("sparkle:version", namespaces=NS) for i in feed(client, "beta").findall("item")] == ["60", "50"]


def test_withdrawing_takes_a_build_out_of_the_feed(client, csrf, admin, run_jobs):
    published(client, csrf, run_jobs, mac_zip("42", "1.4.0"), "MyApp-1.4.0.zip")
    newer = published(client, csrf, run_jobs, mac_zip("43", "1.4.1"), "MyApp-1.4.1.zip")
    assert client.post(f"/api/v1/packages/{newer['id']}/withdraw", headers=h(csrf)).status_code == 202
    run_jobs()
    log = client.get(f"/api/v1/packages/{newer['id']}").get_json()["package"]["job"]["log"]
    assert "Sparkle never goes back" in log
    assert [i.findtext("sparkle:version", namespaces=NS) for i in feed(client).findall("item")] == ["42"]
    assert client.get(f"/macos/{newer['id']}/MyApp-1.4.1.zip").status_code == 404


def test_feeds_count_installs_checking(app, client, csrf, admin, run_jobs):
    from flatout.models import StatDay
    published(client, csrf, run_jobs, mac_zip(), "MyApp-1.4.0.zip")
    feed(client)
    feed(client)
    with app.app_context():
        row = StatDay.query.filter_by(metric="pkgcheck", target="macos-stable").one()
        assert (row.uniques, row.hits) == (1, 2)
    stats = client.get("/api/v1/stats").get_json()
    assert any(f["format"] == "macos" and f["installs"] == 1 for f in stats["formats"])


# ———————————————————————————— The key's API ————————————————————————————

def test_the_key_is_made_imported_and_replaced(client, csrf, admin, run_jobs):
    resp = client.post("/api/v1/sparkle/key", json={"action": "generate"}, headers=h(csrf))
    assert resp.status_code == 200 and resp.get_json()["job"] is None
    first = resp.get_json()["sparkle"]["public_key"]
    resp = client.post("/api/v1/sparkle/key", json={"action": "generate"}, headers=h(csrf))
    assert resp.status_code == 409
    pkg = published(client, csrf, run_jobs, mac_zip(), "MyApp-1.4.0.zip")
    assert "Made a Sparkle signing key" not in pkg["job"]["log"]
    seed = base64.b64encode(bytes(range(32))).decode()
    resp = client.post("/api/v1/sparkle/key", json={"action": "import", "key": seed, "replace": True}, headers=h(csrf))
    assert resp.status_code == 200 and resp.get_json()["job"]["kind"] == "sparkle-sign"
    public = resp.get_json()["sparkle"]["public_key"]
    assert public != first
    run_jobs()
    signature = feed(client).find("item/enclosure").get(f"{{{sparkle.NS}}}edSignature")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    Ed25519PublicKey.from_public_bytes(base64.b64decode(public)).verify(base64.b64decode(signature), mac_zip())
    secret = client.get("/api/v1/sparkle/key/secret", headers=h(csrf)).get_json()
    assert secret == {"key": seed, "public_key": public}
    bad = client.post("/api/v1/sparkle/key", json={"action": "import", "key": "nope", "replace": True}, headers=h(csrf))
    assert bad.status_code == 400 and "generate_keys" in bad.get_json()["error"]


def test_the_secret_key_never_goes_to_a_token(client, csrf, admin):
    client.post("/api/v1/sparkle/key", json={"action": "generate"}, headers=h(csrf))
    token = client.post("/api/v1/tokens", json={"name": "ci", "scopes": ["read", "releases"]},
                        headers=h(csrf)).get_json()["token"]
    other = client.application.test_client()
    resp = other.get("/api/v1/sparkle/key/secret", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


# ———————————————————————————— The site ————————————————————————————

def with_a_linux_package(app):
    """An RPM beside the Mac app, so the dialog lists its formats."""
    from flatout.models import utcnow
    with app.app_context():
        db.session.add(Package(format="rpm", channel="stable", name="myapp", version="1.4.0", evr="1.4.0-1",
                               arch="x86_64", status="live", path="rpm/stable/packages/myapp-1.4.0-1.x86_64.rpm",
                               published_at=utcnow()))
        db.session.commit()


def chips_of(page, m="macos"):
    face = page[page.index(f'value="{m}"'):]
    return face[:face.index("</label>")]


def test_the_install_dialog_offers_the_mac_app(app, client, csrf, admin, run_jobs):
    published(client, csrf, run_jobs, mac_zip(), "My App 1.4.0.zip")
    with_a_linux_package(app)
    client.post("/api/v1/site/status", json={"status": "published"}, headers=h(csrf))
    client.patch("/api/v1/site/status", json={"status": "published"}, headers=h(csrf))
    page = client.get("/").get_data(as_text=True)
    assert 'data-method="macos"' in page
    assert "/download/My-App.zip" in page
    assert "Apple silicon and Intel" in page
    # A universal build runs on both kinds of Mac, so the list names both.
    assert ('<span class="method__arches"><span class="arch-chip" data-arch="arm64">Apple silicon</span>'
            '<span class="arch-chip" data-arch="x86_64">Intel</span></span>') in chips_of(page)
    # With no Flatpak release, the Linux formats still name their machines, as the Mac's does.
    assert '<span class="arch-chip" data-arch="x86_64">Intel/AMD</span>' in chips_of(page, "rpm")


def test_the_mac_chips_name_only_the_macs_built_for(app, client, csrf, admin, run_jobs):
    published(client, csrf, run_jobs, mac_zip(cpus=(ARM64,)), "MyApp-1.4.0-arm64.zip")
    with_a_linux_package(app)
    client.post("/api/v1/site/status", json={"status": "published"}, headers=h(csrf))
    client.patch("/api/v1/site/status", json={"status": "published"}, headers=h(csrf))
    page = client.get("/").get_data(as_text=True)
    chips = chips_of(page)
    assert '<span class="arch-chip" data-arch="arm64">Apple silicon</span>' in chips
    assert 'data-arch="x86_64"' not in chips


def test_the_migration_adds_the_mac_columns(app):
    from sqlalchemy import inspect, text
    from flatout import _migrate
    with app.app_context():
        db.session.execute(text("ALTER TABLE packages DROP COLUMN signature"))
        db.session.execute(text("ALTER TABLE packages DROP COLUMN min_os"))
        db.session.commit()
        _migrate(app)
        _migrate(app)
        names = {c["name"] for c in inspect(db.engine).get_columns("packages")}
        assert {"signature", "min_os"} <= names
        assert Package.query.count() == 0


def test_a_package_for_every_machine_names_both(app, client, csrf, admin, run_jobs):
    published(client, csrf, run_jobs, mac_zip(), "MyApp-1.4.0.zip")
    from flatout.models import utcnow
    with app.app_context():
        db.session.add(Package(format="rpm", channel="stable", name="myapp", version="1.4.0", evr="1.4.0-1",
                               arch="noarch", status="live", path="rpm/stable/packages/myapp-1.4.0-1.noarch.rpm",
                               published_at=utcnow()))
        db.session.commit()
    client.post("/api/v1/site/status", json={"status": "published"}, headers=h(csrf))
    client.patch("/api/v1/site/status", json={"status": "published"}, headers=h(csrf))
    page = client.get("/").get_data(as_text=True)
    assert ('<span class="method__arches"><span class="arch-chip" data-arch="x86_64">Intel/AMD</span>'
            '<span class="arch-chip" data-arch="aarch64">ARM</span></span>') in chips_of(page, "rpm")
