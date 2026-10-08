"""Bundles sent in pieces, for proxies that cap request bodies.

The import itself needs flatpak and is in test_repo.py; here the pieces, and
the release they become, without it.
"""
import hashlib
import os
import time

import pytest

from flatout import chunks


@pytest.fixture()
def ready(monkeypatch, admin):
    # No flatpak or signing key needed to take in the pieces.
    monkeypatch.setattr("flatout.api._ready_for_releases", lambda: None)
    monkeypatch.setattr(chunks, "CHUNK_BYTES", 1000)


def h(csrf):
    return {"X-CSRF": csrf}


def put(client, csrf, upload_id, offset, data):
    return client.put(f"/api/v1/uploads/{upload_id}?offset={offset}", data=data, headers=h(csrf),
                      content_type="application/octet-stream")


def test_a_bundle_in_pieces_becomes_a_release(app, client, csrf, ready):
    bundle = os.urandom(2500)
    start = client.post("/api/v1/uploads", json={"size": len(bundle)}, headers=h(csrf))
    assert start.status_code == 201
    up = start.get_json()
    assert up["chunk_size"] == 1000

    assert put(client, csrf, up["id"], 0, bundle[:1000]).get_json()["received"] == 1000
    # The same piece again (its answer was lost) is taken once.
    assert put(client, csrf, up["id"], 0, bundle[:1000]).get_json()["received"] == 1000
    # One out of place says where to carry on from.
    gap = put(client, csrf, up["id"], 2000, bundle[2000:])
    assert gap.status_code == 409 and gap.get_json()["received"] == 1000
    # Too big a piece, and making the release too early, are refused.
    assert put(client, csrf, up["id"], 1000, bundle[1000:2001]).status_code == 413
    early = client.post("/api/v1/releases", json={"upload": up["id"], "channel": "beta"}, headers=h(csrf))
    assert early.status_code == 409 and early.get_json()["received"] == 1000

    put(client, csrf, up["id"], 1000, bundle[1000:2000])
    assert put(client, csrf, up["id"], 2000, bundle[2000:]).get_json() == {
        "id": up["id"], "received": 2500, "size": 2500}
    made = client.post("/api/v1/releases", json={"upload": up["id"], "channel": "beta", "notes": "Hi."},
                       headers=h(csrf))
    assert made.status_code == 202, made.get_json()
    rel = made.get_json()["release"]
    assert rel["channel"] == "beta" and rel["status"] == "queued" and rel["notes"] == "Hi."
    with app.app_context():
        from flatout.models import Release, db
        row = db.session.get(Release, rel["id"])
        assert row.bundle_size == 2500 and row.bundle_sha256 == hashlib.sha256(bundle).hexdigest()
    # The upload is used up.
    assert client.get(f"/api/v1/uploads/{up['id']}").status_code == 404


def test_uploads_are_checked(app, client, csrf, ready):
    assert client.post("/api/v1/uploads", json={"size": 0}, headers=h(csrf)).status_code == 400
    too_big = (app.config["MAX_UPLOAD_MB"] + 1) * 1024 * 1024
    assert client.post("/api/v1/uploads", json={"size": too_big}, headers=h(csrf)).status_code == 413
    assert put(client, csrf, "0" * 24, 0, b"x").status_code == 404
    assert put(client, csrf, "../../etc", 0, b"x").status_code == 404
    up = client.post("/api/v1/uploads", json={"size": 10}, headers=h(csrf)).get_json()
    assert put(client, csrf, up["id"], 0, b"x" * 11).status_code == 400   # more than the size
    assert client.delete(f"/api/v1/uploads/{up['id']}", headers=h(csrf)).status_code == 200
    assert client.get(f"/api/v1/uploads/{up['id']}").status_code == 404


def test_a_reader_token_cant_upload(app, client, csrf, ready):
    token = client.post("/api/v1/tokens", json={"name": "r"}, headers=h(csrf)).get_json()["token"]
    robot = app.test_client()
    resp = robot.post("/api/v1/uploads", json={"size": 10}, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_unfinished_uploads_are_dropped_after_a_day(tmp_path):
    fresh = chunks.start(tmp_path, 10)
    stale = chunks.start(tmp_path, 10)
    old = time.time() - 25 * 3600
    for path in [tmp_path / stale, *(tmp_path / stale).iterdir()]:
        os.utime(path, (old, old))
    assert chunks.prune(tmp_path) == 1
    assert (tmp_path / fresh).is_dir() and not (tmp_path / stale).exists()


def test_uploads_in_flight_count_against_free_space(app, client, csrf, ready):
    """Each upload on the way has promised its size; a new one is refused
    when all of them together wouldn't fit."""
    big = 100 * 1024 * 1024
    first = client.post("/api/v1/uploads", json={"size": big}, headers=h(csrf))
    assert first.status_code == 201
    from flatout import chunks as c
    with app.app_context():
        import flatout.api as api_module
        assert c.planned_total(api_module._uploads_dir()) == big
    # A second hundred megabytes still fits this disk.
    assert client.post("/api/v1/uploads", json={"size": big}, headers=h(csrf)).status_code == 201
    with app.app_context():
        assert c.planned_total(api_module._uploads_dir()) == 2 * big
    client.delete(f"/api/v1/uploads/{first.get_json()['id']}", headers=h(csrf))
    assert client.delete(f"/api/v1/uploads/{'0' * 24}", headers=h(csrf)).status_code == 404


def test_arrived_and_stalled_uploads_dont_count_twice(tmp_path):
    """Bytes already on disk have taken their room, so only what is still to
    come counts; an upload untouched for an hour (a closed tab, a retry that
    started over) no longer holds the disk until the day's prune."""
    import io
    import os
    import time
    from flatout import chunks as c
    arriving = c.start(tmp_path, 1000)
    c.add(tmp_path, arriving, 0, io.BytesIO(b"x" * 400), 400)
    assert c.planned_total(tmp_path) == 600
    stalled = c.start(tmp_path, 5000)
    assert c.planned_total(tmp_path) == 5600
    two_hours_ago = time.time() - 7200
    os.utime(tmp_path / stalled / "part", (two_hours_ago, two_hours_ago))
    assert c.planned_total(tmp_path) == 600
