"""Phase 3 M2: Mosquitto access control, the device publishers, last-will, clock control.

Each test module gets its own throwaway broker on a free port (never the dev broker).
"""

import subprocess
import sys
import threading
import time
from datetime import date

import pandas as pd
import pytest

from coffwe.feed import broker
from coffwe.feed.observed import generate, load_messages
from coffwe.feed.publisher import connect, load_events, new_client
from coffwe.feed.runner import run_feed
from coffwe.feed.schema import (
    CLOCK_SET_TOPIC,
    CLOCK_TOPIC,
    ClockCommand,
    ClockState,
    parse_reading,
)
from coffwe.sim import engine

PASSWORDS = {"devices": "dev-pw", "ingest": "ing-pw", "admin": "adm-pw"}

try:
    broker.mosquitto_bin()
    HAVE_MOSQUITTO = True
except FileNotFoundError:
    HAVE_MOSQUITTO = False
pytestmark = pytest.mark.skipif(not HAVE_MOSQUITTO, reason="mosquitto not installed")


@pytest.fixture(scope="module")
def mqtt_broker(tmp_path_factory):
    port = broker.free_port()
    conf = broker.write_config(tmp_path_factory.mktemp("mosquitto"), port, PASSWORDS)
    proc = broker.start(conf, "127.0.0.1", port)
    yield port
    proc.terminate()
    proc.wait(5)


@pytest.fixture(scope="module")
def run_dir(tmp_path_factory):
    store = engine.run(
        seed=11, start=date(2026, 11, 20), days=10, root=tmp_path_factory.mktemp("feedrun")
    )
    generate(store.run_dir)
    return store.run_dir


class Collector:
    def __init__(self, port: int, role: str = "ingest", topic: str = "coffwe/v1/#"):
        self.readings, self.status, self.clocks, self.other = [], {}, [], []
        self.client = new_client(f"collector-{role}-{time.time_ns()}", role, PASSWORDS[role])
        self.client.on_message = self.on_message
        connect(self.client, "127.0.0.1", port)
        self.client.subscribe(topic, qos=1)
        time.sleep(0.2)

    def on_message(self, _c, _u, m):
        if m.topic == CLOCK_TOPIC:
            self.clocks.append(ClockState.model_validate_json(m.payload))
        elif m.topic.endswith("/status"):
            self.status[m.topic] = m.payload.decode()
        elif "/clock" in m.topic:
            self.other.append(m)
        else:
            self.readings.append(parse_reading(m.payload))

    def close(self):
        self.client.disconnect()
        self.client.loop_stop()


# --- access control ----------------------------------------------------------------


def test_anonymous_and_wrong_passwords_are_refused(mqtt_broker):
    with pytest.raises(ConnectionRefusedError):
        connect(new_client("anon", "", ""), "127.0.0.1", mqtt_broker, timeout=2)
    with pytest.raises(ConnectionRefusedError):
        connect(new_client("bad", "devices", "nope"), "127.0.0.1", mqtt_broker, timeout=2)


def test_roles_can_only_do_their_job(mqtt_broker):
    topic = "coffwe/v1/B1/FS-B1/soil_moisture"
    ingest = Collector(mqtt_broker, "ingest")
    devices_listener = Collector(mqtt_broker, "devices")  # devices may NOT read readings
    pub_ingest = new_client("i-pub", "ingest", PASSWORDS["ingest"])
    connect(pub_ingest, "127.0.0.1", mqtt_broker)
    pub_ingest.publish(topic, "from-ingest", qos=1).wait_for_publish(2)  # denied write
    pub_dev = new_client("d-pub", "devices", PASSWORDS["devices"])
    connect(pub_dev, "127.0.0.1", mqtt_broker)
    pub_dev.publish(CLOCK_TOPIC, "{}", qos=1).wait_for_publish(2)  # devices can't set the clock
    good = (
        '{"sensor":"FS-B1-soil_moisture","measured_at":"2026-11-25T00:00:00+05:30",'
        '"value":0.3,"unit":"m3/m3","seq":1}'
    )
    pub_dev.publish(topic, good, qos=1).wait_for_publish(2)
    time.sleep(0.5)
    assert [r.value for r in ingest.readings] == [0.3]  # only the device's reading arrived
    assert devices_listener.readings == []
    assert ingest.clocks == []
    for c in (pub_ingest, pub_dev):
        c.disconnect()
    ingest.close()
    devices_listener.close()


# --- the feed through the broker ---------------------------------------------------


@pytest.fixture(scope="module")
def fed(mqtt_broker, run_dir):
    collector = Collector(mqtt_broker)
    start = pd.Timestamp("2026-11-25 06:00", tz="Asia/Kolkata")
    until = start + pd.Timedelta(hours=3)
    results = run_feed(
        run_dir,
        start,
        until,
        7200,
        "127.0.0.1",
        mqtt_broker,
        ("devices", PASSWORDS["devices"]),
        ("admin", PASSWORDS["admin"]),
    )
    time.sleep(0.8)
    collector.close()
    expected = load_messages(run_dir)
    expected = expected[(expected.delivered_at >= start) & (expected.delivered_at < until)]
    return collector, results, expected


def test_every_device_message_arrives_and_validates(fed):
    collector, results, expected = fed
    assert len(collector.readings) == len(expected) > 0
    assert sum(r["sent"] for r in results) == len(expected)
    got = pd.DataFrame(
        [(r.sensor, r.measured_at, r.value) for r in collector.readings],
        columns=["sensor_code", "measured_at", "value"],
    )
    merged = got.merge(expected, on=["sensor_code", "measured_at"], suffixes=("", "_exp"))
    assert (merged["value"] == merged["value_exp"]).all()


def test_no_group_fell_behind_the_clock(fed):
    _, results, _ = fed
    assert {r["group"] for r in results} == {
        "weather",
        "field_a",
        "field_b",
        "process_water",
        "corridor",
    }
    assert all(r["late_publishes"] == 0 for r in results)


def test_stations_announce_themselves_and_sign_off(fed):
    collector, _, _ = fed
    assert len(collector.status) == 18
    assert set(collector.status.values()) == {"offline"}  # clean shutdown at the end


def test_clock_is_retained_and_ends_paused(fed, mqtt_broker):
    collector, _, _ = fed
    assert collector.clocks and collector.clocks[-1].running is False
    late = Collector(mqtt_broker)  # a late joiner still gets the clock (retained)
    time.sleep(0.3)
    assert late.clocks and late.clocks[0].running is False
    late.close()


# --- last-will and clock control ---------------------------------------------------

CRASHING_STATION = """
import os, sys, time
from coffwe.feed.publisher import connect, new_client
c = new_client("station-CRASH", "devices", sys.argv[2])
c.will_set("coffwe/v1/B1/CRASH/status", "offline", qos=1, retain=True)
connect(c, "127.0.0.1", int(sys.argv[1]))
c.publish("coffwe/v1/B1/CRASH/status", "online", qos=1, retain=True).wait_for_publish(2)
os._exit(1)  # die without a clean MQTT disconnect, like a station losing power
"""


def test_last_will_marks_a_crashed_station_offline(mqtt_broker):
    collector = Collector(mqtt_broker)
    subprocess.run(
        [sys.executable, "-c", CRASHING_STATION, str(mqtt_broker), PASSWORDS["devices"]],
        check=False,
        timeout=20,
    )
    deadline = time.monotonic() + 15
    while collector.status.get("coffwe/v1/B1/CRASH/status") != "offline":
        assert time.monotonic() < deadline, collector.status
        time.sleep(0.1)
    collector.close()


def test_operators_can_change_speed_mid_run(mqtt_broker, run_dir):
    collector = Collector(mqtt_broker)
    start = pd.Timestamp("2026-11-26 00:00", tz="Asia/Kolkata")

    def change_speed():
        time.sleep(4.0)
        op = new_client("operator", "admin", PASSWORDS["admin"])
        connect(op, "127.0.0.1", mqtt_broker)
        op.publish(
            CLOCK_SET_TOPIC, ClockCommand(speed=36000).model_dump_json(), qos=1
        ).wait_for_publish(2)
        op.disconnect()

    threading.Thread(target=change_speed, daemon=True).start()
    run_feed(
        run_dir,
        start,
        start + pd.Timedelta(hours=12),
        3600,
        "127.0.0.1",
        mqtt_broker,
        ("devices", PASSWORDS["devices"]),
        ("admin", PASSWORDS["admin"]),
        groups=["weather"],
    )
    collector.close()
    speeds = [c.speed for c in collector.clocks]
    assert 3600 in speeds and 36000 in speeds
    times = [c.sim_time for c in collector.clocks]
    assert times == sorted(times)  # re-anchoring never jumps time backwards


# --- brownouts become status changes -----------------------------------------------


def test_brownouts_in_the_labels_become_offline_online_status(run_dir):
    from coffwe.sim.truth import TruthStore

    store = TruthStore(run_dir)
    labels = store.read("observed_labels").reset_index()
    labels.loc[
        (labels["station_code"] == "FS-B4") & (labels["measured_at"].dt.date == date(2026, 11, 26)),
        "measured",
    ] = False
    store.write("observed_labels", labels.set_index("measured_at"))
    start = pd.Timestamp("2026-11-25", tz="Asia/Kolkata")
    _, status = load_events(run_dir, ["FS-B4"], start, start + pd.Timedelta(days=3))
    states = [(t.date(), s) for t, _, s in status]
    assert states == [(date(2026, 11, 26), "offline"), (date(2026, 11, 27), "online")]


def test_nothing_is_lost_when_the_clock_outruns_the_publishers(mqtt_broker, run_dir):
    """At 200,000x the publishers fall far behind the clock; they must still deliver
    every reading (an early stop at `until` once dropped the unsent backlog)."""
    collector = Collector(mqtt_broker)
    start = pd.Timestamp("2026-11-27 00:00", tz="Asia/Kolkata")
    until = start + pd.Timedelta(hours=24)
    results = run_feed(
        run_dir,
        start,
        until,
        200_000,
        "127.0.0.1",
        mqtt_broker,
        ("devices", PASSWORDS["devices"]),
        ("admin", PASSWORDS["admin"]),
    )
    time.sleep(1.5)
    collector.close()
    expected = load_messages(run_dir)
    expected = expected[(expected.delivered_at >= start) & (expected.delivered_at < until)]
    assert any(r["late_publishes"] > 0 for r in results)  # it really did fall behind
    assert len(collector.readings) == len(expected)
