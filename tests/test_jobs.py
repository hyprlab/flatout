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


def _serve(monkeypatch, body: bytes, declared: int | None = None, cut: int | None = None, tls=None):
    """A one-shot local HTTP server the fetch reaches by a monkeypatched
    dialer: getaddrinfo answers a public address, the socket dials localhost.
    ``declared`` overrides the Content-Length header; ``cut`` closes the
    response after that many bytes despite a longer declared length; ``tls``
    is a server SSLContext, for HTTPS."""
    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(declared if declared is not None else len(body)))
            self.end_headers()
            self.wfile.write(body[:cut] if cut else body)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    if tls:
        server.socket = tls.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.handle_request, daemon=True).start()
    port = server.server_address[1]
    real_getaddrinfo, real_connect = socket.getaddrinfo, socket.create_connection

    def getaddrinfo(host, port, *a, **kw):
        if host in ("127.0.0.1", "localhost"):   # the dialer's own resolution
            return real_getaddrinfo(host, port, *a, **kw)
        return _answers("93.184.216.34")
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket, "create_connection",
                        lambda address, timeout=None, source_address=None:
                        real_connect(("127.0.0.1", port), timeout, source_address))
    return server


def test_a_declared_size_over_the_limit_never_writes_a_byte(app, monkeypatch, tmp_path):
    from flatout import jobs
    body = b"x" * 500
    _serve(monkeypatch, body, declared=app.config["MAX_CONTENT_LENGTH"] + 1)
    dest = tmp_path / "b.flatpak"
    with app.app_context(), pytest.raises(repo.RepoError, match="limit"):
        jobs._fetch("http://packages.example/b.flatpak", dest, [])
    assert not dest.exists()


def test_a_stalled_download_leaves_no_partial_file(app, monkeypatch, tmp_path):
    from flatout import jobs
    body = b"x" * 20_000
    _serve(monkeypatch, body, cut=1000)   # says 20000, sends 1000, hangs up
    dest = tmp_path / "b.flatpak"
    with app.app_context(), pytest.raises(Exception):
        jobs._fetch("http://packages.example/b.flatpak", dest, [])
    assert not dest.exists()


def test_a_full_download_is_kept(app, monkeypatch, tmp_path):
    from flatout import jobs
    import hashlib
    body = b"y" * 1500
    _serve(monkeypatch, body)
    dest = tmp_path / "b.flatpak"
    with app.app_context():
        size, sha = jobs._fetch("http://packages.example/b.flatpak", dest, [])
    assert size == 1500 and sha == hashlib.sha256(body).hexdigest() and dest.read_bytes() == body


def _run_job(app, kind, handler, monkeypatch):
    """Run a queued job of ``kind`` with ``handler`` standing in for the real one."""
    from flatout import jobs
    from flatout.models import Job
    monkeypatch.setitem(jobs.HANDLERS, kind, handler)
    with app.app_context():
        job = Job(kind=kind, payload="{}", created_by="test")
        db.session.add(job)
        db.session.commit()
        jobs.run_job(job)
        return db.session.get(Job, job.id)


def test_a_jobs_log_shows_the_failing_line_not_the_traceback(app, monkeypatch):
    """The traceback goes to the server's log; a job's log is visible to
    scoped tokens and stays free of server paths and payloads."""
    def boom(job, payload, lines):
        lines.append("Working on it.")
        raise ValueError("odd value in /data/bundles/incoming-1.flatpak")
    job = _run_job(app, "import", boom, monkeypatch)
    assert job.status == "failed"
    assert "FAILED: ValueError: odd value" in job.log and "Traceback" not in job.log


def test_a_chatty_jobs_log_is_trimmed(app, monkeypatch):
    from flatout import jobs
    monkeypatch.setattr(jobs, "LOG_LINES_KEPT", 10)

    def chatty(job, payload, lines):
        lines.extend(f"line {i}" for i in range(50))
    job = _run_job(app, "summary", chatty, monkeypatch)
    assert job.status == "done"
    assert "earlier lines left out" in job.log and "line 49" in job.log and "line 30" not in job.log


@pytest.fixture()
def certificate(tmp_path):
    """A self-signed certificate for one host name, from openssl."""
    import shutil
    import subprocess
    if not shutil.which("openssl"):
        pytest.skip("openssl is needed")

    def make(name):
        cert, key = tmp_path / f"{name}.crt", tmp_path / f"{name}.key"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                        "-keyout", str(key), "-out", str(cert), "-subj", f"/CN={name}",
                        "-addext", f"subjectAltName=DNS:{name}"], check=True, capture_output=True)
        return cert, key
    return make


def _trust(monkeypatch, cert):
    """Make the fetch's default HTTPS context trust ``cert`` and only it."""
    import ssl
    monkeypatch.setattr(ssl, "_create_default_https_context",
                        lambda *a, **kw: ssl.create_default_context(cafile=str(cert)))


def _tls_server(cert, key):
    import ssl
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    return context


def test_an_https_download_is_kept(app, monkeypatch, tmp_path, certificate):
    """HTTPS goes through the pinned dialer too, with the certificate
    checked against the URL's host name."""
    from flatout import jobs
    import hashlib
    cert, key = certificate("packages.example")
    _trust(monkeypatch, cert)
    body = b"z" * 2500
    _serve(monkeypatch, body, tls=_tls_server(cert, key))
    dest = tmp_path / "b.flatpak"
    with app.app_context():
        size, sha = jobs._fetch("https://packages.example/b.flatpak", dest, [])
    assert size == 2500 and sha == hashlib.sha256(body).hexdigest() and dest.read_bytes() == body


def test_an_https_certificate_for_another_name_is_refused(app, monkeypatch, tmp_path, certificate):
    """Dialing the checked address must not weaken the name check: the
    certificate still has to be for the host in the URL."""
    from flatout import jobs
    cert, key = certificate("elsewhere.example")
    _trust(monkeypatch, cert)
    _serve(monkeypatch, b"z" * 10, tls=_tls_server(cert, key))
    dest = tmp_path / "b.flatpak"
    import ssl
    with app.app_context(), pytest.raises(Exception) as caught:
        jobs._fetch("https://packages.example/b.flatpak", dest, [])
    assert isinstance(getattr(caught.value, "reason", caught.value), ssl.SSLCertVerificationError)
    assert not dest.exists()
