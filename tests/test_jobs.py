"""The job thread and the web share one SQLite file.

In WAL mode a transaction that has only read can't start writing once another
connection has written since, and SQLite refuses at once. A job runs flatpak
for minutes between reading and writing, and requests write meanwhile; the
job thread must not keep its transaction open across that.
"""
import sys

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
