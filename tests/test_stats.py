"""Install numbers: what is counted, and every figure the Installs page shows."""
from datetime import date, datetime, timedelta, timezone

import pytest

from flatout import stats
from flatout.models import Release, StatDay, db


def day(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


def when(n: int) -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=n)


def test_only_update_checks_and_commit_pulls_count(app):
    with app.app_context():
        for _ in range(2):
            stats.record("summary", "203.0.113.5")
        stats.record("summary.idx", "203.0.113.5")       # the same install, the same day
        stats.record("summary", "203.0.113.6")
        stats.record("objects/ab/cdef.commitmeta", "203.0.113.5")
        for ignored in ("objects/ab/cdef.filez", "summary.sig", "config", "deltas/x/y"):
            stats.record(ignored, "203.0.113.5")
        check = StatDay.query.filter_by(metric="check").one()
        assert (check.uniques, check.hits) == (2, 4)
        pull = StatDay.query.filter_by(metric="pull").one()
        assert (pull.target, pull.uniques) == ("abcdef", 1)
        assert StatDay.query.count() == 2


@pytest.fixture()
def history(app, admin):
    """Releases and counts over the last weeks, as if collected."""
    with app.app_context():
        def rel(commit, channel, version, arch, published, status):
            db.session.add(Release(app_id="org.example.Hello", arch=arch, channel=channel, version=version,
                                   status=status, commit=commit * 64, published_at=when(published)))
        rel("a", "stable", "1.0", "x86_64", 40, "superseded")
        rel("b", "stable", "1.1", "x86_64", 2, "live")
        rel("c", "stable", "1.1", "aarch64", 2, "live")
        rel("d", "beta", "1.2", "x86_64", 0, "live")

        def count(metric, target, ago, uniques, hits=None):
            db.session.add(StatDay(day=day(ago), metric=metric, target=target, uniques=uniques,
                                   hits=hits if hits is not None else uniques))
        count("check", "summary", 0, 10, 25)
        count("check", "summary", 3, 12, 30)
        count("check", "summary", 20, 50, 90)
        count("pull", "a" * 64, 40, 8)
        count("pull", "b" * 64, 1, 5, 7)
        count("pull", "c" * 64, 0, 2)
        count("pull", "d" * 64, 0, 1)
        count("pull", "e" * 64, 0, 3)                     # a commit no release knows
        db.session.commit()


def test_every_figure_of_the_installs_page(client, history):
    s = client.get("/api/v1/stats?days=30").get_json()
    # Installs in use: the busiest of the last seven days.
    assert (s["install_base"], s["install_base_basis"]) == (12, "checks")
    assert s["today"] == 10 and s["average_7_days"] == round(22 / 7, 1)
    assert (s["peak"], s["peak_day"]) == (50, day(20)) and s["counting_since"] == day(20)
    assert s["generated"].endswith("Z")

    # The current builds, every architecture together, and who is behind.
    stable = s["latest_stable"]
    assert (stable["version"], stable["installs"], stable["downloads"]) == ("1.1", 7, 9)
    assert stable["arches"] == {"x86_64": 5, "aarch64": 2}
    assert (s["latest_beta"]["version"], s["latest_beta"]["installs"]) == ("1.2", 1)
    assert s["older_installs"] == 12 - 8
    assert s["releases_30_days"] == {"total": 2, "stable": 1, "beta": 1}

    # Machines by architecture: all time, and the last 30 days.
    arches = {a["arch"]: a for a in s["arches"]}
    assert (arches["x86_64"]["installs"], arches["x86_64"]["installs_30_days"]) == (14, 6)
    assert (arches["aarch64"]["installs"], arches["aarch64"]["downloads"]) == (2, 2)

    # Every build, newest first, the unlabelled commit last.
    assert [b["version"] for b in s["builds"]] == ["1.2", "1.1", "1.1", "1.0", None]
    assert s["builds"][-1]["commit"] == "e" * 64 and s["pulls_without_release"] == 3
    assert [r["version"] for r in s["releases"]] == ["1.2", "1.1", "1.0"]


def test_before_checks_build_up_the_newest_pulls_estimate_the_base(app, admin, client):
    with app.app_context():
        db.session.add(Release(app_id="org.example.Hello", arch="x86_64", channel="stable", version="1.0",
                               status="live", commit="f" * 64, published_at=when(1)))
        db.session.add(StatDay(day=day(0), metric="pull", target="f" * 64, uniques=4, hits=4))
        db.session.commit()
    s = client.get("/api/v1/stats").get_json()
    assert (s["install_base"], s["install_base_basis"]) == (4, "release")
    assert s["older_installs"] == 0 and s["latest_beta"] is None


def test_nothing_yet(client, admin):
    s = client.get("/api/v1/stats").get_json()
    assert (s["install_base"], s["install_base_basis"], s["latest_stable"]) == (0, "none", None)
    assert s["builds"] == [] and s["counting_since"] is None and s["peak_day"] is None
    assert client.get("/admin/stats").status_code == 200
