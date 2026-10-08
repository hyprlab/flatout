"""The job thread and the web share one SQLite file.

In WAL mode a transaction that has only read can't start writing once another
connection has written since, and SQLite refuses at once. A job runs flatpak
for minutes between reading and writing, and requests write meanwhile; the
job thread must not keep its transaction open across that.
"""
import socket
import sys

import pytest

from flatout import repo
from flatout.models import db, get_setting, set_setting, worker

# Another connection writing while the command runs, as a request would.
OTHER_WRITER = ("import sqlite3, sys; c = sqlite3.connect(sys.argv[1], timeout=5); "
                "c.execute(\"INSERT OR REPLACE INTO settings (key, value) VALUES ('other', 'wrote')\"); c.commit()")


def test_a_job_writes_after_others_wrote_during_its_command(app, tmp_path):
    with app.app_context():
        set_setting("job", "before")
        worker.active = True
        try:
            assert get_setting("job") == "before"     # the job reads...
            repo.run([sys.executable, "-c", OTHER_WRITER, str(tmp_path / "flatout.db")])
            set_setting("job", "after")               # ...and writes once flatpak is done
        finally:
            worker.active = False
            db.session.remove()
        assert get_setting("other") == "wrote" and get_setting("job") == "after"


def test_only_public_addresses_are_fetched(app):
    """A URL import must not reach the internal network through Flatout."""
    from flatout import jobs
    private = [
        "http://127.0.0.1/x.flatpak", "http://localhost/x.flatpak",
        "http://169.254.169.254/latest/meta-data/", "http://10.1.2.3/x",
        "http://192.168.1.4/x", "https://172.16.0.9/x",
        "http://[::1]/x", "http://[fe80::1]/x", "http://[fc00::1]/x",
        "ftp://example.org/x",
    ]
    for url in private:
        with pytest.raises(repo.RepoError):
            jobs._assert_public(url)
    jobs._assert_public("https://1.1.1.1/x.flatpak")   # an IP literal, no DNS needed


def _answers(*ips):
    """getaddrinfo results for the given addresses, port filled in."""
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in ips]


def test_the_fetched_address_is_the_checked_one(app, monkeypatch, tmp_path):
    """The download dials the address the check approved: it never resolves
    the host name a second time, so DNS rebinding can't move the target."""
    from flatout import jobs
    dialed = []
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda host, port, **kw: _answers("93.184.216.34"))
    def fake_connect(address, timeout=None, source_address=None):
        dialed.append(address)
        raise OSError("stop here")
    monkeypatch.setattr(socket, "create_connection", fake_connect)
    with app.app_context(), pytest.raises(Exception, match="stop here"):
        jobs._fetch("http://packages.example/bundle.flatpak", tmp_path / "b", [])
    assert dialed == [("93.184.216.34", 80)]


def test_a_rebound_lookup_is_refused(app, monkeypatch, tmp_path):
    """If the answer at connect time differs from the checked one, the
    private address is refused again."""
    from flatout import jobs
    lookups = iter([_answers("93.184.216.34"), _answers("10.0.0.8")])
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, **kw: next(lookups))
    with app.app_context(), pytest.raises(repo.RepoError, match="not a public address"):
        jobs._fetch("http://packages.example/bundle.flatpak", tmp_path / "b", [])
