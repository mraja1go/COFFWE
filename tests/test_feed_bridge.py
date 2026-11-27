"""Phase 3 M5: the MQTT -> Pub/Sub bridge -- envelopes, billing estimate, the budget
guard, and forwarding through a real (throwaway) broker into an in-memory sink."""

import threading
import time
from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest

from coffwe.feed import broker
from coffwe.feed.bridge import (
    BATCH_READINGS,
    MIN_BILLED_BYTES,
    Bridge,
    BudgetLedger,
    DryRunSink,
    MemorySink,
    billed_bytes,
    make_envelope,
    open_envelope,
    run_bridge,
)
from coffwe.feed.observed import generate, load_messages
from coffwe.feed.runner import run_feed
from coffwe.sim import engine

PASSWORDS = {"devices": "dev-pw", "ingest": "ing-pw", "admin": "adm-pw"}


def fake_readings(n: int) -> list[dict]:
    t0 = datetime(2026, 11, 25, tzinfo=UTC)
    return [
        {
            "kind": "reading",
            "sensor": "FS-B1-soil_moisture",
            "measured_at": (t0 + timedelta(minutes=15 * i)).isoformat(),
            "value": 0.3,
            "unit": "m3/m3",
            "seq": i,
        }
        for i in range(n)
    ]


# --- envelopes and billing ---------------------------------------------------------


def test_envelopes_round_trip_and_compress():
    readings = fake_readings(BATCH_READINGS)
    data, attrs = make_envelope(readings)
    assert open_envelope(data, attrs) == readings
    raw = sum(len(str(r)) for r in readings)
    assert len(data) < raw / 5  # repetitive JSON compresses well
    assert attrs["count"] == str(BATCH_READINGS)
    assert attrs["first_measured_at"] <= attrs["last_measured_at"]
    with pytest.raises(ValueError):
        open_envelope(data, {**attrs, "schema": "something-else"})


def test_billing_counts_publish_and_delivery_with_a_1kb_floor():
    assert billed_bytes(10, {}) == 2 * MIN_BILLED_BYTES
    assert billed_bytes(5000, {"a": "bb"}) == 2 * 5003


def test_batching_is_far_cheaper_than_one_message_per_reading():
    readings = fake_readings(BATCH_READINGS)
    data, attrs = make_envelope(readings)
    batched = billed_bytes(len(data), attrs)
    singly = sum(billed_bytes(len(make_envelope([r])[0]), make_envelope([r])[1]) for r in readings)
    assert singly > 50 * batched


# --- the budget guard --------------------------------------------------------------


def test_ledger_survives_restarts_and_resets_each_month(tmp_path, monkeypatch):
    path = tmp_path / "budget.json"
    ledger = BudgetLedger(path, monthly_limit=10_000)
    ledger.record(4_000, readings=200)
    again = BudgetLedger(path, monthly_limit=10_000)  # a restart can't reset the guard
    assert again.used == 4_000 and again.can_spend(6_000) and not again.can_spend(6_001)
    monkeypatch.setattr(BudgetLedger, "month", staticmethod(lambda now=None: "2099-01"))
    assert BudgetLedger(path, 10_000).used == 0  # a new calendar month starts fresh


def test_bridge_holds_back_once_the_budget_is_spent(tmp_path):
    readings = fake_readings(BATCH_READINGS)
    data, attrs = make_envelope(readings)
    one_envelope = billed_bytes(len(data), attrs)
    sink = MemorySink()
    bridge = Bridge(sink, BudgetLedger(tmp_path / "b.json", monthly_limit=int(2.5 * one_envelope)))

    class Msg:
        def __init__(self, r):
            self.topic = "coffwe/v1/B1/FS-B1/soil_moisture"
            import json

            self.payload = json.dumps({k: v for k, v in r.items() if k != "kind"}).encode()

    for _ in range(4):
        for r in readings:
            bridge.on_message(None, None, Msg(r))
    assert len(sink.envelopes) == 2  # the third and fourth would cross the cap
    assert bridge.stats["held_back"] == 2 * BATCH_READINGS
    assert bridge.ledger.used <= bridge.ledger.monthly_limit
    assert bridge.ledger.state["held_back_envelopes"] == 2


def test_dry_run_never_needs_google():
    sink = DryRunSink()
    data, attrs = make_envelope(fake_readings(10))
    sink.publish(data, attrs)
    assert sink.envelopes == 1 and sink.bytes == len(data)


# --- forwarding through a real broker ----------------------------------------------

try:
    broker.mosquitto_bin()
    HAVE_MOSQUITTO = True
except FileNotFoundError:
    HAVE_MOSQUITTO = False
needs_broker = pytest.mark.skipif(not HAVE_MOSQUITTO, reason="mosquitto not installed")


@pytest.fixture(scope="module")
def mqtt_port(tmp_path_factory):
    port = broker.free_port()
    conf = broker.write_config(tmp_path_factory.mktemp("mosq"), port, PASSWORDS)
    proc = broker.start(conf, "127.0.0.1", port)
    yield port
    proc.terminate()
    proc.wait(5)


@needs_broker
def test_every_reading_reaches_the_cloud_sink_in_envelopes(mqtt_port, tmp_path):
    store = engine.run(seed=41, start=date(2026, 11, 22), days=6, root=tmp_path)
    generate(store.run_dir)
    sink = MemorySink()
    bridge = Bridge(sink, BudgetLedger(tmp_path / "budget.json", monthly_limit=5 * 1024**3))
    stop = threading.Event()
    result = {}
    t = threading.Thread(
        target=lambda: result.update(
            run_bridge(
                bridge,
                "127.0.0.1",
                mqtt_port,
                "ingest",
                PASSWORDS["ingest"],
                stop,
                client_id="test-bridge",
            )
        )
    )
    t.start()
    time.sleep(1.0)
    start = pd.Timestamp("2026-11-25", tz="Asia/Kolkata")
    until = start + pd.Timedelta(hours=12)
    run_feed(
        store.run_dir,
        start,
        until,
        7200,
        "127.0.0.1",
        mqtt_port,
        ("devices", PASSWORDS["devices"]),
        ("admin", PASSWORDS["admin"]),
    )
    time.sleep(2.5)
    stop.set()
    t.join(30)
    forwarded = [r for d, a in sink.envelopes for r in open_envelope(d, a)]
    readings = [r for r in forwarded if r["kind"] == "reading"]
    expected = load_messages(store.run_dir)
    expected = expected[(expected.delivered_at >= start) & (expected.delivered_at < until)]
    assert len(readings) == len(expected)  # duplicates included: the cloud de-dupes like we do
    assert {r["kind"] for r in forwarded} == {"reading", "status"}
    assert all(int(a["count"]) <= BATCH_READINGS for _, a in sink.envelopes)
    ledger = bridge.ledger.state
    assert ledger["envelopes"] == len(sink.envelopes)
    assert ledger["billed_bytes"] < ledger["readings"] * 50  # << 1 KB per reading
    assert datetime.now(UTC).strftime("%Y-%m") == ledger["month"]
