"""AppImages: update information written in, zsync files, and the addresses
updaters fetch.

The AppImages here are small ELF files with the sections a real runtime has,
built below. zsyncmake is in the Docker image (tools/test-in-docker.sh);
without it a stand-in writes the header Gear Lever reads, and the test that
needs the real tool is skipped.
"""
import hashlib
import io
import re
import shutil
import struct
import subprocess

import pytest

from flatout import appimage, packages
from flatout.models import Package, db


def h(csrf):
    return {"X-CSRF": csrf}


def build(body: bytes = b"payload", update_info: bytes = b"", signed: bool = False, machine: int = 0x3E,
          kind: int = 2) -> bytes:
    """An ELF64 file shaped like a type 2 AppImage: "AI" and the type in the
    header's padding, and .upd_info, .sha256_sig and .shstrtab sections."""
    names = b"\0.upd_info\0.sha256_sig\0.shstrtab\0"
    upd_at, sig_at = 0x100, 0x100 + 1024
    names_at = sig_at + 1024
    body_at = names_at + len(names)
    shoff = body_at + len(body)
    ident = b"\x7fELF" + bytes([2, 1, 1, 0]) + b"AI" + bytes([kind]) + bytes(5)
    header = ident + struct.pack("<HHIQQQIHHHHHH", 2, machine, 1, 0, 0, shoff, 0, 64, 56, 0, 64, 4, 3)
    data = bytearray(header.ljust(upd_at, b"\0"))
    data += update_info.ljust(1024, b"\0")
    data += (b"\x01" * 64 if signed else b"").ljust(1024, b"\0")
    data += names + body

    def section(name, offset, size):
        return struct.pack("<IIQQQQIIQQ", name, 1, 0, 0, offset, size, 0, 0, 1, 0)
    data += bytes(64)   # the null section
    data += section(names.index(b".upd_info"), upd_at, 1024)
    data += section(names.index(b".sha256_sig"), sig_at, 1024)
    data += section(names.index(b".shstrtab"), names_at, len(names))
    return bytes(data)


def upload(client, csrf, data: bytes, filename: str, **fields):
    form = {"file": (io.BytesIO(data), filename), **fields}
    return client.post("/api/v1/packages", data=form, headers=h(csrf), content_type="multipart/form-data")


def header(zsync: bytes) -> dict:
    head = zsync.partition(b"\n\n")[0].decode()
    return dict(line.split(": ", 1) for line in head.splitlines())


def embedded(data: bytes) -> str:
    """What Gear Lever finds: readelf's dump of .upd_info, read with its own
    pattern (UpdateManagerChecker.check_app_embedded_url)."""
    if shutil.which("readelf"):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".AppImage") as f:
            f.write(data)
            f.flush()
            out = subprocess.run(["readelf", "--string-dump=.upd_info", "--wide", f.name],
                                 capture_output=True, text=True).stdout
        found = re.search(r"\szsync\|http(.*)\s", out.replace("\n", " ") + " ")
        return found[0].strip() if found else ""
    start = data.index(b"zsync|")
    return data[start:data.index(b"\0", start)].decode()


@pytest.fixture()
def run_jobs(app):
    from flatout import jobs
    return lambda: jobs.run_all_queued(app)


@pytest.fixture()
def zsyncmake(monkeypatch):
    """The real zsyncmake where it's installed; otherwise a stand-in that
    writes the header (Gear Lever reads only that) and no block checksums."""
    if shutil.which("zsyncmake"):
        return True

    def make(path, log):
        data = path.read_bytes()
        dest = path.with_name(path.name + ".zsync")
        dest.write_bytes(f"zsync: 0.6.2\nFilename: {path.name}\nBlocksize: 2048\nLength: {len(data)}\n"
                         f"URL: {path.name}\nSHA-1: {hashlib.sha1(data).hexdigest()}\n\n".encode())
        return dest
    monkeypatch.setattr(appimage, "make_zsync", make)
    monkeypatch.setattr(packages, "tools", lambda: {"rpm": False, "deb": False, "appimage": True})
    return False


# ———————————————————————————— Reading and writing ————————————————————————————

def test_an_appimage_is_known_by_its_first_bytes(tmp_path):
    path = tmp_path / "x"
    path.write_bytes(build())
    assert packages.sniff(path, "whatever.bin") == "appimage"
    path.write_bytes(b"\x7fELF" + bytes(20))
    assert packages.sniff(path, "App.AppImage") == "file"


def test_update_information_is_read_and_written(tmp_path):
    path = tmp_path / "App.AppImage"
    path.write_bytes(build(update_info=b"gh-releases-zsync|me|app|latest|App-*.AppImage.zsync"))
    meta = appimage.read(path)
    assert meta == {"type": 2, "arch": "x86_64", "update_info": "gh-releases-zsync|me|app|latest|App-*.AppImage.zsync",
                    "signed": False, "room": 1024}
    before = path.read_bytes()
    appimage.write_update_info(path, "zsync|https://example.com/download/App.AppImage.zsync")
    after = path.read_bytes()
    assert appimage.read(path)["update_info"] == "zsync|https://example.com/download/App.AppImage.zsync"
    assert len(after) == len(before)
    # Only the section changed, and the old, longer value is gone from it.
    changed = [i for i in range(len(before)) if before[i] != after[i]]
    assert 0x100 <= min(changed) and max(changed) < 0x100 + 1024
    assert b"latest" not in after

    with pytest.raises(packages.RepoError, match="longer"):
        appimage.write_update_info(path, "zsync|https://" + "x" * 1100)
    assert appimage.read(tmp_path.joinpath("App.AppImage"))["update_info"].startswith("zsync|https://example.com")


def test_signatures_and_architectures_are_read(tmp_path):
    path = tmp_path / "App.AppImage"
    path.write_bytes(build(signed=True, machine=0xB7))
    assert appimage.read(path)["signed"] is True and appimage.read(path)["arch"] == "aarch64"
    path.write_bytes(b"not an appimage at all")
    assert appimage.read(path) == {}


@pytest.mark.parametrize("filename,arch,expected", [
    ("App-1.2.0-x86_64.AppImage", "x86_64", "App-x86_64.AppImage"),
    ("App-v1.2.0-x86_64.AppImage", "x86_64", "App-x86_64.AppImage"),
    ("app_1.2.0_amd64.AppImage", "x86_64", "app_amd64.AppImage"),
    ("App-1.2.0.AppImage", "aarch64", "App-aarch64.AppImage"),
    ("App.AppImage", "x86_64", "App-x86_64.AppImage"),
    ("App-x86_64.AppImage", "x86_64", "App-x86_64.AppImage"),
    ("App-1.2.0-beta.3-x86_64.AppImage", "", "App-x86_64.AppImage"),
])
def test_an_appimage_is_named_without_its_version(filename, arch, expected):
    assert packages.stable_name(filename, arch) == expected


def test_a_zsync_header_takes_another_url():
    zsync = b"zsync: 0.6.2\nURL: App.AppImage\nSHA-1: abc\n\n\x00\x01URL: binary"
    out = appimage.with_url(zsync, "https://example.com/appimage/3/App.AppImage")
    assert out == b"zsync: 0.6.2\nURL: https://example.com/appimage/3/App.AppImage\nSHA-1: abc\n\n\x00\x01URL: binary"


# ———————————————————————————— Publishing ————————————————————————————

def test_an_uploaded_appimage_updates_from_this_site(client, csrf, admin, run_jobs, zsyncmake):
    resp = upload(client, csrf, build(b"one"), "App-1.0.0-x86_64.AppImage", notes="First.")
    assert resp.status_code == 202, resp.get_json()
    assert resp.get_json()["package"]["format"] == "appimage"
    run_jobs()

    pkg = client.get("/api/v1/packages").get_json()["packages"][0]
    assert pkg["status"] == "live", pkg
    assert (pkg["name"], pkg["version"], pkg["arch"]) == ("App-x86_64.AppImage", "1.0.0", "x86_64")
    assert pkg["file"]["zsync_url"] == "http://localhost/download/App-x86_64.AppImage.zsync"

    dl = client.get("/download/App-x86_64.AppImage")
    assert dl.status_code == 200 and dl.mimetype == "application/vnd.appimage"
    assert "App-1.0.0-x86_64.AppImage" in dl.headers["Content-Disposition"]
    assert embedded(dl.data) == "zsync|http://localhost/download/App-x86_64.AppImage.zsync"
    assert hashlib.sha256(dl.data).hexdigest() == pkg["file"]["sha256"]

    # What Gear Lever does: fetch the zsync file, compare its SHA-1 with the
    # installed file's, then download from its URL.
    zs = client.get("/download/App-x86_64.AppImage.zsync")
    assert zs.status_code == 200 and zs.headers["Cache-Control"] == "no-store"
    head = header(zs.data)
    assert head["SHA-1"] == hashlib.sha1(dl.data).hexdigest()
    assert head["URL"] == f"http://localhost/appimage/{pkg['id']}/App-1.0.0-x86_64.AppImage"
    exact = client.get(head["URL"][len("http://localhost"):])
    assert exact.status_code == 200 and exact.data == dl.data
    assert "immutable" in exact.headers["Cache-Control"]
    own = client.get(head["URL"][len("http://localhost"):] + ".zsync")
    assert header(own.data)["URL"] == "App-1.0.0-x86_64.AppImage"

    info = client.get("/api/v1/repo").get_json()["packages"]
    assert [f["name"] for f in info["appimage"]["stable"]] == ["App-x86_64.AppImage"] and not info["files"]["stable"]
    assert info["appimage"]["stable"][0]["zsync_url"] == "http://localhost/download/App-x86_64.AppImage.zsync"

    # The next version takes the address over; an installed 1.0.0 sees a new SHA-1.
    upload(client, csrf, build(b"two"), "App-1.1.0-x86_64.AppImage")
    run_jobs()
    newer = header(client.get("/download/App-x86_64.AppImage.zsync").data)
    assert newer["SHA-1"] != head["SHA-1"] and newer["URL"].endswith("/App-1.1.0-x86_64.AppImage")
    rows = client.get("/api/v1/packages").get_json()["packages"]
    assert [(r["version"], r["status"]) for r in rows] == [("1.1.0", "live"), ("1.0.0", "superseded")]

    stats = client.get("/api/v1/stats").get_json()["packages"]
    assert [(r["format"], r["channel"], r["today"]) for r in stats["repositories"]] == [("appimage", "stable", 1)]

    # Withdrawing the newest sends updaters back to the one before.
    assert client.post(f"/api/v1/packages/{rows[0]['id']}/withdraw", headers=h(csrf)).status_code == 202
    run_jobs()
    assert header(client.get("/download/App-x86_64.AppImage.zsync").data)["SHA-1"] == head["SHA-1"]
    assert client.get(newer["URL"][len("http://localhost"):]).status_code == 404
    log = client.get(f"/api/v1/packages/{rows[0]['id']}").get_json()["package"]["job"]["log"]
    assert "go back to 1.0.0" in log


def test_the_pinned_public_address_is_the_one_written_in(client, csrf, admin, run_jobs, zsyncmake):
    client.patch("/api/v1/repo/settings", json={"public_url": "https://app.example.com/"}, headers=h(csrf))
    upload(client, csrf, build(), "App-1.0.0-x86_64.AppImage", channel="beta")
    run_jobs()
    data = client.get("/download/beta/App-x86_64.AppImage").data
    assert embedded(data) == "zsync|https://app.example.com/download/beta/App-x86_64.AppImage.zsync"
    assert client.get("/download/App-x86_64.AppImage.zsync").status_code == 404
    assert header(client.get("/download/beta/App-x86_64.AppImage.zsync").data)["URL"].startswith(
        "https://app.example.com/appimage/")


def test_a_promoted_appimage_updates_from_stable(client, csrf, admin, run_jobs, zsyncmake):
    upload(client, csrf, build(), "App-2.0.0-x86_64.AppImage", channel="beta")
    run_jobs()
    beta = client.get("/download/beta/App-x86_64.AppImage").data
    resp = client.post("/api/v1/packages/promote", json={"from": "beta", "to": "stable"}, headers=h(csrf))
    assert resp.status_code == 202, resp.get_json()
    run_jobs()
    stable = client.get("/download/App-x86_64.AppImage").data
    assert embedded(beta).endswith("/download/beta/App-x86_64.AppImage.zsync")
    assert embedded(stable) == "zsync|http://localhost/download/App-x86_64.AppImage.zsync"
    assert len(stable) == len(beta)
    assert header(client.get("/download/App-x86_64.AppImage.zsync").data)["SHA-1"] == hashlib.sha1(stable).hexdigest()
    # The beta keeps its own file, and a second promotion has nothing to do.
    assert client.get("/download/beta/App-x86_64.AppImage").data == beta
    again = client.post("/api/v1/packages/promote", json={"from": "beta", "to": "stable"}, headers=h(csrf))
    assert again.status_code == 409


def test_a_signed_appimage_is_left_as_it_is(client, csrf, admin, run_jobs, zsyncmake):
    original = build(update_info=b"zsync|https://elsewhere.example/App.AppImage.zsync", signed=True)
    upload(client, csrf, original, "App-1.0.0-x86_64.AppImage")
    run_jobs()
    assert client.get("/download/App-x86_64.AppImage").data == original
    pkg = client.get("/api/v1/packages").get_json()["packages"][0]
    log = client.get(f"/api/v1/packages/{pkg['id']}").get_json()["package"]["job"]["log"]
    assert "signed" in log and "appimagetool -u" in log
    # Still served with a zsync file, for an updater pointed here by hand.
    assert client.get("/download/App-x86_64.AppImage.zsync").status_code == 200


def test_a_fixed_name_is_kept(client, csrf, admin, run_jobs, zsyncmake):
    upload(client, csrf, build(), "build-output.AppImage", name="MyApp.AppImage", version="3.0")
    run_jobs()
    pkg = client.get("/api/v1/packages").get_json()["packages"][0]
    assert (pkg["name"], pkg["version"]) == ("MyApp.AppImage", "3.0")
    assert embedded(client.get("/download/MyApp.AppImage").data).endswith("/download/MyApp.AppImage.zsync")


def test_appimages_need_zsyncmake(client, csrf, admin, monkeypatch):
    monkeypatch.setattr(packages, "tools", lambda: {"rpm": False, "deb": False, "appimage": False})
    resp = upload(client, csrf, build(), "App-1.0.0-x86_64.AppImage")
    assert resp.status_code == 503 and "zsyncmake" in resp.get_json()["error"]


def test_appimages_published_as_files_become_appimages(app, client, csrf, admin, run_jobs):
    # Published while AppImages were plain files: no zsync, a name of its own.
    with app.app_context():
        pkg = Package(format="file", channel="stable", name="Old.AppImage", version="0.9", status="live")
        db.session.add(pkg)
        db.session.flush()
        pkg.path = f"files/stable/{pkg.id}/Old.AppImage"
        (packages.root() / pkg.path).parent.mkdir(parents=True)
        (packages.root() / pkg.path).write_bytes(build())
        other = Package(format="file", channel="stable", name="tool.tar.gz", version="1", status="live",
                        path="files/stable/99/tool.tar.gz")
        db.session.add(other)
        (packages.root() / other.path).parent.mkdir(parents=True)
        (packages.root() / other.path).write_bytes(b"tarball")
        db.session.commit()
        assert packages.adopt_appimages() == 1
        assert packages.adopt_appimages() == 0
        assert (pkg.format, pkg.arch, other.format) == ("appimage", "x86_64", "file")
    assert client.get("/download/Old.AppImage").status_code == 200
    assert client.get("/download/Old.AppImage.zsync").status_code == 404
    info = client.get("/api/v1/repo").get_json()["packages"]["appimage"]["stable"]
    assert info[0]["zsync_url"] is None


def test_the_install_dialog_offers_the_appimage(client, csrf, admin, run_jobs, zsyncmake):
    upload(client, csrf, build(), "App-1.0.0-x86_64.AppImage")
    run_jobs()
    client.post("/api/v1/site/publish", headers=h(csrf))
    page = client.get("/").get_data(as_text=True)
    assert 'data-method="appimage"' in page
    assert 'href="http://localhost/download/App-x86_64.AppImage"' in page
    assert "Gear Lever" in page


@pytest.mark.skipif(not shutil.which("zsyncmake"), reason="needs zsyncmake (tools/test-in-docker.sh)")
def test_the_zsync_file_describes_the_file_served(client, csrf, admin, run_jobs, zsyncmake):
    body = bytes(range(256)) * 400
    upload(client, csrf, build(body), "App-1.0.0-x86_64.AppImage")
    run_jobs()
    served = client.get("/download/App-x86_64.AppImage").data
    head = header(client.get("/download/App-x86_64.AppImage.zsync").data)
    assert head["SHA-1"] == hashlib.sha1(served).hexdigest()
    assert int(head["Length"]) == len(served)
    assert head["Filename"] == "App-1.0.0-x86_64.AppImage"


def test_blocks_come_several_ranges_at_a_time(client, csrf, admin, run_jobs, zsyncmake):
    """zsync asks for the blocks that changed as several ranges in one
    request; Werkzeug alone would refuse it."""
    upload(client, csrf, build(bytes(range(256)) * 40), "App-1.0.0-x86_64.AppImage")
    run_jobs()
    pkg = client.get("/api/v1/packages").get_json()["packages"][0]
    path = f"/appimage/{pkg['id']}/App-1.0.0-x86_64.AppImage"
    data = client.get(path).data

    one = client.get(path, headers={"Range": "bytes=10-19"})
    assert one.status_code == 206 and one.data == data[10:20]

    many = client.get(path, headers={"Range": "bytes=0-99,2000-2047,-10"})
    assert many.status_code == 206 and many.mimetype == "multipart/byteranges"
    boundary = many.mimetype_params["boundary"].encode()
    assert int(many.headers["Content-Length"]) == len(many.data)
    parts = many.data.split(b"--" + boundary)[1:-1]
    got = []
    for part in parts:
        head, _, body = part.partition(b"\r\n\r\n")
        rng = re.search(rb"Content-Range: bytes (\d+)-(\d+)/(\d+)", head).groups()
        got.append((int(rng[0]), int(rng[1]), body[:-2]))
    size = len(data)
    assert [(a, b) for a, b, _ in got] == [(0, 99), (2000, 2047), (size - 10, size - 1)]
    assert all(body == data[a:b + 1] for a, b, body in got)

    # Too many parts: the whole file instead.
    spread = ",".join(f"{i * 4}-{i * 4}" for i in range(300))
    whole = client.get(path, headers={"Range": f"bytes={spread}"})
    assert whole.status_code == 200 and whole.data == data


def test_an_appimage_that_fails_leaves_nothing_behind(app, client, csrf, admin, run_jobs, zsyncmake, monkeypatch):
    def broken(path, log):
        raise packages.RepoError("zsyncmake failed: out of space")
    monkeypatch.setattr(appimage, "make_zsync", broken)
    upload(client, csrf, build(), "App-1.0.0-x86_64.AppImage")
    run_jobs()
    pkg = client.get("/api/v1/packages").get_json()["packages"][0]
    assert pkg["status"] == "failed" and "out of space" in pkg["error"]
    with app.app_context():
        assert not list((packages.root() / "appimage").rglob("*.AppImage*"))
    assert client.get("/download/App-x86_64.AppImage").status_code == 404
