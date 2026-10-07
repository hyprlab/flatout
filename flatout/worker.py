"""Periodic housekeeping.

``run_once`` is called by the thread that ``__init__._start_worker`` starts,
every ``worker_minutes`` (an admin setting whose default is the WORKER_MINUTES
environment variable). It runs in the web process, so keep it short and let it
fail loudly: the caller logs the traceback and tries again next cycle.

Repository work doesn't run here: it is queued as jobs and run by its own
thread (jobs.py), as soon as it is asked for.
"""
import logging
import time

from flask import Flask

from .models import db

log = logging.getLogger(__name__)


def run_once(app: Flask) -> None:
    with app.app_context():
        started = time.monotonic()
        try:
            touched = _work()
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        finally:
            db.session.remove()
        log.info("background pass: %d rows in %.2fs", touched, time.monotonic() - started)


def _work() -> int:
    """Forget the daily visitor hashes once they are old enough not to
    matter for any count. Returns the number of rows removed, for the log."""
    from . import stats
    return stats.prune()
