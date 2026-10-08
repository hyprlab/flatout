"""What the shell guarantees.

These cover what is easy to break while reshaping the app and expensive to
notice later: the setup gate, CSRF, the admin guard, sign-in, and the error
shapes the client depends on. Add to them rather than replacing them.
"""


def test_fresh_install_steers_to_setup(client):
    resp = client.get("/")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/setup")


def test_api_before_setup_answers_json(client):
    resp = client.get("/api/v1/site")
    assert resp.status_code == 503 and "set up" in resp.get_json()["error"]


def test_health_answers_before_setup(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_setup_runs_once_and_names_the_app(client, csrf, admin):
    home = client.get("/")
    assert home.status_code == 200 and b"Gnomish" in home.data
    assert client.get("/admin").status_code == 200
    again = client.post("/setup", json={"username": "x@example.com", "password": "password1"},
                        headers={"X-CSRF": csrf})
    assert again.status_code == 409


def test_setup_closes_sign_up_by_default(app, client, csrf):
    client.post("/setup", json={"username": "a@example.com", "password": "password1"}, headers={"X-CSRF": csrf})
    resp = app.test_client().get("/register")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/login")


def test_post_without_csrf_is_refused(client, csrf, admin):
    resp = client.post("/api/v1/site/publish", json={})
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_admin_pages_need_an_account(app, admin):
    stranger = app.test_client()
    for path in ("/admin", "/admin/site", "/admin/preview", "/admin/releases"):
        resp = stranger.get(path)
        assert resp.status_code == 302 and "/login" in resp.headers["Location"], path


def test_every_admin_page_renders(client, admin):
    for path in ("/admin", "/admin/site", "/admin/design", "/admin/pages", "/admin/media", "/admin/preview",
                 "/admin/app", "/admin/releases", "/admin/repository", "/admin/stats", "/admin/api", "/admin/api/reference"):
        assert client.get(path).status_code == 200, path
    # Design was called Theme; the old address still leads there.
    moved = client.get("/admin/theme")
    assert moved.status_code == 301 and moved.headers["Location"].endswith("/admin/design")
    assert ">Design</span>" in client.get("/admin").data.decode()


def test_admin_routes_refuse_a_plain_account(second_user):
    other, token = second_user
    assert other.post("/settings/registration", json={"open": False},
                      headers={"X-CSRF": token}).status_code == 403


def test_admin_cannot_delete_or_demote_itself(client, csrf, admin):
    h = {"X-CSRF": csrf}
    assert client.post("/settings/users", json={"username": "b@example.com", "password": "password1"},
                       headers=h).status_code == 200
    assert client.post("/settings/users/1/delete", headers=h).status_code == 400
    assert client.post("/settings/users/1/toggle-admin", headers=h).status_code == 400


def test_deleting_a_user_deletes_their_tokens(client, csrf, admin, second_user):
    other, token = second_user
    assert other.post("/api/v1/tokens", json={"name": "theirs"}, headers={"X-CSRF": token}).status_code == 201
    assert client.post("/settings/users/2/delete", headers={"X-CSRF": csrf}).status_code == 200
    from flatout.models import ApiToken
    with client.application.app_context():
        assert ApiToken.query.count() == 0


def test_registration_can_be_closed(client, csrf, admin, app):
    client.post("/settings/registration", json={"open": False}, headers={"X-CSRF": csrf})
    stranger = app.test_client()
    resp = stranger.get("/register")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/login")


def test_login_next_cannot_leave_the_site(client, csrf, admin):
    client.post("/logout", headers={"X-CSRF": csrf})
    for bad in ("https://example.net/", "//example.net/", "/\\example.net"):
        resp = client.post(f"/login?next={bad}", data={
            "_csrf": csrf, "username": admin["username"], "password": admin["password"],
        })
        assert resp.headers["Location"] in ("/admin", "http://localhost/admin"), bad
        client.post("/logout", headers={"X-CSRF": csrf})


def test_failed_sign_ins_are_throttled(client, csrf, admin):
    client.post("/logout", headers={"X-CSRF": csrf})
    data = {"_csrf": csrf, "username": admin["username"], "password": "wrong-password"}
    codes = [client.post("/login", data=data).status_code for _ in range(9)]
    assert codes[:8] == [401] * 8 and codes[8] == 429


def test_errors_are_json_for_the_api_and_the_site_404_for_people(client, csrf, admin):
    api = client.get("/api/v1/releases/999", headers={"X-CSRF": csrf})
    assert api.status_code == 404 and "error" in api.get_json()
    page = client.get("/no-such-page")
    assert page.status_code == 404 and b"There is nothing at this address" in page.data
    admin_page = client.get("/admin/no-such-page")
    assert admin_page.status_code == 404 and b"error-code" in admin_page.data


def test_signed_out_api_calls_get_json_401(app, admin):
    stranger = app.test_client()
    resp = stranger.get("/api/v1/site")
    assert resp.status_code == 401 and "error" in resp.get_json()


def test_security_headers(client, admin):
    resp = client.get("/login", follow_redirects=True)
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["Cache-Control"] == "no-store"
    # The editor frames its own preview.
    assert client.get("/admin/preview").headers["X-Frame-Options"] == "SAMEORIGIN"


def test_html_carries_a_csp_and_the_page_own_scripts_its_nonce(client, admin):
    import re
    resp = client.get("/admin")
    policy = resp.headers["Content-Security-Policy"]
    nonce = re.search(r"script-src 'nonce-([^']+)'", policy).group(1)
    assert f'nonce="{nonce}"' in resp.get_data(as_text=True)
    assert "object-src 'none'" in policy and "frame-ancestors 'self'" in policy
    # Turnstile's origin only joins the policy while the challenge is on.
    assert "challenges.cloudflare.com" not in policy


def test_hsts_only_when_cookies_are_secure(app, admin):
    client = app.test_client()
    assert "Strict-Transport-Security" not in client.get("/login").headers
    app.config["SESSION_COOKIE_SECURE"] = True
    assert "max-age" in client.get("/login").headers["Strict-Transport-Security"]


def test_cf_connecting_ip_counts_only_behind_a_configured_proxy(app):
    from flatout import serve
    with app.test_request_context("/repo/summary", headers={"CF-Connecting-IP": "203.0.113.9"}):
        assert serve.client_address() != "203.0.113.9"
    app.config["TRUST_PROXY"] = 2
    with app.test_request_context("/repo/summary", headers={"CF-Connecting-IP": "203.0.113.9"}):
        assert serve.client_address() == "203.0.113.9"


def test_changelog_renders_in_the_about_tab(client, csrf, admin):
    from flatout import __version__
    body = client.get("/admin").data.decode()
    assert "Changelog" in body and __version__ in body


def test_a_client_that_accepts_anything_is_sent_to_sign_in(app, admin):
    resp = app.test_client().get("/admin", headers={"Accept": "*/*"})
    assert resp.status_code == 302 and "/login" in resp.headers["Location"]
