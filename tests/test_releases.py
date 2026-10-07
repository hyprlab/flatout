"""What the admin says about releases, without the flatpak tools.

The repository itself is exercised end to end in test_repo.py; these put
release rows in place directly and check what the site and the admin make of
them.
"""
import pytest

from flatout.models import Release, db, utcnow


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


def test_releases_of_another_app_are_named(client, csrf, live_release):
    live_release("org.example.Other")
    client.patch("/api/v1/site", json={"app": {"app_id": "org.example.Mine"}}, headers=h(csrf))
    client.post("/api/v1/site/publish", json={}, headers=h(csrf))

    repo = client.get("/api/v1/repo").get_json()
    assert repo["app_id"] == "org.example.Mine"
    assert repo["unmatched_app_ids"] == ["org.example.Other"]
    assert repo["stable"] is None
    body = client.get("/admin").data.decode()
    assert "doesn&#39;t show your live releases" in body or "doesn't show your live releases" in body
    assert "org.example.Other" in body and "Change the app ID" in body


def test_a_fixed_draft_only_needs_publishing(client, csrf, live_release):
    live_release("org.example.Other")
    client.patch("/api/v1/site", json={"app": {"app_id": "org.example.Mine"}}, headers=h(csrf))
    client.post("/api/v1/site/publish", json={}, headers=h(csrf))
    client.patch("/api/v1/site", json={"app": {"app_id": "org.example.Other"}}, headers=h(csrf))

    assert client.get("/api/v1/repo").get_json()["draft_app_id"] == "org.example.Other"
    assert "publish the site</a> to apply it" in client.get("/admin").data.decode()

    client.post("/api/v1/site/publish", json={}, headers=h(csrf))
    repo = client.get("/api/v1/repo").get_json()
    assert repo["unmatched_app_ids"] == [] and repo["stable"]["version"] == "1.0.0"
    assert "show your live releases" not in client.get("/admin").data.decode()


def test_no_warning_while_the_site_names_no_app(client, csrf, live_release):
    live_release("org.example.Other")
    repo = client.get("/api/v1/repo").get_json()
    assert repo["app_id"] == "org.example.Other" and repo["unmatched_app_ids"] == []
