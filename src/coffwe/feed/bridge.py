"""MQTT -> Google Cloud Pub/Sub bridge, with a hard monthly budget.

    uv run python -m coffwe.feed bridge [--dry-run]     # runs until Ctrl-C
    uv run python -m coffwe.feed bridge-check           # pull a few envelopes back

* Subscribes to readings and station status as a read-only MQTT client with a
  persistent session (so it catches up after a restart, like ingestion).
* Packs readings into **envelopes**: up to BATCH_READINGS per Pub/Sub message, as
  gzip-compressed JSON lines. Pub/Sub bills every publish request as at least 1 KB,
  so one reading per message would cost ~100x more than batching.
* A **quota guard** keeps a persistent monthly ledger (data/bridge/budget.json) of the
  bytes Pub/Sub will bill -- publish *and* delivery to the subscription, each at
  least 1 KB. If the next envelope would cross the monthly cap, it isn't published;
  the bridge keeps counting what it held back. Nothing is lost locally: MQTT and
  TimescaleDB still have every reading.
* Authenticates by **impersonating** the publish-only service account: short-lived
  tokens minted from your Application Default Credentials -- no key file anywhere.
"""

import gzip
import json
import os
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import paho.mqtt.client as mqtt

from coffwe.feed.publisher import connect, new_client
from coffwe.feed.schema import TOPIC_ROOT, parse_reading

BATCH_READINGS = 200
FLUSH_EVERY_S = 2.0
MIN_BILLED_BYTES = 1024  # Pub/Sub bills each request as at least 1 KB
DELIVERIES_PER_MESSAGE = 1  # one subscription (coffwe-twin-pull) receives each message
DEFAULT_BUDGET = Path("data/bridge/budget.json")
ENVELOPE_SCHEMA = "coffwe.readings.v1"


def billed_bytes(payload_size: int, attributes: dict[str, str]) -> int:
    """What Pub/Sub will bill for one message: data + attributes, >= 1 KB, for the
    publish and again for each subscription it's delivered to."""
    size = payload_size + sum(len(k) + len(v) for k, v in attributes.items())
    return max(size, MIN_BILLED_BYTES) * (1 + DELIVERIES_PER_MESSAGE)


@dataclass
class BudgetLedger:
    """Persistent monthly spend, so a restart can't reset the guard."""

    path: Path
    monthly_limit: int
    state: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.path.exists():
            self.state = json.loads(self.path.read_text())

    @staticmethod
    def month(now: datetime | None = None) -> str:
        return (now or datetime.now(UTC)).strftime("%Y-%m")  # Google bills by calendar month

    def _current(self) -> dict:
        m = self.month()
        if self.state.get("month") != m:
            self.state = {
                "month": m,
                "billed_bytes": 0,
                "envelopes": 0,
                "readings": 0,
                "held_back_envelopes": 0,
                "held_back_readings": 0,
            }
        return self.state

    def can_spend(self, n: int) -> bool:
        return self._current()["billed_bytes"] + n <= self.monthly_limit

    def record(self, billed: int, readings: int) -> None:
        s = self._current()
        s["billed_bytes"] += billed
        s["envelopes"] += 1
        s["readings"] += readings
        self._save()

    def hold_back(self, readings: int) -> None:
        s = self._current()
        s["held_back_envelopes"] += 1
        s["held_back_readings"] += readings
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=2))
        tmp.replace(self.path)  # atomic: a crash mid-write can't corrupt the ledger

    @property
    def used(self) -> int:
        return self._current()["billed_bytes"]


def make_envelope(readings: list[dict]) -> tuple[bytes, dict[str, str]]:
    lines = "\n".join(json.dumps(r, separators=(",", ":")) for r in readings).encode()
    data = gzip.compress(lines, mtime=0)
    times = sorted(r["measured_at"] for r in readings)
    attributes = {
        "schema": ENVELOPE_SCHEMA,
        "encoding": "gzip+jsonl",
        "count": str(len(readings)),
        "first_measured_at": times[0],
        "last_measured_at": times[-1],
    }
    return data, attributes


def open_envelope(data: bytes, attributes: dict[str, str]) -> list[dict]:
    if attributes.get("schema") != ENVELOPE_SCHEMA:
        raise ValueError(f"unknown envelope schema {attributes.get('schema')!r}")
    return [json.loads(line) for line in gzip.decompress(data).splitlines()]


class Sink(Protocol):
    def publish(self, data: bytes, attributes: dict[str, str]) -> None: ...


@dataclass
class MemorySink:
    """Keeps envelopes in memory (tests)."""

    envelopes: list[tuple[bytes, dict[str, str]]] = field(default_factory=list)

    def publish(self, data: bytes, attributes: dict[str, str]) -> None:
        self.envelopes.append((data, attributes))


@dataclass
class DryRunSink:
    """Counts what would be sent; never contacts Google."""

    envelopes: int = 0
    bytes: int = 0

    def publish(self, data: bytes, attributes: dict[str, str]) -> None:
        self.envelopes += 1
        self.bytes += len(data)


class PubSubSink:
    """The real thing: publishes as the impersonated publish-only service account."""

    def __init__(self, project: str, topic: str, service_account: str) -> None:
        import google.auth
        from google.auth import impersonated_credentials
        from google.cloud import pubsub_v1

        scopes = ["https://www.googleapis.com/auth/pubsub"]
        source, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds = impersonated_credentials.Credentials(
            source_credentials=source,
            target_principal=service_account,
            target_scopes=scopes,
            lifetime=3600,
        )
        # We batch ourselves (envelopes); tell the client to send each one promptly.
        settings = pubsub_v1.types.BatchSettings(max_messages=1, max_latency=0.05)
        self.client = pubsub_v1.PublisherClient(batch_settings=settings, credentials=creds)
        self.topic_path = self.client.topic_path(project, topic)
        self._futures = []

    def publish(self, data: bytes, attributes: dict[str, str]) -> None:
        self._futures.append(self.client.publish(self.topic_path, data, **attributes))
        self._futures = [f for f in self._futures if not f.done()]

    def wait(self, timeout: float = 60) -> None:
        for f in self._futures:
            f.result(timeout=timeout)  # raise if Google rejected anything


@dataclass
class Bridge:
    sink: Sink
    ledger: BudgetLedger
    stats: Counter = field(default_factory=Counter)
    _pending: list[dict] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _last_flush: float = field(default_factory=time.monotonic)

    def on_message(self, _client, _userdata, msg) -> None:
        parts = msg.topic.split("/")
        if len(parts) != 5 or parts[4] == "clock":
            return
        if parts[4] == "status":
            item = {
                "kind": "status",
                "station": parts[3],
                "state": msg.payload.decode(),
                "measured_at": datetime.now(UTC).isoformat(),
            }
        else:
            try:
                r = parse_reading(msg.payload)
            except ValueError:
                self.stats["skipped_invalid"] += 1
                return
            item = {"kind": "reading", **r.model_dump(mode="json")}
        with self._lock:
            self._pending.append(item)
            self.stats["received"] += 1
            if len(self._pending) >= BATCH_READINGS:
                self._flush_locked()

    def tick(self) -> None:
        with self._lock:
            if self._pending and time.monotonic() - self._last_flush >= FLUSH_EVERY_S:
                self._flush_locked()

    def flush(self) -> None:
        with self._lock:
            if self._pending:
                self._flush_locked()

    def _flush_locked(self) -> None:
        batch, self._pending = self._pending, []
        self._last_flush = time.monotonic()
        data, attributes = make_envelope(batch)
        cost = billed_bytes(len(data), attributes)
        if not self.ledger.can_spend(cost):
            self.ledger.hold_back(len(batch))
            self.stats["held_back"] += len(batch)
            return
        self.sink.publish(data, attributes)
        self.ledger.record(cost, len(batch))
        self.stats["forwarded"] += len(batch)
        self.stats["envelopes"] += 1
        self.stats["billed_bytes"] += cost


def run_bridge(
    bridge: Bridge,
    host: str,
    port: int,
    username: str,
    password: str,
    stop: threading.Event,
    client_id: str = "coffwe-bridge",
) -> Counter:
    client = new_client(client_id, username, password, clean_session=False)
    client.on_message = bridge.on_message
    connect(client, host, port)
    client.subscribe(f"{TOPIC_ROOT}/+/+/+", qos=1)
    try:
        while not stop.is_set():
            bridge.tick()
            time.sleep(0.2)
    finally:
        client.disconnect()
        client.loop_stop()
        bridge.flush()
        if isinstance(bridge.sink, PubSubSink):
            bridge.sink.wait()
    return bridge.stats


@dataclass(frozen=True)
class CloudSettings:
    project: str
    topic: str
    subscription: str
    service_account: str
    monthly_limit: int


def cloud_settings() -> CloudSettings:
    from dotenv import load_dotenv

    load_dotenv()
    project = os.environ.get("GCP_PROJECT", "coffwe-estate-dt")
    return CloudSettings(
        project=project,
        topic=os.environ.get("PUBSUB_TOPIC", "coffwe-sensor-readings"),
        subscription=os.environ.get("PUBSUB_SUBSCRIPTION", "coffwe-twin-pull"),
        service_account=os.environ.get(
            "BRIDGE_SERVICE_ACCOUNT", f"coffwe-bridge@{project}.iam.gserviceaccount.com"
        ),
        monthly_limit=int(os.environ.get("BRIDGE_MONTHLY_BYTES", 5 * 1024**3)),
    )


def pull_check(s: CloudSettings, max_messages: int = 5) -> list[tuple[dict, int]]:
    """Pull and acknowledge a few envelopes with your own credentials; decode them."""
    from google.cloud import pubsub_v1

    sub = pubsub_v1.SubscriberClient()
    path = sub.subscription_path(s.project, s.subscription)
    resp = sub.pull(request={"subscription": path, "max_messages": max_messages}, timeout=30)
    out = []
    for rm in resp.received_messages:
        readings = open_envelope(rm.message.data, dict(rm.message.attributes))
        out.append((dict(rm.message.attributes), len(readings)))
    if resp.received_messages:
        sub.acknowledge(
            request={"subscription": path, "ack_ids": [rm.ack_id for rm in resp.received_messages]}
        )
    return out


__all__ = ["mqtt"]
