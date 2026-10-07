"""What the admin says about releases, and the app settings, without the
flatpak tools.

The repository itself is exercised end to end in test_repo.py; these put
release rows in place directly and check what the site and the admin make of
them.
"""
import json

import pytest

from flatout.models import Release, SiteDocument, db, get_setting, utcnow


@pytest.fixture()
def live_release(app, admin):
    def make(app_id, channel="stable", version="1.0.0"):
        with app.app_context():
            db.session.add(Release(app_id=app_id, arch="x86_64", channel=channel, version=version,
                                   status="live", commit="a" * 64, published_at=utcnow()))
            db.session.commit()
    return make


def h(csrf):
    return {"X-CSRF": csrf}


def settings(client, csrf, **values):
    return client.patch("/api/v1/repo/settings", json=values, headers=h(csrf))


def test_releases_of_another_app_are_named(client, csrf, live_release):
    live_release("org.example.Other")
    assert settings(client, csrf, app_id="org.example.Mine").status_code == 200

    repo = client.get("/api/v1/repo").get_json()
    assert repo["app_id"] == "org.example.Mine" and repo["settings"]["app_id"] == "org.example.Mine"
    assert repo["unmatched_app_ids"] == ["org.example.Other"]
    assert repo["release_app_ids"] == ["org.example.Other"]
    assert repo["stable"] is None
    body = client.get("/admin").data.decode()
    assert "show your live releases" in body and "org.example.Other" in body
    assert "Repository &gt; App" in body


def test_the_app_id_applies_at_once(client, csrf, live_release):
    live_release("org.example.Other")
    settings(client, csrf, app_id="org.example.Mine")
    settings(client, csrf, app_id="org.example.Other")
    repo = client.get("/api/v1/repo").get_json()
    assert repo["unmatched_app_ids"] == [] and repo["stable"]["version"] == "1.0.0"
    assert "show your live releases" not in client.get("/admin").data.decode()
    assert b"org.example.Other.flatpakref" in client.get("/").data


def test_no_warning_while_no_app_id_is_set(client, csrf, live_release):
    live_release("org.example.Other")
    repo = client.get("/api/v1/repo").get_json()
    assert repo["app_id"] == "org.example.Other" and repo["unmatched_app_ids"] == []


def test_app_settings_are_checked_whole(client, csrf, admin):
    resp = settings(client, csrf, app_id="org.example.Fine", remote_name="bad name")
    assert resp.status_code == 400 and "remote_name" in resp.get_json()["error"]
    assert client.get("/api/v1/repo").get_json()["settings"]["app_id"] == ""
    assert settings(client, csrf, app_id="not-an-id").status_code == 400
    assert settings(client, csrf, colour="red").status_code == 400
    resp = settings(client, csrf, app_id="org.example.Fine", remote_name="fine-remote")
    assert resp.get_json()["remote_name"] == "fine-remote"
    assert settings(client, csrf, remote_name="").get_json()["remote_name"] == "gnomish"


def test_the_site_document_points_to_the_new_place(client, csrf, admin):
    resp = client.patch("/api/v1/site", json={"app": {"app_id": "org.example.App"}}, headers=h(csrf))
    assert resp.status_code == 422
    assert "Repository > App" in resp.get_json()["errors"][0]["message"]


def test_saved_values_move_to_settings(app, admin):
    """A document saved while the app ID was part of the site gives it up to
    the repository settings at the next start."""
    with app.app_context():
        for name, app_id in (("live", "org.example.Live"), ("draft", "org.example.Draft")):
            row = db.session.get(SiteDocument, name)
            doc = json.loads(row.data)
            doc["app"].update(app_id=app_id, remote_name="old-remote")
            row.data = json.dumps(doc)
        db.session.commit()
        from flatout import _migrate
        _migrate(app)
        assert get_setting("app_id") == "org.example.Live"
        assert get_setting("remote_name") == "old-remote"
        for name in ("live", "draft"):
            assert "app_id" not in json.loads(db.session.get(SiteDocument, name).data)["app"]


def test_an_old_revision_restores_without_its_app_id(app, client, csrf, admin):
    from flatout.models import SiteRevision
    client.post("/api/v1/site/publish", json={}, headers=h(csrf))
    with app.app_context():
        rev = SiteRevision.query.first()
        doc = json.loads(rev.data)
        doc["app"]["app_id"] = "org.example.Old"
        rev.data = json.dumps(doc)
        db.session.commit()
        rev_id = rev.id
    resp = client.post(f"/api/v1/site/revisions/{rev_id}/restore", headers=h(csrf))
    assert resp.status_code == 200 and "app_id" not in resp.get_json()["document"]["app"]
