"""Backups: made under Settings, restored by a fresh install's wizard.

Needs gpg, which the host and the Docker image both have; skipped without.
"""
import base64
import importlib
import shutil

import pytest

pytestmark = pytest.mark.skipif(not shutil.which("gpg"), reason="gpg is needed")

PASSPHRASE = "correct horse battery staple"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def h(csrf):
    return {"X-CSRF": csrf}


def make_backup(app, client, csrf):
    from flatout import jobs
    resp = client.post("/api/v1/backup", json={"passphrase": PASSPHRASE}, headers=h(csrf))
    assert resp.status_code == 202, resp.get_json()
    jobs.run_all_queued(app)
    info = client.get("/api/v1/backup").get_json()
    assert info["job"]["status"] == "done", info["job"]["log"]
    download = client.get("/api/v1/backup/download")
    assert download.status_code == 200
    return download.data, info


@pytest.fixture()
def source(app, client, csrf, admin):
    """An install with something in it: a renamed site and an image."""
    client.patch("/api/v1/site", json={"app": {"name": "Restored App"}}, headers=h(csrf))
    client.post("/api/v1/site/publish", json={}, headers=h(csrf))
    up = client.post("/api/v1/media", json={"filename": "dot.png", "data_base64": base64.b64encode(PNG).decode()},
                     headers=h(csrf))
    assert up.status_code == 201, up.get_json()
    return up.get_json()["media"]["url"]


@pytest.fixture()
def fresh(tmp_path_factory, monkeypatch):
    """A second, fresh install in a data directory of its own."""
    def make():
        data = tmp_path_factory.mktemp("fresh")
        monkeypatch.setenv("DATA_DIR", str(data))
        import flatout
        import flatout.config
        import flatout.setup
        importlib.reload(flatout.config)
        flatout.setup._completed["done"] = False

        class TestConfig(flatout.config.Config):
            TESTING = True
        app = flatout.create_app(TestConfig)
        client = app.test_client()
        from tests.conftest import token_for
        return app, client, token_for(client)
    return make


def upload(client, csrf, data, chunk=None):
    start = client.post("/setup/restore", json={"size": len(data)}, headers=h(csrf))
    assert start.status_code == 201, start.get_json()
    upload_id, size = start.get_json()["id"], chunk or start.get_json()["chunk_size"]
    offset = 0
    while offset < len(data):
        piece = data[offset:offset + size]
        resp = client.put(f"/setup/restore/{upload_id}?offset={offset}", data=piece, headers=h(csrf),
                          content_type="application/octet-stream")
        assert resp.status_code == 200, resp.get_json()
        offset = resp.get_json()["received"]
    return upload_id


def test_a_backup_is_encrypted_and_only_for_admins(app, client, csrf, source, second_user):
    data, info = make_backup(app, client, csrf)
    assert data[:1] in (b"\x8c", b"\xc3")            # an OpenPGP symmetric-key packet, not a tar
    assert b"Restored App" not in data and PNG not in data
    assert info["backup"]["name"].endswith(".tar.gpg") and info["backup"]["size"] == len(data)
    assert "Wrote" in info["job"]["log"]

    other, token = second_user
    assert other.get("/api/v1/backup").status_code == 403
    assert client.post("/api/v1/backup", json={"passphrase": "short"}, headers=h(csrf)).status_code == 400
    reader = client.post("/api/v1/tokens", json={"name": "r", "scopes": ["releases", "site"]},
                         headers=h(csrf)).get_json()["token"]
    assert app.test_client().get("/api/v1/backup/download",
                                 headers={"Authorization": f"Bearer {reader}"}).status_code == 403

    # One backup is kept; deleting it leaves none.
    assert client.delete("/api/v1/backup", headers=h(csrf)).get_json()["backup"] is None
    assert client.get("/api/v1/backup/download").status_code == 404


def test_a_fresh_install_restores_it_in_pieces(app, client, csrf, source, fresh, monkeypatch):
    from flatout import repo
    with app.app_context():
        fingerprint = repo.generate_key("Test", "")
    data, _ = make_backup(app, client, csrf)
    new_app, new_client, new_csrf = fresh()
    from flatout import backup
    monkeypatch.setattr(backup, "CHUNK_BYTES", 4096)
    upload_id = upload(new_client, new_csrf, data, chunk=4096)

    # A piece sent twice is taken once; one out of place is refused.
    again = new_client.put(f"/setup/restore/{upload_id}?offset=0", data=data[:4096], headers=h(new_csrf),
                           content_type="application/octet-stream")
    assert again.status_code == 200 and again.get_json()["received"] == len(data)
    gap = new_client.put(f"/setup/restore/{upload_id}?offset={len(data) + 10}", data=b"x", headers=h(new_csrf),
                         content_type="application/octet-stream")
    assert gap.status_code == 409

    wrong = new_client.post(f"/setup/restore/{upload_id}/check", json={"passphrase": "not the passphrase"},
                            headers=h(new_csrf))
    assert wrong.status_code == 400 and "passphrase" in wrong.get_json()["error"]
    right = new_client.post(f"/setup/restore/{upload_id}/check", json={"passphrase": PASSPHRASE},
                            headers=h(new_csrf))
    assert right.status_code == 200 and right.get_json()["backup"]["version"]

    done = new_client.post(f"/setup/restore/{upload_id}/finish", json={"passphrase": PASSPHRASE},
                           headers=h(new_csrf))
    assert done.status_code == 202
    assert new_client.get(f"/setup/restore/{upload_id}").get_json()["state"] == "done"

    # Everything is back: the account, the site, the image; setup is over.
    signin = new_app.test_client()
    from tests.conftest import token_for
    token = token_for(signin)
    resp = signin.post("/login", data={"_csrf": token, "username": "admin@example.com", "password": "password1"})
    assert resp.status_code == 302 and "/setup" not in resp.headers["Location"]
    assert "Restored App" in signin.get("/").data.decode()
    assert signin.get(source).data == PNG
    with new_app.app_context():
        assert repo.key_fingerprint() == fingerprint      # the signing key came with it
    jobs = signin.get("/api/v1/jobs").get_json()["jobs"]
    assert jobs[0]["kind"] == "backup" and jobs[0]["status"] == "done"
    assert new_client.post("/setup/restore", json={"size": 10}, headers=h(new_csrf)).status_code == 409


def test_what_isnt_a_backup_is_refused(fresh):
    new_app, new_client, new_csrf = fresh()
    upload_id = upload(new_client, new_csrf, b"just some text, not a backup" * 10)
    resp = new_client.post(f"/setup/restore/{upload_id}/check", json={"passphrase": PASSPHRASE},
                           headers=h(new_csrf))
    assert resp.status_code == 400 and "isn't a Flatout backup" in resp.get_json()["error"]
    resp = new_client.post(f"/setup/restore/{upload_id}/finish", json={"passphrase": PASSPHRASE},
                           headers=h(new_csrf))
    assert resp.status_code == 202
    status = new_client.get(f"/setup/restore/{upload_id}").get_json()
    assert status["state"] == "failed" and "isn't a Flatout backup" in status["error"]
    # Nothing was replaced: the install is still fresh.
    assert new_client.get("/setup").status_code == 200


def test_an_unfinished_upload_isnt_restored(fresh):
    new_app, new_client, new_csrf = fresh()
    start = new_client.post("/setup/restore", json={"size": 1000}, headers=h(new_csrf)).get_json()
    resp = new_client.post(f"/setup/restore/{start['id']}/finish", json={"passphrase": PASSPHRASE},
                           headers=h(new_csrf))
    assert resp.status_code == 400 and "finished uploading" in resp.get_json()["error"]
    big = new_client.post("/setup/restore", json={"size": 10 ** 15}, headers=h(new_csrf))
    assert big.status_code == 400 and "disk space" in big.get_json()["error"]
