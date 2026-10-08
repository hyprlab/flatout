"""The site document: the schema, the draft and live copies, and rendering."""
import base64
import json
import struct
import zlib

import pytest

from flatout import site_schema


def png(width: int, height: int) -> bytes:
    """A real, tiny PNG: grey pixels."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = b"".join(b"\x00" + b"\x80" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


PNG = png(2, 3)


def h(csrf):
    return {"X-CSRF": csrf}


def test_the_placeholder_site_validates_unchanged():
    doc = site_schema.default_document()
    assert site_schema.validate(doc) == doc


def test_every_problem_is_reported_with_its_path():
    doc = site_schema.default_document()
    doc["theme"]["light"]["accent"] = "blue"
    doc["sections"][0]["title"] = "two\nlines"
    doc["sections"].append(dict(doc["sections"][0]))
    doc["pages"][0]["slug"] = "admin"
    doc["nav"]["unknown"] = 1
    with pytest.raises(site_schema.Invalid) as err:
        site_schema.validate(doc)
    paths = {e["path"] for e in err.value.errors}
    assert {"$.theme.light.accent", "$.sections[0].title", "$.sections[11].id", "$.pages[0].slug",
            "$.nav.unknown"} <= paths


def test_a_partial_document_takes_defaults():
    doc = site_schema.validate({"app": {"name": "Hello"}})
    assert doc["app"]["name"] == "Hello" and doc["theme"]["font_size"] == 16
    assert [s["type"] for s in doc["sections"]][:2] == ["hero", "features"]


def test_changes_go_to_the_draft_until_published(client, csrf, admin):
    resp = client.patch("/api/v1/site", json={"app": {"name": "Draftish"}}, headers=h(csrf))
    assert resp.status_code == 200 and resp.get_json()["has_unpublished_changes"] is True
    assert b"Draftish" not in client.get("/").data
    assert b"Draftish" in client.get("/admin/preview").data
    client.post("/api/v1/site/publish", json={"note": "rename"}, headers=h(csrf))
    assert b"Draftish" in client.get("/").data
    revisions = client.get("/api/v1/site/revisions").get_json()["revisions"]
    assert revisions[0]["note"] == "rename"


def test_an_invalid_change_is_refused_whole(client, csrf, admin):
    resp = client.patch("/api/v1/site", json={"app": {"name": "Fine"}, "theme": {"radius": 99}}, headers=h(csrf))
    assert resp.status_code == 422
    assert resp.get_json()["errors"][0]["path"] == "$.theme.radius"
    assert client.get("/api/v1/site").get_json()["document"]["app"]["name"] == "Gnomish"


def test_discard_and_restore(client, csrf, admin):
    client.post("/api/v1/site/publish", json={}, headers=h(csrf))
    first = client.get("/api/v1/site/revisions").get_json()["revisions"][0]["id"]
    client.patch("/api/v1/site", json={"app": {"name": "Second"}}, headers=h(csrf))
    client.post("/api/v1/site/publish", json={}, headers=h(csrf))
    client.patch("/api/v1/site", json={"app": {"name": "Unwanted"}}, headers=h(csrf))
    doc = client.post("/api/v1/site/discard", headers=h(csrf)).get_json()["document"]
    assert doc["app"]["name"] == "Second"
    doc = client.post(f"/api/v1/site/revisions/{first}/restore", headers=h(csrf)).get_json()["document"]
    assert doc["app"]["name"] == "Gnomish"


def test_a_stale_editor_gets_a_conflict(client, csrf, admin):
    base = client.get("/api/v1/site").get_json()["updated"]["updated_at"]
    client.patch("/api/v1/site", json={"app": {"tagline": "Someone else"}}, headers=h(csrf))
    doc = client.get("/api/v1/site").get_json()["document"]
    resp = client.put("/api/v1/site", json=doc, headers={**h(csrf), "X-Draft-Base": base})
    assert resp.status_code == 409 and "changed the draft" in resp.get_json()["error"]


def test_sections_one_at_a_time(client, csrf, admin):
    added = client.post("/api/v1/site/sections", json={"type": "faq", "after": "top",
                                                       "values": {"title": "Asked often"}}, headers=h(csrf))
    assert added.status_code == 201 and added.get_json()["position"] == 1
    sid = added.get_json()["section"]["id"]
    client.patch(f"/api/v1/site/sections/{sid}", json={"items": [{"question": "Why?", "answer": "**Because.**"}]},
                 headers=h(csrf))
    body = client.get("/admin/preview").data.decode()
    assert "Asked often" in body and "<strong>Because.</strong>" in body
    ids = [s["id"] for s in client.get("/api/v1/site/sections").get_json()["sections"]]
    resp = client.post("/api/v1/site/sections/order", json={"ids": list(reversed(ids))}, headers=h(csrf))
    assert resp.get_json()["order"] == list(reversed(ids))
    assert client.post("/api/v1/site/sections/order", json={"ids": ids[:-1]}, headers=h(csrf)).status_code == 400
    assert client.delete(f"/api/v1/site/sections/{sid}", headers=h(csrf)).status_code == 200
    assert client.get(f"/api/v1/site/sections/{sid}").status_code == 404


def test_hidden_sections_and_placeholders(client, csrf, admin):
    client.patch("/api/v1/site/sections/top", json={"title": "Meet {app_name}"}, headers=h(csrf))
    client.patch("/api/v1/site/sections/features", json={"enabled": False}, headers=h(csrf))
    body = client.get("/admin/preview").data.decode()
    assert "Meet Gnomish" in body and 'id="features"' not in body


def test_markdown_is_sanitized(client, csrf, admin):
    client.patch("/api/v1/site/sections/features", json={
        "cards": [{"icon": "bolt", "color": "blue", "title": "X",
                   "body": "<script>alert(1)</script> [out](javascript:alert(1)) [ok](https://example.org)"}]},
        headers=h(csrf))
    body = client.get("/admin/preview").data.decode()
    assert "<script>alert" not in body and "javascript:" not in body
    assert 'href="https://example.org" target="_blank"' in body


def test_the_sanitizer_closes_what_input_left_open():
    from flatout.sanitize import sanitize_html
    assert sanitize_html("<blockquote><pre>unfinished") == "<blockquote><pre>unfinished</pre></blockquote>"
    assert sanitize_html("<p>fine</p></em><p>also fine") == "<p>fine</p><p>also fine</p>"
    assert sanitize_html("<h2>head") == "<h3>head</h3>"   # demoted, and closed


def test_a_filled_link_is_checked_for_its_scheme(app):
    from flatout import site
    with app.test_request_context("/"):
        r = site.Renderer(site_schema.default_document())
        assert r.link("https://example.org/x") == "https://example.org/x"
        assert r.link("#install") == "#install"
        assert r.link("javascript:alert(1)") == ""
        r.values["site_url"] = "javascript:alert(1)"
        assert r.link("{site_url}") == ""


def test_theme_reaches_the_page(client, csrf, admin):
    client.patch("/api/v1/site", json={"theme": {"light": {"accent": "#123456"}, "font_body": "inter",
                                                 "font_size": 18}}, headers=h(csrf))
    body = client.get("/admin/preview").data.decode()
    assert "--accent: #123456" in body and '"Inter"' in body and "--font-size: 18px" in body


def test_pages_live_at_their_address(client, csrf, admin):
    client.patch("/api/v1/site", json={"pages": [
        {"slug": "privacy", "title": "Privacy", "body": "## Data\nNone kept.", "published": True},
        {"slug": "draft-page", "title": "Hidden", "body": "x", "published": False},
    ]}, headers=h(csrf))
    client.post("/api/v1/site/publish", json={}, headers=h(csrf))
    page = client.get("/privacy")
    assert page.status_code == 200 and b"<h2>Data</h2>" in page.data
    assert client.get("/draft-page").status_code == 404
    assert client.get("/admin/preview/draft-page").status_code == 200
    assert b"/privacy" in client.get("/sitemap.xml").data


def test_media_upload_use_and_delete(client, csrf, admin):
    up = client.post("/api/v1/media", json={"filename": "dot.png", "data_base64": base64.b64encode(PNG).decode()},
                     headers=h(csrf))
    assert up.status_code == 201
    item = up.get_json()["media"]
    assert item["kind"] == "image" and (item["width"], item["height"]) == (2, 3)
    assert client.get(item["url"]).status_code == 200
    # The same file twice is one entry.
    again = client.post("/api/v1/media", json={"filename": "copy.png", "data_base64": base64.b64encode(PNG).decode()},
                        headers=h(csrf))
    assert again.get_json()["media"]["id"] == item["id"]
    # A missing file can't be used.
    bad = client.patch("/api/v1/site", json={"images": {"icon": "/media/nope.png"}}, headers=h(csrf))
    assert bad.status_code == 422
    client.patch("/api/v1/site", json={"images": {"icon": item["url"]}}, headers=h(csrf))
    assert client.delete(f"/api/v1/media/{item['id']}", headers=h(csrf)).status_code == 409
    client.patch("/api/v1/site", json={"images": {"icon": ""}}, headers=h(csrf))
    assert client.delete(f"/api/v1/media/{item['id']}", headers=h(csrf)).status_code == 200


def test_media_refuses_other_files_and_scripted_svg(client, csrf, admin):
    for data in (b"just text", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'):
        resp = client.post("/api/v1/media", json={"filename": "x", "data_base64": base64.b64encode(data).decode()},
                           headers=h(csrf))
        assert resp.status_code == 400


def test_a_media_body_over_the_limit_is_refused_unread(client, csrf, admin, monkeypatch):
    """The 413 comes from the declared length, before the body is decoded."""
    from flatout import media
    monkeypatch.setattr(media, "MAX_IMAGE", 1024)   # so the test body can stay small
    limit = 1024 * 4 // 3 + 64 * 1024
    big = base64.b64encode(b"x" * (limit + 1)).decode()
    resp = client.post("/api/v1/media", json={"filename": "big.png", "data_base64": big}, headers=h(csrf))
    assert resp.status_code == 413


def test_uploaded_svg_is_served_sandboxed(client, csrf, admin):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10"/></svg>'
    item = client.post("/api/v1/media", json={"filename": "a.svg", "data_base64": base64.b64encode(svg).decode()},
                       headers=h(csrf)).get_json()["media"]
    resp = client.get(item["url"])
    assert "sandbox" in resp.headers["Content-Security-Policy"]


def test_install_dialog_without_a_release(client, admin):
    assert b"has no release yet" in client.get("/").data


def test_schema_endpoint_describes_every_section_type(client, admin):
    schema = client.get("/api/v1/site/schema").get_json()
    assert set(schema["section_types"]) == set(site_schema.SECTION_TYPES)
    json.dumps(schema)


def test_the_hero_can_fade_into_the_page(client, csrf, admin):
    """Off by default, so a site keeps its straight edge until the owner
    turns the fade on; its length is the owner's too."""
    hero = site_schema.default_document()["sections"][0]
    assert hero["fade"] is False and hero["fade_length"] == 470
    assert "hero--fade" not in client.get("/admin/preview").data.decode()

    resp = client.patch("/api/v1/site/sections/top", json={"fade": True, "fade_length": 300}, headers=h(csrf))
    assert resp.status_code == 200, resp.get_json()
    page = client.get("/admin/preview").data.decode()
    assert 'class="hero hero--fade"' in page and 'style="--hero-fade-len: 300px"' in page

    too_long = client.patch("/api/v1/site/sections/top", json={"fade_length": 5000}, headers=h(csrf))
    assert too_long.status_code == 422
    assert too_long.get_json()["errors"][0]["path"].endswith("fade_length")
