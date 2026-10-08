"""Transactions and sessions: a savepoint rolls back instead of committing,
and a new password ends the account's other sessions."""
from flatout.models import Setting, db

from .conftest import token_for


def test_a_savepoint_rolls_back_with_its_transaction(app):
    """The sqlite3 driver's own transaction handling commits a released
    savepoint; SQLAlchemy begins transactions itself so it doesn't."""
    with app.app_context():
        with db.session.begin_nested():
            db.session.add(Setting(key="kept-only-if-committed", value="1"))
        db.session.rollback()
        assert db.session.get(Setting, "kept-only-if-committed") is None
        try:
            with db.session.begin_nested():
                db.session.add(Setting(key="inner", value="1"))
                raise ValueError
        except ValueError:
            pass
        db.session.add(Setting(key="outer", value="1"))
        db.session.commit()
        assert db.session.get(Setting, "inner") is None and db.session.get(Setting, "outer").value == "1"


def test_a_new_password_ends_the_other_sessions(app, client, csrf, admin):
    other = app.test_client()
    tok = token_for(other)
    other.post("/login", data={"_csrf": tok, "username": "admin@example.com", "password": "password1",
                               "remember": "on"})
    json = {"Accept": "application/json"}
    assert other.get("/api/v1/site", headers=json).status_code == 200
    resp = client.post("/account/password", json={"current": "password1", "new": "password2"},
                       headers={"X-CSRF": csrf})
    assert resp.status_code == 200
    assert client.get("/api/v1/site", headers=json).status_code == 200      # this session stays
    assert other.get("/api/v1/site", headers=json).status_code == 401       # that one ends, cookie and all
    # CSRF survives the fresh sign-in.
    assert client.post("/account/password", json={"current": "password2", "new": "password3"},
                       headers={"X-CSRF": csrf}).status_code == 200


def test_wrong_current_passwords_hit_the_signin_throttle(client, csrf, admin):
    body = {"current": "nope-nope", "new": "newpassword1"}
    for _ in range(8):
        assert client.post("/account/password", json=body, headers={"X-CSRF": csrf}).status_code == 403
    assert client.post("/account/password", json=body, headers={"X-CSRF": csrf}).status_code == 429


def test_spraying_accounts_hits_the_per_address_limit(app, admin):
    """Eight tries per account, but past forty failures from one address an
    account gets only three; someone who hasn't failed yet still signs in,
    since behind an undeclared proxy everyone shares that address."""
    stranger = app.test_client()
    csrf = token_for(stranger)

    def attempt(username, password="wrong-password"):
        return stranger.post("/login", data={"_csrf": csrf, "username": username, "password": password})
    for i in range(40):
        assert attempt(f"nobody{i}@example.com").status_code == 401
    assert attempt("nobody0@example.com").status_code == 401        # its second failure
    assert attempt("nobody0@example.com").status_code == 401        # its third
    assert attempt("nobody0@example.com").status_code == 429
    assert attempt("someone-new@example.com").status_code == 401   # a first try still runs
    neighbor = app.test_client()                                    # same address, the real admin
    resp = neighbor.post("/login", data={"_csrf": token_for(neighbor), "username": "admin@example.com",
                                         "password": "password1"})
    assert resp.status_code == 302


def test_a_missing_account_costs_a_password_check(app, admin):
    """Same answer, same work: nothing to learn from how fast "no" arrives."""
    stranger = app.test_client()
    csrf = token_for(stranger)
    resp = stranger.post("/login", data={"_csrf": csrf, "username": "ghost@example.com",
                                         "password": "wrong-password"})
    assert resp.status_code == 401 and b"Wrong email or password." in resp.data
