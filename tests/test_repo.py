"""The repository, end to end, with the real flatpak and ostree.

Skipped where those aren't installed. They are in the Docker image, where
tools/test-in-docker.sh runs the whole suite, and in CI.

A tiny app is built from scratch (no runtime needed: the client installs it
with --no-deps), uploaded through the API, promoted, rolled back, and
installed by a real Flatpak client over HTTP.
"""
import os
import shutil
import subprocess
import threading

import pytest

pytestmark = pytest.mark.skipif(
    not all(shutil.which(t) for t in ("flatpak", "ostree", "gpg")),
    reason="flatpak, ostree and gpg are needed (run tools/test-in-docker.sh)",
)

APP = "org.example.Hello"


def sh(*args, cwd=None, env=None):
    return subprocess.run(args, cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout


def build_bundle(tmp, version):
    """A minimal app bundle, built the way flatpak-builder would end."""
    work = tmp / f"build-{version}"
    app = work / "app"
    (app / "files" / "bin").mkdir(parents=True)
    (app / "files" / "share" / "metainfo").mkdir(parents=True)
    (app / "export").mkdir()
    (app / "metadata").write_text(
        f"[Application]\nname={APP}\nruntime=org.freedesktop.Platform/x86_64/24.08\n"
        "sdk=org.freedesktop.Sdk/x86_64/24.08\ncommand=hello\n")
    hello = app / "files" / "bin" / "hello"
    hello.write_text(f"#!/bin/sh\necho hello {version}\n")
    hello.chmod(0o755)
    (app / "files" / "share" / "metainfo" / f"{APP}.metainfo.xml").write_text(
        f'<?xml version="1.0"?><component type="desktop-application"><id>{APP}</id><name>Hello</name>'
        f'<summary>Says hello</summary><releases><release version="{version}" date="2026-10-01"/></releases></component>')
    repo = work / "repo"
    sh("ostree", "init", "--mode=archive-z2", f"--repo={repo}")
    sh("flatpak", "build-export", str(repo), str(app), "stable")
    bundle = work / "hello.flatpak"
    sh("flatpak", "build-bundle", "--runtime-repo=https://dl.flathub.org/repo/flathub.flatpakrepo",
       str(repo), str(bundle), APP, "stable")
    return bundle


@pytest.fixture()
def run_jobs(app):
    from flatout import jobs

    def run():
        jobs.run_all_queued(app)
    return run


def upload(client, csrf, bundle, channel, notes=""):
    with open(bundle, "rb") as f:
        resp = client.post("/api/v1/releases", data={"file": (f, "hello.flatpak"), "channel": channel, "notes": notes},
                           headers={"X-CSRF": csrf}, content_type="multipart/form-data")
    assert resp.status_code == 202, resp.get_json()
    return resp.get_json()["release"]["id"]


def release(client, rid):
    return client.get(f"/api/v1/releases/{rid}").get_json()["release"]


def test_the_whole_life_of_a_release(app, client, csrf, admin, run_jobs, tmp_path):
    h = {"X-CSRF": csrf}

    # No key yet: uploads are refused with a reason.
    assert client.post("/api/v1/releases", json={"url": "https://example.org/x.flatpak"}, headers=h).status_code == 409
    assert client.post("/api/v1/repo/key", json={"action": "generate", "name": "Test"}, headers=h).status_code == 202
    run_jobs()
    key = client.get("/api/v1/repo").get_json()["signing_key"]
    assert key and len(key["fingerprint"]) == 40
    assert client.post("/api/v1/repo/key", json={"action": "generate"}, headers=h).status_code == 409

    # A beta, read from the bundle: the app ID, the architecture, the version.
    beta_id = upload(client, csrf, build_bundle(tmp_path, "1.2.3"), "beta", "First beta.")
    run_jobs()
    beta = release(client, beta_id)
    assert beta["status"] == "live", beta
    assert (beta["app_id"], beta["version"], beta["channel"]) == (APP, "1.2.3", "beta")
    ref = client.get(f"/flatpak/{APP}-beta.flatpakref").data.decode()
    assert "Branch=beta" in ref and "GPGKey=" in ref and f"Name={APP}" in ref

    # Promoted to stable, without uploading again.
    assert client.post("/api/v1/releases/promote", json={"from": "beta", "to": "stable"}, headers=h).status_code == 202
    run_jobs()
    repo = client.get("/api/v1/repo").get_json()
    assert repo["stable"]["version"] == "1.2.3" and repo["beta"]["version"] == "1.2.3"

    # A newer stable replaces it; the old one can come back.
    new_id = upload(client, csrf, build_bundle(tmp_path, "1.2.4"), "stable", "Fixes.")
    run_jobs()
    assert release(client, new_id)["status"] == "live"
    stable_releases = client.get("/api/v1/releases?channel=stable").get_json()["releases"]
    old = next(r for r in stable_releases if r["version"] == "1.2.3")
    assert old["status"] == "superseded"
    resp = client.post(f"/api/v1/releases/{old['id']}/rollback", headers=h)
    assert resp.status_code == 202
    run_jobs()
    back = release(client, resp.get_json()["release"]["id"])
    assert back["status"] == "live" and back["version"] == "1.2.3" and back["commit"] != old["commit"]

    # The site shows it, and the beta section is there while a beta is live.
    client.post("/api/v1/site/publish", json={}, headers=h)
    home = client.get("/").data.decode()
    assert "Version 1.2.3" in home and 'id="beta"' in home
    assert client.get(f"/download/{APP}-{beta['arch']}.flatpak").status_code == 200

    # A real client adds the repository and installs from it, with the site
    # in maintenance: what installs fetch isn't part of the website.
    assert client.patch("/api/v1/site/status", json={"status": "maintenance"}, headers=h).status_code == 200
    from werkzeug.serving import make_server
    server = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        env = dict(os.environ, FLATPAK_USER_DIR=str(tmp_path / "client"), HOME=str(tmp_path))
        remote = repo["remote_name"]
        sh("flatpak", "remote-add", "--user", remote, f"{base}/flatpak/{remote}.flatpakrepo", env=env)
        listing = sh("flatpak", "remote-ls", "--user", "--columns=application,branch", remote, env=env)
        assert f"{APP}\tstable" in listing and f"{APP}\tbeta" in listing
        # --no-deploy downloads and verifies the signed commit but skips the
        # deploy step, which wants a session bus that a container doesn't have.
        done = subprocess.run(["flatpak", "install", "--user", "--noninteractive", "--no-deps", "--no-deploy",
                               remote, f"{APP}//stable"], env=env, capture_output=True, text=True)
        assert done.returncode == 0, done.stdout + done.stderr
    finally:
        server.shutdown()
    # The client's update checks were counted.
    assert client.get("/api/v1/stats").get_json()["today"] >= 1

    client.patch("/api/v1/site/status", json={"status": "published"}, headers=h)

    # Ending the beta tells installs so, and the beta section goes.
    assert client.post("/api/v1/channels/beta/end", json={"message": "Over."}, headers=h).status_code == 202
    run_jobs()
    assert client.get("/api/v1/repo").get_json()["beta"] is None
    assert 'id="beta"' not in client.get("/").data.decode()


def test_a_broken_bundle_fails_with_its_reason(app, client, csrf, admin, run_jobs, tmp_path):
    h = {"X-CSRF": csrf}
    client.post("/api/v1/repo/key", json={"action": "generate", "name": "Test"}, headers=h)
    run_jobs()
    junk = tmp_path / "junk.flatpak"
    junk.write_bytes(b"not a bundle at all")
    rid = upload(client, csrf, junk, "stable")
    run_jobs()
    rel = release(client, rid)
    assert rel["status"] == "failed" and "flatpak failed" in rel["error"]
    assert "FAILED" in rel["job"]["log"]


def test_a_bundle_sent_in_pieces_is_published(app, client, csrf, admin, run_jobs, tmp_path, monkeypatch):
    from flatout import chunks
    h = {"X-CSRF": csrf}
    client.post("/api/v1/repo/key", json={"action": "generate", "name": "Test"}, headers=h)
    run_jobs()
    monkeypatch.setattr(chunks, "CHUNK_BYTES", 1024)
    data = build_bundle(tmp_path, "4.0").read_bytes()
    up = client.post("/api/v1/uploads", json={"size": len(data)}, headers=h).get_json()
    offset = 0
    while offset < len(data):
        resp = client.put(f"/api/v1/uploads/{up['id']}?offset={offset}", data=data[offset:offset + 1024],
                          headers=h, content_type="application/octet-stream")
        offset = resp.get_json()["received"]
    made = client.post("/api/v1/releases", json={"upload": up["id"], "channel": "stable"}, headers=h)
    assert made.status_code == 202
    run_jobs()
    rel = release(client, made.get_json()["release"]["id"])
    assert rel["status"] == "live" and rel["version"] == "4.0", rel
    assert client.get(f"/download/{APP}-{rel['arch']}.flatpak").data == data
