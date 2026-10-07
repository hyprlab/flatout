"""Who sees the site: published, maintenance, unpublished."""
from datetime import datetime, timedelta, timezone


def h(csrf):
    return {"X-CSRF": csrf}


def set_status(client, csrf, **body):
    return client.patch("/api/v1/site/status", json=body, headers=h(csrf))


def test_a_new_install_is_unpublished_until_its_first_publish(app, client, csrf, admin):
    visitor = app.test_client()
    page = visitor.get("/")
    assert page.status_code == 200 and b"Coming soon" in page.data
    assert page.headers["X-Robots-Tag"] == "noindex"
    assert "Disallow: /\n" in visitor.get("/robots.txt").data.decode()
    # The owner sees the site, with a word on what visitors get.
    mine = client.get("/").data.decode()
    assert "coming-soon page; you see the site" in mine and "Coming soon" not in mine

    client.post("/api/v1/site/publish", json={}, headers=h(csrf))
    assert client.get("/api/v1/site/status").get_json()["status"] == "published"
    page = visitor.get("/")
    assert page.status_code == 200 and b"Coming soon" not in page.data and b"Gnomish" in page.data


def test_maintenance_answers_503_but_keeps_serving_updates(app, client, csrf, admin):
    client.post("/api/v1/site/publish", json={}, headers=h(csrf))
    until = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%MZ")
    resp = set_status(client, csrf, status="maintenance",
                      pages={"maintenance": {"title": "Polishing {app_name}", "until": until}})
    assert resp.status_code == 200 and resp.get_json()["status"] == "maintenance"

    visitor = app.test_client()
    for path in ("/", "/privacy", "/no-such-page"):
        page = visitor.get(path)
        assert page.status_code == 503, path
        assert b"Polishing Gnomish" in page.data and b"Updates keep arriving" in page.data
    assert 6000 < int(visitor.get("/").headers["Retry-After"]) <= 7200
    assert b"<url>" not in visitor.get("/sitemap.xml").data
    # What installs fetch is not part of the website.
    assert visitor.get("/healthz").status_code == 200
    assert visitor.get("/repo/summary").status_code == 404     # no repository yet, but not the maintenance page
    assert b"Polishing" not in visitor.get("/repo/summary").data


def test_publishing_keeps_a_chosen_status(app, client, csrf, admin):
    client.post("/api/v1/site/publish", json={}, headers=h(csrf))
    set_status(client, csrf, status="unpublished")
    published = client.post("/api/v1/site/publish", json={}, headers=h(csrf)).get_json()
    assert published["site_status"] == "unpublished"
    assert b"Coming soon" in app.test_client().get("/").data
    set_status(client, csrf, status="published")
    assert b"Coming soon" not in app.test_client().get("/").data


def test_status_changes_are_checked_whole(client, csrf, admin):
    resp = set_status(client, csrf, status="sleeping", pages={"maintenance": {"title": "", "until": "soon"}})
    assert resp.status_code == 422
    paths = {e["path"] for e in resp.get_json()["errors"]}
    assert paths == {"$.status", "$.pages.maintenance.title", "$.pages.maintenance.until"}
    assert client.get("/api/v1/site/status").get_json()["status"] == "unpublished"
    assert set_status(client, csrf, colour="red").status_code == 400


def test_the_admin_can_preview_both_pages(client, csrf, admin):
    set_status(client, csrf, pages={"unpublished": {"title": "Almost there"}})
    page = client.get("/admin/preview/status/unpublished")
    assert page.status_code == 200 and b"Almost there" in page.data
    assert client.get("/admin/preview/status/maintenance").status_code == 200
    assert client.get("/admin/preview/status/other").status_code == 404


def test_tokens_and_agents_can_switch_it(app, client, csrf, admin):
    reader = client.post("/api/v1/tokens", json={"name": "r"}, headers=h(csrf)).get_json()["token"]
    writer = client.post("/api/v1/tokens", json={"name": "w", "scopes": ["site"]}, headers=h(csrf)).get_json()["token"]
    robot = app.test_client()
    assert robot.patch("/api/v1/site/status", json={"status": "maintenance"},
                       headers={"Authorization": f"Bearer {reader}"}).status_code == 403
    call = robot.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "set_site_status", "arguments": {"status": "maintenance"}}},
        headers={"Authorization": f"Bearer {writer}"}).get_json()
    assert call["result"]["isError"] is False
    assert robot.get("/").status_code == 503
