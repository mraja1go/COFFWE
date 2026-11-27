"""Phase 3 M4: the estate clock in Postgres, as-of views, and end-to-end validation."""

import time
from datetime import UTC, date, datetime, timedelta

import pandas as pd
import psycopg
import pytest

from coffwe.feed import broker, validate
from coffwe.feed.ingest import UPSERT_CLOCK, psycopg_url
from coffwe.feed.observed import generate
from coffwe.sim import engine

TZ = "Asia/Kolkata"


@pytest.fixture(scope="module")
def url(db_run):
    """coffwe_test after the shared full-season run (hives, colonies, harvests, ...)."""
    from tests.conftest import _test_db_url

    return psycopg_url(_test_db_url())


def set_clock(url, sim_time, running=False, speed=1.0, wall_at=None):
    with psycopg.connect(url) as c:
        c.execute(
            UPSERT_CLOCK, (sim_time, (wall_at or datetime.now(UTC)).timestamp(), speed, running)
        )


def one(url, sql, *args):
    with psycopg.connect(url) as c:
        return c.execute(sql, args).fetchone()


# --- estate_now() ------------------------------------------------------------------


def test_paused_clock_holds_its_time(url):
    t = datetime(2026, 12, 15, 12, 0, tzinfo=UTC)
    set_clock(url, t, running=False)
    now, today = one(url, "SELECT estate_now(), estate_today()")
    assert now == t and today == date(2026, 12, 15)


def test_running_clock_advances_at_its_speed(url):
    t = datetime(2026, 12, 15, 0, 0, tzinfo=UTC)
    anchor = datetime.now(UTC) - timedelta(seconds=10)  # anchored 10 real seconds ago
    set_clock(url, t, running=True, speed=3600, wall_at=anchor)
    now = one(url, "SELECT estate_now()")[0]
    assert t + timedelta(hours=9.9) < now < t + timedelta(hours=10.5)


def test_estate_today_is_the_ist_calendar_date(url):
    set_clock(url, datetime(2026, 12, 15, 20, 0, tzinfo=UTC))  # 01:30 on the 16th in IST
    assert one(url, "SELECT estate_today()")[0] == date(2026, 12, 16)


def test_without_a_clock_row_it_falls_back_to_wall_time(url):
    with psycopg.connect(url) as c:
        saved = c.execute("SELECT sim_time, wall_at, speed, running FROM estate_clock").fetchone()
        c.execute("DELETE FROM estate_clock")
        now = c.execute("SELECT estate_now(), now()").fetchone()
        assert now[0] == now[1]
        c.rollback()  # put it back
    assert saved is not None


# --- as-of views -------------------------------------------------------------------


@pytest.mark.parametrize("day", [date(2026, 11, 25), date(2027, 1, 15), date(2027, 4, 1)])
def test_hive_overview_is_as_of_the_estate_day(url, day):
    set_clock(url, datetime(day.year, day.month, day.day, 6, 0, tzinfo=UTC))
    with psycopg.connect(url) as c:
        rows = c.execute("""SELECT hive_code, hive_age_days, colony_occupancy_days,
                                   queen_age_days, days_since_last_harvest, last_harvested_on,
                                   as_of FROM hive_overview""").fetchall()
        future = c.execute(
            "SELECT count(*) FROM honey_harvests WHERE harvested_on > %s", (day,)
        ).fetchone()[0]
    assert rows and all(r[6] == day for r in rows)
    for code, age, occ, queen, since, last, _ in rows:
        for v in (age, occ, queen, since):
            assert v is None or v >= 0, (code, day)
        assert last is None or last <= day
    if day < date(2027, 4, 1):
        assert future > 0  # the database really does hold "future" harvests to ignore


def test_a_hive_shows_the_colony_living_in_it_on_that_day(url):
    with psycopg.connect(url) as c:
        ended = c.execute("""SELECT h.code, c.established_on, c.ended_on FROM bee_colonies c
                             JOIN hives h ON h.id = c.hive_id WHERE c.ended_on IS NOT NULL
                             ORDER BY c.ended_on DESC LIMIT 1""").fetchone()
    code, established, ended_on = ended
    set_clock(url, datetime.combine(ended_on - timedelta(days=1), datetime.min.time(), UTC))
    before = one(url, "SELECT colony_established_on FROM hive_overview WHERE hive_code = %s", code)
    set_clock(url, datetime.combine(ended_on, datetime.min.time(), UTC))
    after = one(url, "SELECT colony_established_on FROM hive_overview WHERE hive_code = %s", code)
    assert before[0] == established
    assert after[0] != established  # gone (empty box) or replaced by a new colony


def test_set_estate_clock_pauses_the_estate_at_a_moment(test_db, url):
    """What `seed` (25 Nov) and `sim run --db` (the run's end) use to set "now"."""
    from coffwe.seed import set_estate_clock

    moment = datetime(2027, 5, 1, 0, 0, tzinfo=UTC)
    with test_db() as session:
        set_estate_clock(session, moment)
        session.flush()
        from sqlalchemy import text

        now, running = session.execute(text("SELECT estate_now(), running FROM estate_clock")).one()
        session.rollback()  # leave the shared test database as it was
    assert now == moment and running is False


# --- end to end --------------------------------------------------------------------

try:
    broker.mosquitto_bin()
    HAVE_MOSQUITTO = True
except FileNotFoundError:
    HAVE_MOSQUITTO = False


@pytest.mark.skipif(not HAVE_MOSQUITTO, reason="mosquitto not installed")
def test_validation_replay_loses_nothing_and_mirrors_the_clock(url, tmp_path):
    store = engine.run(seed=31, start=date(2026, 11, 22), days=8, root=tmp_path)
    generate(store.run_dir)
    validate._prepare_db(url)
    start = pd.Timestamp("2026-11-25", tz=TZ)
    until = start + pd.Timedelta(hours=36)
    rep = validate.replay(store.run_dir, start, until, 20000, url)
    m = validate.measure(store.run_dir, start, until, url)
    j = m["labels"]
    on_time = j["delivered"] & (j["delivered_at"] < until)
    assert (j.loc[on_time, "stored"]).all()  # nothing lost between radio and database
    assert rep["broker_drops"] == 0
    assert m["clock"][1] in (date(2026, 11, 26), date(2026, 11, 27))  # clock followed the feed
    # Write to a temp file: the real docs/phase3-report.md is the full-season run.
    report = validate.write_report(
        store.run_dir, start, until, rep, m, 20000, path=tmp_path / "report.md"
    )
    text = report.read_text()
    assert "lost nothing" in text and "Accuracy" in text
    time.sleep(0.1)


@pytest.mark.skipif(not HAVE_MOSQUITTO, reason="mosquitto not installed")
def test_estate_clock_follows_the_feed_live(url, tmp_path):
    """While a replay runs, estate_now() in SQL tracks the MQTT clock."""
    store = engine.run(seed=32, start=date(2026, 11, 23), days=5, root=tmp_path)
    generate(store.run_dir)
    validate._prepare_db(url)
    start = pd.Timestamp("2026-11-25", tz=TZ)
    validate.replay(store.run_dir, start, start + pd.Timedelta(hours=12), 7200, url)
    now = one(url, "SELECT estate_now()")[0]
    assert (
        start.to_pydatetime() + timedelta(hours=11)
        <= now
        <= start.to_pydatetime() + timedelta(hours=13)
    )
