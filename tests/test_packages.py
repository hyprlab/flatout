"""Packages: the dnf and apt repositories and other downloads.

Versions, file types and plain files need no tools and run anywhere. The RPM
and Debian tests build real packages, publish them, and check the signatures
the way dnf and apt would; they need rpm, rpmsign, rpmbuild, createrepo_c,
dpkg-deb and gpg, which the Docker image has (tools/test-in-docker.sh).
"""
import gzip
import hashlib
import io
import re
import shutil
import subprocess

import pytest

from flatout import packages
from flatout.models import Package, db


def h(csrf):
    return {"X-CSRF": csrf}


@pytest.fixture()
def run_jobs(app):
    from flatout import jobs

    def run():
        jobs.run_all_queued(app)
    return run


def upload(client, csrf, data: bytes, filename: str, **fields):
    form = {"file": (io.BytesIO(data), filename), **fields}
    return client.post("/api/v1/packages", data=form, headers=h(csrf), content_type="multipart/form-data")


# ———————————————————————————— Versions ————————————————————————————

@pytest.mark.parametrize("a,b,expected", [
    ("1.0", "1.0", 0), ("1.10", "1.9", 1), ("1.0", "1.0.1", -1), ("1.0a", "1.0", 1),
    ("1.0~rc1", "1.0", -1), ("1.0~rc1", "1.0~rc2", -1), ("1.0^git1", "1.0", 1), ("1.0^git1", "1.0.1", -1),
    ("1.0-1.fc44", "1.0-2.fc44", -1), ("2:1.0-1", "1:9.9-9", 1), ("1.43.0~beta.7-1", "1.42.0-1", 1),
    ("1.0.010", "1.0.9", 1), ("1.a", "1.1", -1),
])
def test_rpm_versions_compare_like_rpm(a, b, expected):
    assert packages.compare_rpm(a, b) == expected
    assert packages.compare_rpm(b, a) == -expected


@pytest.mark.parametrize("a,b,expected", [
    ("1.0", "1.0", 0), ("1.10", "1.9", 1), ("1.0~rc1", "1.0", -1), ("1.0~~", "1.0~", -1),
    ("1.0-1", "1.0-2", -1), ("1:0.9", "2.0", 1), ("1.0+b1", "1.0", 1), ("1.0a", "1.0+", -1),
    ("2.0-1ubuntu1", "2.0-1", 1), ("1.2-3-4", "1.2-3-5", -1),
])
def test_debian_versions_compare_like_dpkg(a, b, expected):
    assert packages.compare_deb(a, b) == expected
    assert packages.compare_deb(b, a) == -expected


def test_file_types_come_from_their_first_bytes(tmp_path):
    rpm = tmp_path / "x"
    rpm.write_bytes(b"\xed\xab\xee\xdb" + b"\0" * 20)
    deb = tmp_path / "y"
    deb.write_bytes(b"!<arch>\ndebian-binary   " + b"\0" * 20)
    other = tmp_path / "z"
    other.write_bytes(b"\x7fELF" + b"\0" * 20)
    assert packages.sniff(rpm, "anything.bin") == "rpm"
    assert packages.sniff(deb, "anything.bin") == "deb"
    assert packages.sniff(other, "App-x86_64.AppImage") == "file"
    assert packages.sniff(other, "app.flatpak") == "flatpak"


def test_names_say_architecture_and_version():
    assert packages.guess_arch("Hylki-1.42.0-x86_64.AppImage") == "x86_64"
    assert packages.guess_arch("app_arm64.tar.gz") == "aarch64"
    assert packages.guess_arch("notes.txt") == ""
    assert packages.guess_version("Hylki-1.42.0-x86_64.AppImage") == "1.42.0"
    assert packages.guess_version("app-2.0.1-beta.3.tar.gz") == "2.0.1-beta.3"


# ———————————————————————————— Plain files ————————————————————————————

def test_a_file_becomes_a_download(client, csrf, admin, run_jobs):
    resp = upload(client, csrf, b"\x7fELF one", "App-1.0.0-x86_64.AppImage", notes="First.")
    assert resp.status_code == 202, resp.get_json()
    pkg = resp.get_json()["package"]
    assert pkg["format"] == "file" and pkg["status"] == "queued"
    run_jobs()

    got = client.get(f"/api/v1/packages/{pkg['id']}").get_json()["package"]
    assert got["status"] == "live", got
    assert got["version"] == "1.0.0" and got["arch"] == "x86_64" and got["name"] == "App-1.0.0-x86_64.AppImage"
    assert got["file"]["sha256"] == hashlib.sha256(b"\x7fELF one").hexdigest()
    dl = client.get("/download/App-1.0.0-x86_64.AppImage")
    assert dl.status_code == 200 and dl.data == b"\x7fELF one"
    assert "attachment" in dl.headers["Content-Disposition"]

    files = client.get("/api/v1/repo").get_json()["packages"]["files"]["stable"]
    assert [f["name"] for f in files] == ["App-1.0.0-x86_64.AppImage"]

    counted = client.get("/api/v1/stats").get_json()["packages"]["downloads"]
    assert [(d["name"], d["installs"], d["downloads"]) for d in counted] == [("App-1.0.0-x86_64.AppImage", 1, 1)]


def test_a_file_under_a_fixed_name_keeps_its_address(client, csrf, admin, run_jobs, app):
    for body, version in ((b"one", "1.0"), (b"two", "1.1")):
        assert upload(client, csrf, body, f"build-{version}.bin", name="App-x86_64.AppImage",
                      version=version).status_code == 202
        run_jobs()
    assert client.get("/download/App-x86_64.AppImage").data == b"two"

    rows = client.get("/api/v1/packages?format=file").get_json()["packages"]
    assert [(r["version"], r["status"]) for r in rows] == [("1.1", "live"), ("1.0", "superseded")]

    # Withdrawing the newest brings the one before back.
    assert client.post(f"/api/v1/packages/{rows[0]['id']}/withdraw", headers=h(csrf)).status_code == 202
    run_jobs()
    assert client.get("/download/App-x86_64.AppImage").data == b"one"
    with app.app_context():
        assert db.session.get(Package, rows[0]["id"]).status == "withdrawn"
        assert not list((packages.root() / "files" / "stable").glob(f"{rows[0]['id']}/*"))


def test_a_fixed_name_keeps_the_version_the_file_was_named_with(client, csrf, admin, run_jobs):
    upload(client, csrf, b"three", "App-1.2.0-x86_64.AppImage", name="App.AppImage")
    run_jobs()
    newest = client.get("/api/v1/packages").get_json()["packages"][0]
    assert (newest["name"], newest["version"], newest["arch"]) == ("App.AppImage", "1.2.0", "x86_64")


def test_files_past_the_number_kept_are_pruned(client, csrf, admin, run_jobs, app):
    assert client.patch("/api/v1/repo/settings", json={"packages_kept": 2}, headers=h(csrf)).status_code == 200
    for n in range(4):
        upload(client, csrf, f"v{n}".encode(), "tool.tar.gz", version=f"1.{n}")
        run_jobs()
    rows = client.get("/api/v1/packages").get_json()["packages"]
    assert [r["status"] for r in rows] == ["live", "superseded", "pruned", "pruned"]
    with app.app_context():
        assert len(list((packages.root() / "files" / "stable").rglob("tool.tar.gz"))) == 2
    assert client.patch("/api/v1/repo/settings", json={"packages_kept": 0}, headers=h(csrf)).status_code == 400


def test_beta_files_promote_to_stable(client, csrf, admin, run_jobs):
    upload(client, csrf, b"beta build", "App.AppImage", channel="beta", version="2.0~beta1")
    run_jobs()
    assert client.get("/download/App.AppImage").status_code == 404
    assert client.get("/download/beta/App.AppImage").data == b"beta build"
    resp = client.post("/api/v1/packages/promote", json={"from": "beta", "to": "stable"}, headers=h(csrf))
    assert resp.status_code == 202, resp.get_json()
    run_jobs()
    assert client.get("/download/App.AppImage").data == b"beta build"
    stable = client.get("/api/v1/packages?channel=stable").get_json()["packages"][0]
    assert stable["origin"] == "promote" and stable["version"] == "2.0~beta1" and stable["status"] == "live"


def test_uploads_that_cant_be_published_are_refused(client, csrf, admin, monkeypatch, app):
    resp = upload(client, csrf, b"anything", "org.example.App.flatpak")
    assert resp.status_code == 400 and "release" in resp.get_json()["error"]
    assert upload(client, csrf, b"", "empty.bin").status_code == 400
    assert upload(client, csrf, b"x", "a.bin", name="no/slashes").status_code == 400
    assert upload(client, csrf, b"x", "a.bin", channel="nightly").status_code == 400

    monkeypatch.setattr(packages, "tools", lambda: {"rpm": False, "deb": False})
    resp = upload(client, csrf, b"\xed\xab\xee\xdb" + b"\0" * 100, "app.rpm")
    assert resp.status_code == 503 and "createrepo_c" in resp.get_json()["error"]
    monkeypatch.setattr(packages, "tools", lambda: {"rpm": True, "deb": True})
    resp = upload(client, csrf, b"!<arch>\ndebian-binary   " + b"\0" * 100, "app.deb")
    assert resp.status_code == 409 and "signing key" in resp.get_json()["error"]
    with app.app_context():
        assert Package.query.count() == 0
        assert not list(packages.incoming_dir().iterdir())


def test_a_failed_import_says_why_and_cleans_up(client, csrf, admin, run_jobs, monkeypatch, app):
    resp = upload(client, csrf, b"x", "fine.bin")

    def broken(*args, **kwargs):
        raise packages.RepoError("Something specific went wrong.")
    monkeypatch.setattr(packages, "publish_upload", broken)
    run_jobs()
    pkg = client.get(f"/api/v1/packages/{resp.get_json()['package']['id']}").get_json()["package"]
    assert pkg["status"] == "failed" and pkg["error"] == "Something specific went wrong."
    with app.app_context():
        assert not list(packages.incoming_dir().iterdir())


def test_package_addresses_need_a_package(client, admin):
    for path in ("/download/nothing-x86_64.rpm", "/download/beta/nothing", "/rpm/gnomish.repo",
                 "/deb/gnomish.sources", "/rpm/nightly/repodata/repomd.xml", "/deb/dists/nightly/InRelease"):
        assert client.get(path).status_code == 404, path


def test_tokens_need_the_releases_scope_to_upload(client, csrf, admin):
    token = client.post("/api/v1/tokens", json={"name": "ro", "scopes": ["read"]}, headers=h(csrf)).get_json()["token"]
    resp = client.post("/api/v1/packages", json={"url": "https://example.org/a.rpm"},
                       headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    assert client.get("/api/v1/packages", headers={"Authorization": f"Bearer {token}"}).status_code == 200


# ———————————————————————————— RPM and Debian, end to end ————————————————————————————

NEEDED = ("rpm", "rpmsign", "rpmbuild", "rpmkeys", "createrepo_c", "dpkg-deb", "gpg")
real_tools = pytest.mark.skipif(not all(shutil.which(t) for t in NEEDED),
                                reason="rpm, rpmbuild, createrepo_c, dpkg-deb and gpg are needed "
                                       "(run tools/test-in-docker.sh)")


def sh(*args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kwargs).stdout


def verify(tmp, key: bytes, *files):
    """Check a signature against the key alone, the way a client that
    trusts only this key would: a keyring of its own, nothing else in it."""
    home = tmp / f"gpg-{len(list(tmp.glob('gpg-*')))}"
    home.mkdir(mode=0o700)
    subprocess.run(["gpg", "--homedir", str(home), "--batch", "--import"], input=key, check=True, capture_output=True)
    sh("gpg", "--homedir", str(home), "--batch", "--verify", *map(str, files))


def build_rpm(tmp, version, release="1", arch="noarch"):
    top = tmp / f"rpmbuild-{version}-{release}"
    (top / "SPECS").mkdir(parents=True)
    spec = top / "SPECS" / "hello.spec"
    spec.write_text(f"""Name: hello
Version: {version}
Release: {release}
Summary: Says hello
License: MIT
BuildArch: {arch}
%description
Says hello.
%install
mkdir -p %{{buildroot}}/usr/bin
printf '#!/bin/sh\\necho hello {version}\\n' > %{{buildroot}}/usr/bin/hello
chmod 755 %{{buildroot}}/usr/bin/hello
%files
/usr/bin/hello
""")
    sh("rpmbuild", "--define", f"_topdir {top}", "-bb", str(spec))
    return next((top / "RPMS").rglob("*.rpm"))


def build_deb(tmp, version, arch="all"):
    root = tmp / f"deb-{version}"
    (root / "DEBIAN").mkdir(parents=True)
    (root / "usr" / "bin").mkdir(parents=True)
    (root / "usr" / "bin" / "hello").write_text(f"#!/bin/sh\necho hello {version}\n")
    (root / "DEBIAN" / "control").write_text(
        f"Package: hello\nVersion: {version}\nArchitecture: {arch}\nMaintainer: Ada <ada@example.org>\n"
        "Description: Says hello\n A longer description,\n over two lines.\n")
    out = tmp / f"hello_{version}_{arch}.deb"
    sh("dpkg-deb", "--root-owner-group", "--build", str(root), str(out))
    return out


def primary_xml(client, channel="stable") -> bytes:
    """The package list the current repomd.xml points to, as dnf reads it."""
    repomd = client.get(f"/rpm/{channel}/repodata/repomd.xml").get_data(as_text=True)
    href = re.search(r'<data type="primary">.*?<location href="([^"]+)"', repomd, re.S).group(1)
    return gzip.decompress(client.get(f"/rpm/{channel}/{href}").data)


@pytest.fixture()
def signed(client, csrf, admin, run_jobs):
    """A signing key, made the way the admin makes one."""
    assert client.post("/api/v1/repo/key", json={"action": "generate", "name": "Test"},
                       headers=h(csrf)).status_code == 202
    run_jobs()
    key = client.get("/api/v1/repo/key/public").get_json()
    assert key["fingerprint"]
    return key


@real_tools
def test_rpms_make_a_signed_dnf_repository(client, csrf, signed, run_jobs, tmp_path, app):
    first = build_rpm(tmp_path, "1.0.0")
    assert upload(client, csrf, first.read_bytes(), first.name, notes="First.").status_code == 202
    run_jobs()
    pkg = client.get("/api/v1/packages").get_json()["packages"][0]
    assert pkg["status"] == "live", client.get(f"/api/v1/packages/{pkg['id']}").get_json()["package"]["job"]["log"]
    assert (pkg["name"], pkg["version"], pkg["evr"], pkg["arch"]) == ("hello", "1.0.0", "1.0.0-1", "noarch")

    # The .repo file, the key it names, and the index, signed with that key.
    repo_file = client.get("/rpm/gnomish.repo").get_data(as_text=True)
    assert "[gnomish]" in repo_file and "baseurl=http://localhost/rpm/stable/" in repo_file
    assert "gpgcheck=1" in repo_file and "repo_gpgcheck=1" in repo_file
    key = client.get("/rpm/gnomish.asc").get_data(as_text=True)
    assert key.startswith("-----BEGIN PGP PUBLIC KEY BLOCK-----")
    (tmp_path / "key.asc").write_text(key)
    repomd = client.get("/rpm/stable/repodata/repomd.xml")
    assert repomd.headers["Cache-Control"] == "no-store"
    (tmp_path / "repomd.xml").write_bytes(repomd.data)
    (tmp_path / "repomd.xml.asc").write_bytes(client.get("/rpm/stable/repodata/repomd.xml.asc").data)
    verify(tmp_path, key.encode(), tmp_path / "repomd.xml.asc", tmp_path / "repomd.xml")

    # The package itself carries the repository's signature.
    served = client.get("/rpm/stable/packages/hello-1.0.0-1.noarch.rpm")
    assert served.status_code == 200 and "Content-Encoding" not in served.headers
    (tmp_path / "served.rpm").write_bytes(served.data)
    db_path = tmp_path / "rpmdb"
    db_path.mkdir()
    sh("rpmkeys", "--dbpath", str(db_path), "--import", str(tmp_path / "key.asc"))
    assert "signatures OK" in sh("rpmkeys", "--dbpath", str(db_path), "--checksig", str(tmp_path / "served.rpm"))
    assert b'href="packages/hello-1.0.0-1.noarch.rpm"' in primary_xml(client)
    assert client.get("/download/hello-noarch.rpm").data == served.data

    # The same version again is refused; an older one stays behind the newest.
    again = upload(client, csrf, first.read_bytes(), first.name)
    run_jobs()
    again = client.get(f"/api/v1/packages/{again.get_json()['package']['id']}").get_json()["package"]
    assert again["status"] == "failed" and "already in the stable repository" in again["error"]
    newer, older = build_rpm(tmp_path, "1.1.0"), build_rpm(tmp_path, "0.9.0")
    for f in (newer, older):
        upload(client, csrf, f.read_bytes(), f.name)
        run_jobs()
    live = client.get("/api/v1/packages?status=live&format=rpm").get_json()["packages"]
    assert [p["version"] for p in live] == ["1.1.0"]
    info = client.get("/api/v1/repo").get_json()["packages"]["rpm"]
    assert info["stable"]["version"] == "1.1.0" and info["stable"]["name"] == "hello"
    assert info["stable"]["downloads"] == {"noarch": "http://localhost/download/hello-noarch.rpm"}

    # Withdrawing the newest puts the one before back in front, and out of the index.
    newest = live[0]["id"]
    client.post(f"/api/v1/packages/{newest}/withdraw", headers=h(csrf))
    run_jobs()
    assert client.get("/api/v1/repo").get_json()["packages"]["rpm"]["stable"]["version"] == "1.0.0"
    assert b"hello-1.1.0" not in primary_xml(client) and b"hello-1.0.0" in primary_xml(client)
    log = client.get(f"/api/v1/packages/{newest}").get_json()["package"]["job"]["log"]
    assert "Installed copies keep 1.1.0" in log

    # Counted: the index as a check, the package as a download.
    with app.app_context():
        from flatout.models import StatDay
        assert StatDay.query.filter_by(metric="pkgcheck", target="rpm-stable").count() == 1
        assert StatDay.query.filter_by(metric="pkgpull").count() >= 1


@real_tools
def test_debian_packages_make_a_signed_apt_archive(client, csrf, signed, run_jobs, tmp_path, app):
    deb = build_deb(tmp_path, "1.0.0-1")
    assert upload(client, csrf, deb.read_bytes(), deb.name, channel="beta").status_code == 202
    run_jobs()
    pkg = client.get("/api/v1/packages").get_json()["packages"][0]
    assert pkg["status"] == "live", client.get(f"/api/v1/packages/{pkg['id']}").get_json()["package"]["job"]["log"]
    assert (pkg["name"], pkg["version"], pkg["evr"], pkg["arch"]) == ("hello", "1.0.0", "1.0.0-1", "all")

    sources = client.get("/deb/gnomish-beta.sources").get_data(as_text=True)
    assert "URIs: http://localhost/deb" in sources and "Suites: beta" in sources
    assert "Signed-By:\n -----BEGIN PGP PUBLIC KEY BLOCK-----\n .\n" in sources
    assert client.get("/deb/gnomish.sources").status_code == 404   # nothing on stable yet

    inrelease = client.get("/deb/dists/beta/InRelease")
    assert inrelease.headers["Cache-Control"] == "no-store"
    (tmp_path / "InRelease").write_bytes(inrelease.data)
    key = client.get("/deb/gnomish.gpg").data
    verify(tmp_path, key, tmp_path / "InRelease")
    release = client.get("/deb/dists/beta/Release").get_data(as_text=True)
    (tmp_path / "Release").write_text(release)
    (tmp_path / "Release.gpg").write_bytes(client.get("/deb/dists/beta/Release.gpg").data)
    verify(tmp_path, key, tmp_path / "Release.gpg", tmp_path / "Release")
    assert "Suite: beta" in release and "Architectures: amd64 arm64" in release

    # Every index the Release names is there, with the hash it says, and by that hash too.
    lines = release.split("SHA256:\n", 1)[1].strip().splitlines()
    assert lines
    for line in lines:
        digest, size, rel = line.split()
        data = client.get(f"/deb/dists/beta/{rel}").data
        assert hashlib.sha256(data).hexdigest() == digest and len(data) == int(size)
        folder = rel.rsplit("/", 1)[0]
        assert client.get(f"/deb/dists/beta/{folder}/by-hash/SHA256/{digest}").data == data
    packages_text = client.get("/deb/dists/beta/main/binary-amd64/Packages").get_data(as_text=True)
    assert "Package: hello\n" in packages_text and "Filename: pool/beta/hello_1.0.0-1_all.deb" in packages_text
    assert " over two lines." in packages_text
    served = client.get("/deb/pool/beta/hello_1.0.0-1_all.deb").data
    assert served == deb.read_bytes() and f"SHA256: {hashlib.sha256(served).hexdigest()}" in packages_text

    # Promoted to stable: the same file, now in the stable suite too.
    assert client.post("/api/v1/packages/promote", json={"from": "beta", "to": "stable", "format": "deb"},
                       headers=h(csrf)).status_code == 202
    run_jobs()
    assert "Filename: pool/stable/hello_1.0.0-1_all.deb" in \
        client.get("/deb/dists/stable/main/binary-arm64/Packages").get_data(as_text=True)
    assert client.get("/download/hello-all.deb").data == served
    assert client.get("/download/hello-beta-all.deb").data == served
    assert client.post("/api/v1/packages/promote", json={"from": "beta", "to": "stable"},
                       headers=h(csrf)).status_code == 409   # nothing left that stable lacks


@real_tools
def test_a_new_key_signs_the_packages_again(client, csrf, signed, run_jobs, tmp_path, app):
    rpm = build_rpm(tmp_path, "1.0.0")
    upload(client, csrf, rpm.read_bytes(), rpm.name)
    run_jobs()
    before = client.get("/rpm/stable/packages/hello-1.0.0-1.noarch.rpm").data
    assert client.post("/api/v1/repo/key", json={"action": "generate", "name": "New", "replace": True},
                       headers=h(csrf)).status_code == 202
    run_jobs()
    after = client.get("/rpm/stable/packages/hello-1.0.0-1.noarch.rpm").data
    assert after != before
    (tmp_path / "key.asc").write_text(client.get("/rpm/gnomish.asc").get_data(as_text=True))
    (tmp_path / "new.rpm").write_bytes(after)
    db_path = tmp_path / "rpmdb"
    db_path.mkdir()
    sh("rpmkeys", "--dbpath", str(db_path), "--import", str(tmp_path / "key.asc"))
    assert "signatures OK" in sh("rpmkeys", "--dbpath", str(db_path), "--checksig", str(tmp_path / "new.rpm"))
    pkg = client.get("/api/v1/packages").get_json()["packages"][0]
    assert pkg["file"]["sha256"] == hashlib.sha256(after).hexdigest()


# ———————————————————————————— The site ————————————————————————————

def test_the_install_dialog_offers_what_is_published(client, admin, app):
    from flatout.models import utcnow
    page = client.get("/").get_data(as_text=True)
    assert 'data-method="rpm"' not in page and "has no release yet" in page
    with app.app_context():
        db.session.add(Package(format="rpm", channel="stable", name="gnomish", version="1.2.0", evr="1.2.0-1",
                               arch="x86_64", status="live", path="rpm/stable/packages/gnomish-1.2.0-1.x86_64.rpm",
                               published_at=utcnow()))
        db.session.add(Package(format="file", channel="stable", name="Gnomish-x86_64.AppImage", version="1.2.0",
                               arch="x86_64", status="live", path="files/stable/2/Gnomish-x86_64.AppImage",
                               size=5 * 1048576, published_at=utcnow()))
        db.session.commit()
    page = client.get("/").get_data(as_text=True)
    assert "has no release yet" not in page and "Version 1.2.0" in page
    assert "sudo curl -fsSLo /etc/yum.repos.d/gnomish.repo http://localhost/rpm/gnomish.repo\nsudo dnf install gnomish" in page
    assert "http://localhost/download/gnomish-x86_64.rpm" in page
    assert 'data-method="files"' in page and "http://localhost/download/Gnomish-x86_64.AppImage" in page
    assert 'data-method="deb"' not in page and 'data-method="ref"' not in page

    # Each can be switched off in the site's install dialog settings.
    resp = client.patch("/api/v1/site", json={"install": {"rpm_method": False}}, headers=h(token_of(client)))
    assert resp.status_code == 200, resp.get_json()
    assert client.post("/api/v1/site/publish", json={}, headers=h(token_of(client))).status_code == 200
    page = client.get("/").get_data(as_text=True)
    assert 'data-method="rpm"' not in page and 'data-method="files"' in page


def token_of(client):
    from tests.conftest import token_for
    return token_for(client)
