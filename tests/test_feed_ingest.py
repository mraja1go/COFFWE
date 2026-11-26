"""Phase 3 M3: ingestion into TimescaleDB -- validation, flags, idempotence, rollups,
persistent sessions. Uses a throwaway broker and the coffwe_test database."""

import asyncio
import threading
import time
from datetime import UTC, date, datetime, timedelta

import pandas as pd
import psycopg
import pytest

from coffwe.feed import broker
from coffwe.feed.ingest import LATE_AFTER, Catalog, Ingestor, SensorInfo, classify, psycopg_url
from coffwe.feed.observed import generate, load_messages
from coffwe.feed.publisher import connect, new_client
from coffwe.feed.runner import run_feed
from coffwe.feed.schema import Reading
from coffwe.sim import engine
from coffwe.sim.truth import TruthStore

PASSWORDS = {"devices": "dev-pw", "ingest": "ing-pw", "admin": "adm-pw"}
TZ = "Asia/Kolkata"


# --- pure logic --------------------------------------------------------------------


def reading(value=0.3, minutes_late=0, sensor="FS-B1-soil_moisture", seq=5):
    t = datetime(2026, 11, 25, 6, 0, tzinfo=UTC)
    return Reading(sensor=sensor, measured_at=t, value=value, unit="m3/m3", seq=seq), t + timedelta(
        minutes=minutes_late
    )


def test_classify_flags_range_and_lateness():
    info = SensorInfo(1, 1, 0.0, 0.6)
    r, recv = reading(0.3)
    assert classify(r, info, recv) == ("ok", False)
    r, recv = reading(0.9, minutes_late=31)
    assert classify(r, info, recv) == ("out_of_range", True)
    r, recv = reading(-0.1, minutes_late=int(LATE_AFTER.total_seconds() // 60))
    assert classify(r, info, recv) == ("out_of_range", False)


def offline_ingestor() -> Ingestor:
    ing = Ingestor("postgresql://unused", "127.0.0.1", 1, "u", "p")
    ing.catalog = Catalog(
        sensors={"FS-B1-soil_moisture": SensorInfo(7, 3, 0.0, 0.6)},
        stations={"FS-B1": 3},
        station_status={3: "online"},
    )
    return ing


def test_bad_messages_go_to_the_dead_letter_queue_not_the_floor():
    ing = offline_ingestor()
    ing.handle("coffwe/v1/B1/FS-B1/soil_moisture", b"not json")
    ing.handle(
        "coffwe/v1/B1/FS-B1/soil_moisture",
        b'{"sensor":"FS-B1-soil_moisture","measured_at":"2026-11-25T06:00:00",'
        b'"value":0.3,"unit":"m3/m3","seq":1}',
    )  # naive time
    r, _ = reading(sensor="FS-B9-nonexistent")
    ing.handle("coffwe/v1/B9/FS-B9/x", r.model_dump_json().encode())
    ing.handle("coffwe/v1/B1/FS-B1/status", b"sleepy")
    assert ing.stats["rejected"] == 4 and len(ing._batch.rejects) == 4
    assert ing._batch.cols[0] == []


def test_seq_gaps_count_missing_messages_and_status_repeats_are_ignored():
    ing = offline_ingestor()
    for seq in (1, 2, 5):
        r, _ = reading(seq=seq)
        r = r.model_copy(update={"measured_at": r.measured_at + timedelta(minutes=15 * seq)})
        ing.handle("coffwe/v1/B1/FS-B1/soil_moisture", r.model_dump_json().encode())
    assert ing.stats["seq_gaps"] == 2
    ing.handle("coffwe/v1/B1/FS-B1/status", b"online")  # already online: no event
    ing.handle("coffwe/v1/B1/FS-B1/status", b"offline")
    assert ing.stats["status_events"] == 1


def test_psycopg_url_conversion():
    assert psycopg_url("postgresql+psycopg://raja@localhost/x") == "postgresql://raja@localhost/x"


# --- against a real broker and TimescaleDB -----------------------------------------

try:
    broker.mosquitto_bin()
    HAVE_MOSQUITTO = True
except FileNotFoundError:
    HAVE_MOSQUITTO = False
needs_infra = pytest.mark.skipif(not HAVE_MOSQUITTO, reason="mosquitto not installed")


@pytest.fixture(scope="module")
def mqtt_port(tmp_path_factory):
    port = broker.free_port()
    conf = broker.write_config(tmp_path_factory.mktemp("mosq"), port, PASSWORDS)
    proc = broker.start(conf, "127.0.0.1", port)
    yield port
    proc.terminate()
    proc.wait(5)


@pytest.fixture(scope="module")
def ts_db(db_run):
    """coffwe_test, seeded (via db_run), with only the time-series tables emptied."""
    from tests.conftest import _test_db_url

    url = psycopg_url(_test_db_url())
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("TRUNCATE sensor_readings, station_status_events, ingest_rejects")
        for view in ("sensor_readings_hourly", "sensor_readings_daily"):
            c.execute(f"CALL refresh_continuous_aggregate('{view}', NULL, NULL)")
    return url


@pytest.fixture(scope="module")
def feed_run(tmp_path_factory):
    store = engine.run(
        seed=21, start=date(2026, 11, 20), days=10, root=tmp_path_factory.mktemp("ingestrun")
    )
    generate(store.run_dir)
    return store.run_dir


class Background:
    """Run an Ingestor in its own thread + event loop, stop it on demand."""

    def __init__(self, url: str, port: int, client_id: str = "test-ingest"):
        self.ing = Ingestor(
            url, "127.0.0.1", port, "ingest", PASSWORDS["ingest"], client_id=client_id
        )
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        time.sleep(1.0)

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.ing.stop = asyncio.Event()
        self.loop.run_until_complete(self.ing.run())

    def stop(self):
        time.sleep(1.5)  # let the last messages land
        self.loop.call_soon_threadsafe(self.ing.stop.set)
        self.thread.join(30)
        return self.ing.stats


def feed(run_dir, port, start, hours=6, speed=7200):
    return run_feed(
        run_dir,
        start,
        start + pd.Timedelta(hours=hours),
        speed,
        "127.0.0.1",
        port,
        ("devices", PASSWORDS["devices"]),
        ("admin", PASSWORDS["admin"]),
    )


def q(url, sql, *args):
    with psycopg.connect(url) as c:
        return c.execute(sql, args).fetchall()


@pytest.fixture(scope="module")
def ingested(mqtt_port, ts_db, feed_run):
    bg = Background(ts_db, mqtt_port)
    start = pd.Timestamp("2026-11-25 00:00", tz=TZ)
    feed(feed_run, mqtt_port, start, hours=30)
    stats = bg.stop()
    expected = load_messages(feed_run)
    expected = expected[
        (expected.delivered_at >= start) & (expected.delivered_at < start + pd.Timedelta(hours=30))
    ]
    return stats, expected, start


@needs_infra
def test_every_unique_reading_lands_once(ingested, ts_db):
    stats, expected, _ = ingested
    unique = expected.drop_duplicates(["sensor_code", "measured_at"])
    assert q(ts_db, "SELECT count(*) FROM sensor_readings")[0][0] == len(unique)
    assert stats["duplicates"] == len(expected) - len(unique) > 0
    assert stats["rejected"] == 0


@needs_infra
def test_late_flags_match_the_devices_answer_key(ingested, ts_db, feed_run):
    _, expected, _ = ingested
    labels = TruthStore(feed_run).read("observed_labels").reset_index()
    first = expected.drop_duplicates(["sensor_code", "measured_at"])
    lag = first["delivered_at"] - first["measured_at"]
    should_be_late = int((lag > LATE_AFTER).sum())
    assert q(ts_db, "SELECT count(*) FROM sensor_readings WHERE late")[0][0] == should_be_late
    assert len(labels) > 0


@needs_infra
def test_stations_are_tracked(ingested, ts_db):
    seen = q(ts_db, "SELECT count(*) FROM sensor_stations WHERE last_seen_at IS NOT NULL")[0][0]
    assert seen == 18
    assert q(ts_db, "SELECT count(*) FROM station_status_events")[0][0] >= 18


@needs_infra
def test_hourly_rollup_equals_the_raw_average(ingested, ts_db):
    rows = q(
        ts_db,
        """
        SELECT h.avg_value, h.n_readings,
               (SELECT avg(value) FROM sensor_readings r
                 WHERE r.sensor_id = h.sensor_id AND r.quality = 'ok'
                   AND r.measured_at >= h.bucket AND r.measured_at < h.bucket + interval '1 hour')
        FROM sensor_readings_hourly h JOIN sensors s ON s.id = h.sensor_id
        WHERE s.code = 'WX-CORE-air_temperature' ORDER BY h.bucket LIMIT 12""",
    )
    assert len(rows) == 12
    for agg, n, raw in rows:
        assert n <= 4 and agg == pytest.approx(raw)


@needs_infra
def test_replaying_the_same_window_changes_nothing(ingested, ts_db, mqtt_port, feed_run):
    before = q(ts_db, "SELECT count(*), sum(value) FROM sensor_readings")[0]
    bg = Background(ts_db, mqtt_port, client_id="test-ingest-replay")
    _, _, start = ingested
    feed(feed_run, mqtt_port, start, hours=3, speed=14400)
    stats = bg.stop()
    assert stats["inserted"] == 0 and stats["duplicates"] > 0
    assert q(ts_db, "SELECT count(*), sum(value) FROM sensor_readings")[0] == before


@needs_infra
def test_faults_are_flagged_and_garbage_is_kept_for_inspection(ts_db, mqtt_port):
    bg = Background(ts_db, mqtt_port, client_id="test-ingest-faults")
    pub = new_client("fault-injector", "devices", PASSWORDS["devices"])
    connect(pub, "127.0.0.1", mqtt_port)
    spike = Reading(
        sensor="FS-B2-soil_moisture",
        measured_at=datetime(2027, 1, 1, 0, 0, tzinfo=UTC),
        value=9.9,
        unit="m3/m3",
        seq=99999,
    )
    pub.publish(
        "coffwe/v1/B2/FS-B2/soil_moisture", spike.model_dump_json(), qos=1
    ).wait_for_publish(2)
    pub.publish("coffwe/v1/B2/FS-B2/soil_moisture", "{broken", qos=1).wait_for_publish(2)
    pub.disconnect()
    bg.stop()
    row = q(
        ts_db,
        """SELECT quality FROM sensor_readings r JOIN sensors s ON s.id = r.sensor_id
                      WHERE s.code = 'FS-B2-soil_moisture' AND r.measured_at = %s""",
        spike.measured_at,
    )
    assert row == [("out_of_range",)]
    assert q(ts_db, "SELECT count(*) FROM ingest_rejects WHERE payload = '{broken'")[0][0] == 1


@needs_infra
def test_persistent_session_keeps_messages_while_ingestion_is_down(ts_db, mqtt_port):
    client_id = "test-ingest-persistent"
    Background(ts_db, mqtt_port, client_id=client_id).stop()  # register the session once
    pub = new_client("while-down", "devices", PASSWORDS["devices"])
    connect(pub, "127.0.0.1", mqtt_port)
    t0 = datetime(2027, 2, 1, 0, 0, tzinfo=UTC)
    for i in range(20):
        r = Reading(
            sensor="FS-B3-soil_temperature",
            measured_at=t0 + timedelta(minutes=15 * i),
            value=20.0 + i / 10,
            unit="degC",
            seq=50000 + i,
        )
        pub.publish(
            "coffwe/v1/B3/FS-B3/soil_temperature", r.model_dump_json(), qos=1
        ).wait_for_publish(2)
    pub.disconnect()
    stats = Background(ts_db, mqtt_port, client_id=client_id).stop()  # comes back
    assert stats["inserted"] >= 20
    got = q(
        ts_db,
        """SELECT count(*) FROM sensor_readings r JOIN sensors s ON s.id = r.sensor_id
                      WHERE s.code = 'FS-B3-soil_temperature' AND r.measured_at >= %s""",
        t0,
    )
    assert got[0][0] == 20


def test_lateness_uses_the_gateway_timestamp_not_pipeline_delay():
    info = SensorInfo(1, 1, 0.0, 0.6)
    r, _ = reading(0.3)
    on_time = r.model_copy(update={"sent_at": r.measured_at + timedelta(seconds=2)})
    # Ingestion processed it 2 sim-hours later (e.g. 2 real seconds at 3600x): not late.
    assert classify(on_time, info, r.measured_at + timedelta(hours=2)) == ("ok", False)
    burst = r.model_copy(update={"sent_at": r.measured_at + timedelta(hours=3)})
    assert classify(burst, info, r.measured_at + timedelta(hours=3)) == ("ok", True)
