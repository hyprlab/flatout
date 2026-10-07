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
    assert other.get("/search?q=ab", headers=json).status_code == 200
    resp = client.post("/account/password", json={"current": "password1", "new": "password2"},
                       headers={"X-CSRF": csrf})
    assert resp.status_code == 200
    assert client.get("/search?q=ab", headers=json).status_code == 200      # this session stays
    assert other.get("/search?q=ab", headers=json).status_code == 401       # that one ends, cookie and all
    # CSRF survives the fresh sign-in.
    assert client.post("/account/password", json={"current": "password2", "new": "password3"},
                       headers={"X-CSRF": csrf}).status_code == 200
