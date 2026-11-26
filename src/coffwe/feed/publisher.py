"""Device publishers: each station is its own MQTT client, replaying its observed feed.

Each station connects like a real field unit would: its own client id, a last-will
("offline", retained) that the broker publishes if the connection dies uncleanly,
and a retained "online" once connected. Brownouts in the observed feed become
"offline"/"online" status changes at the right sim times.

Stations are grouped into one OS process per device group (weather mast, field
stations, process & water gauges, corridor). Every process follows the estate clock
from the retained coffwe/v1/clock message, so they all agree on "now"; when the clock
runs faster than a process can publish, it simply catches up as fast as it can.
"""

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import paho.mqtt.client as mqtt
import pandas as pd
import pyarrow.dataset as ds

from coffwe.estate_data import STATIONS
from coffwe.feed.schema import (
    CLOCK_TOPIC,
    ClockState,
    Reading,
    reading_topic,
    status_topic,
)

SITE = {s.code: s.site_code for s in STATIONS}
GROUPS: dict[str, list[str]] = {
    "weather": [s.code for s in STATIONS if s.station_type == "weather"],
    "field": [s.code for s in STATIONS if s.station_type == "field"],
    "process_water": [s.code for s in STATIONS if s.station_type in ("process", "water")],
    "corridor": [s.code for s in STATIONS if s.station_type == "corridor"],
}
MAX_SLEEP_S = 0.2  # never sleep longer than this, so clock changes are noticed quickly
KEEPALIVE_S = 5


def new_client(client_id: str, username: str, password: str) -> mqtt.Client:
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, protocol=mqtt.MQTTv311
    )
    client.username_pw_set(username, password)
    return client


def connect(client: mqtt.Client, host: str, port: int, timeout: float = 5.0) -> None:
    """Connect and wait for the broker's CONNACK; raise if it refused us."""
    done = threading.Event()
    result: dict = {}

    def on_connect(_c, _u, _flags, reason_code, _props):
        result["rc"] = reason_code
        done.set()

    client.on_connect = on_connect
    client.connect(host, port, keepalive=KEEPALIVE_S)
    client.loop_start()
    if not done.wait(timeout) or result["rc"].is_failure:
        client.loop_stop()
        raise ConnectionRefusedError(f"MQTT connect failed: {result.get('rc')}")


@dataclass
class ClockFollower:
    """Tracks the estate clock from retained clock messages."""

    state: ClockState | None = None
    changed: threading.Event = field(default_factory=threading.Event)

    def on_message(self, _client, _userdata, msg) -> None:
        self.state = ClockState.model_validate_json(msg.payload)
        self.changed.set()

    def now(self) -> datetime | None:
        s = self.state
        if s is None:
            return None
        if not s.running:
            return s.sim_time
        return s.sim_time + pd.Timedelta(seconds=(time.time() - s.wall_epoch) * s.speed)

    def wall_seconds_until(self, t: datetime) -> float:
        s, now = self.state, self.now()
        if s is None or now is None or not s.running:
            return MAX_SLEEP_S
        return max((t - now).total_seconds() / s.speed, 0.0)


def load_events(run_dir: Path, stations: list[str], start: datetime, until: datetime):
    """This group's readings (by delivery time) and status changes, in [start, until)."""
    msgs = ds.dataset(run_dir / "observed_messages", format="parquet", partitioning="hive")
    table = msgs.to_table(filter=ds.field("station_code").isin(stations))
    m = table.to_pandas().drop(columns=["year", "month"])
    m = m[(m["delivered_at"] >= start) & (m["delivered_at"] < until)]
    m = m.sort_values(["delivered_at", "sensor_code"], kind="stable").reset_index(drop=True)

    labels = ds.dataset(run_dir / "observed_labels", format="parquet", partitioning="hive")
    lab = labels.to_table(
        filter=ds.field("station_code").isin(stations),
        columns=["station_code", "measured_at", "measured"],
    ).to_pandas()
    lab = lab.drop_duplicates(["station_code", "measured_at"]).sort_values("measured_at")
    status = []
    for st, g in lab.groupby("station_code"):
        flips = g[g["measured"] != g["measured"].shift(fill_value=True)]
        for _, row in flips.iterrows():
            if start <= row["measured_at"] < until:
                status.append((row["measured_at"], st, "online" if row["measured"] else "offline"))
    return m, sorted(status)


def run_group(
    group: str,
    run_dir: str,
    start: str,
    until: str,
    host: str,
    port: int,
    username: str,
    password: str,
    stop_file: str | None = None,
    ready=None,
) -> dict:
    """One device-group process: connect its stations, replay its feed on the clock."""
    stations = GROUPS[group]
    t0, t1 = pd.Timestamp(start), pd.Timestamp(until)
    messages, status = load_events(Path(run_dir), stations, t0, t1)

    clock = ClockFollower()
    control = new_client(f"group-{group}", username, password)
    control.on_message = clock.on_message
    connect(control, host, port)
    control.subscribe(CLOCK_TOPIC, qos=1)
    if not clock.changed.wait(10):
        raise RuntimeError("no estate clock on the broker -- is the feed orchestrator running?")

    clients: dict[str, mqtt.Client] = {}
    for st in stations:
        c = new_client(f"station-{st}", username, password)
        c.will_set(status_topic(SITE[st], st), "offline", qos=1, retain=True)
        connect(c, host, port)
        c.publish(status_topic(SITE[st], st), "online", qos=1, retain=True)
        clients[st] = c

    if ready is not None:
        ready.set()  # loaded and connected: the orchestrator may start the clock

    # One merged timeline: (sim time, kind, payload)
    timeline = [(r.delivered_at, "reading", r) for r in messages.itertuples(index=False)]
    timeline += [(t, "status", (st, state)) for t, st, state in status]
    timeline.sort(key=lambda e: e[0])

    sent = behind = 0
    for when, kind, item in timeline:
        while True:
            if stop_file and Path(stop_file).exists():
                break
            wait = clock.wall_seconds_until(when)
            if wait <= 0:
                break
            time.sleep(min(wait, MAX_SLEEP_S))
        if stop_file and Path(stop_file).exists():
            break
        if clock.now() is not None and (clock.now() - when).total_seconds() > 3600:
            behind += 1  # publishing more than a sim-hour late: the clock outran us
        if kind == "status":
            st, state = item
            clients[st].publish(status_topic(SITE[st], st), state, qos=1, retain=True)
            continue
        r = item
        payload = Reading(
            sensor=r.sensor_code,
            measured_at=r.measured_at.to_pydatetime(),
            value=float(r.value),
            unit=r.unit,
            seq=int(r.seq),
            battery_v=float(r.battery_v),
            sent_at=r.delivered_at.to_pydatetime(),  # the gateway's transmit time
        ).model_dump_json()
        clients[r.station_code].publish(
            reading_topic(SITE[r.station_code], r.station_code, r.measurement),
            payload,
            qos=1,
        )
        sent += 1

    for st, c in clients.items():  # clean shutdown: say so, then disconnect (no will)
        c.publish(status_topic(SITE[st], st), "offline", qos=1, retain=True).wait_for_publish(5)
        c.disconnect()
        c.loop_stop()
    control.disconnect()
    control.loop_stop()
    return {"group": group, "sent": sent, "late_publishes": behind}


def publish_json(client: mqtt.Client, topic: str, obj, retain: bool = False) -> None:
    client.publish(
        topic, json.dumps(obj) if not isinstance(obj, str) else obj, qos=1, retain=retain
    ).wait_for_publish(5)
