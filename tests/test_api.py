"""API tokens, their scopes, the OpenAPI description, and the MCP server."""
import pytest


@pytest.fixture()
def make_token(client, csrf, admin):
    def make(*scopes, **extra):
        resp = client.post("/api/v1/tokens", json={"name": "test", "scopes": list(scopes), **extra},
                           headers={"X-CSRF": csrf})
        assert resp.status_code == 201
        return resp.get_json()["token"]
    return make


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_a_token_needs_no_csrf_and_no_session(app, make_token):
    token = make_token("site")
    robot = app.test_client()
    resp = robot.patch("/api/v1/site", json={"app": {"name": "Botted"}}, headers=bearer(token))
    assert resp.status_code == 200
    assert resp.get_json()["document"]["app"]["name"] == "Botted"
    assert "token test" in robot.get("/api/v1", headers=bearer(token)).get_json()["you"]


def test_scopes_hold(app, make_token):
    reader = make_token()
    robot = app.test_client()
    assert robot.get("/api/v1/site", headers=bearer(reader)).status_code == 200
    resp = robot.post("/api/v1/site/publish", json={}, headers=bearer(reader))
    assert resp.status_code == 403 and '"site" scope' in resp.get_json()["error"]
    assert robot.post("/api/v1/releases/promote", json={}, headers=bearer(reader)).status_code == 403


def test_a_bad_token_is_refused_even_with_a_session(client, csrf, admin):
    resp = client.get("/api/v1/site", headers={"Authorization": "Bearer fo_nope"})
    assert resp.status_code == 401


def test_tokens_cant_manage_tokens_or_take_the_key(app, make_token):
    token = make_token("site", "releases")
    robot = app.test_client()
    assert robot.post("/api/v1/tokens", json={"name": "x"}, headers=bearer(token)).status_code == 403
    assert robot.get("/api/v1/repo/key/secret", headers=bearer(token)).status_code == 403


def test_only_an_admin_can_take_or_replace_the_signing_key(app, client, csrf, second_user):
    other, other_csrf = second_user
    assert other.get("/api/v1/repo/key/secret").status_code == 403
    assert other.post("/api/v1/repo/key", json={"action": "generate"},
                      headers={"X-CSRF": other_csrf}).status_code == 403
    # The admin passes the gate; there is no key yet, so the answer is 404.
    assert client.get("/api/v1/repo/key/secret").status_code == 404


def test_revoked_and_expired_tokens_stop_working(app, client, csrf, make_token):
    token = make_token()
    robot = app.test_client()
    listed = client.get("/api/v1/tokens").get_json()["tokens"]
    assert listed[0]["prefix"] == token[:10] and "token" not in listed[0]
    client.delete(f"/api/v1/tokens/{listed[0]['id']}", headers={"X-CSRF": csrf})
    assert robot.get("/api/v1/site", headers=bearer(token)).status_code == 401

    from datetime import timedelta
    from flatout.models import ApiToken, db, utcnow
    expiring = make_token(expires_in_days=1)
    with app.app_context():
        row = ApiToken.query.order_by(ApiToken.id.desc()).first()
        row.expires_at = utcnow() - timedelta(minutes=1)
        db.session.commit()
    assert robot.get("/api/v1/site", headers=bearer(expiring)).status_code == 401


def test_openapi_describes_the_api(app, admin):
    spec = app.test_client().get("/api/v1/openapi.json").get_json()
    assert spec["openapi"].startswith("3.1")
    assert "/site/sections/{section_id}" in spec["paths"]
    assert spec["paths"]["/releases"]["post"]["x-scope"] == "releases"
    hero = next(v for v in spec["components"]["schemas"]["SiteDocument"]["properties"]["sections"]["items"]["oneOf"]
                if v["properties"]["type"]["const"] == "hero")
    assert hero["properties"]["title"]["maxLength"] == 160


def rpc(client, token, method, params=None, msg_id=1):
    body = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body, headers=bearer(token))


def test_mcp_handshake_and_tools(app, make_token):
    token = make_token("site")
    robot = app.test_client()
    init = rpc(robot, token, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                            "clientInfo": {"name": "test", "version": "1"}}).get_json()
    assert init["result"]["protocolVersion"] == "2025-06-18"
    assert "publish_site" in init["result"]["instructions"]
    assert robot.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                      headers=bearer(token)).status_code == 202
    names = {t["name"] for t in rpc(robot, token, "tools/list").get_json()["result"]["tools"]}
    assert {"update_section", "publish_site", "upload_release_from_url", "get_install_stats"} <= names


def test_mcp_tools_go_through_the_api(app, make_token):
    token = make_token("site")
    robot = app.test_client()
    call = rpc(robot, token, "tools/call", {"name": "update_section",
                                            "arguments": {"id": "top", "values": {"title": "Agent was here"}}})
    assert call.get_json()["result"]["isError"] is False
    preview = rpc(robot, token, "tools/call", {"name": "preview_site", "arguments": {}}).get_json()
    assert "Agent was here" in preview["result"]["content"][0]["text"]
    # Scopes still hold, and errors come back as tool errors the agent can read.
    denied = rpc(robot, token, "tools/call", {"name": "promote_release", "arguments": {}}).get_json()
    assert denied["result"]["isError"] is True and "releases" in denied["result"]["content"][0]["text"]
    invalid = rpc(robot, token, "tools/call", {"name": "update_site",
                                               "arguments": {"patch": {"theme": {"radius": 500}}}}).get_json()
    assert invalid["result"]["isError"] is True and "$.theme.radius" in invalid["result"]["content"][0]["text"]


def test_mcp_needs_a_token_and_checks_origin(app, make_token):
    robot = app.test_client()
    resp = robot.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert resp.status_code == 401 and "Bearer" in resp.headers["WWW-Authenticate"]
    token = make_token()
    resp = robot.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                      headers={**bearer(token), "Origin": "https://evil.example"})
    assert resp.status_code == 403
    assert rpc(robot, token, "ping").get_json()["result"] == {}
    assert rpc(robot, token, "nope").get_json()["error"]["code"] == -32601
    assert robot.get("/mcp").status_code == 405
