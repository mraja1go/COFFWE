"""Ingestion: MQTT -> validation -> TimescaleDB.

    uv run python -m coffwe.feed ingest        # runs until Ctrl-C

One asyncio service (aiomqtt + async psycopg):

* Subscribes to coffwe/v1/# with a *persistent session* (fixed client id,
  clean_session=False): if ingestion is down, the broker queues QoS-1 messages and
  hands them over when it reconnects -- nothing is lost across a restart.
* Every reading is parsed with the same Pydantic model the devices use. Anything
  malformed or from an unknown sensor goes to the ingest_rejects dead-letter table.
* Valid readings are *never* dropped: out-of-range values are flagged, late ones are
  flagged and placed by their measurement time, and duplicates (QoS-1 retransmits,
  replays) are absorbed by the (sensor_id, measured_at) primary key.
* Writes are batched: one INSERT ... SELECT FROM unnest(arrays) per batch.
* received_at is the *estate clock* time (followed from coffwe/v1/clock), so "how
  late was this?" is measured in the estate's own time.
* As the sim clock crosses midnight, the continuous aggregates are refreshed for the
  last few days (refresh policies run on the wall clock, which our sim data ignores).
"""

import asyncio
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import aiomqtt
import pandas as pd
import psycopg
from pydantic import ValidationError

from coffwe.feed.schema import CLOCK_TOPIC, ClockState, parse_reading
from coffwe.feed.truth import REPORT_MINUTES

LATE_AFTER = timedelta(minutes=2 * REPORT_MINUTES)
BATCH_SIZE = 2000
FLUSH_EVERY_S = 0.5
AGGREGATES = ("sensor_readings_hourly", "sensor_readings_daily")
REFRESH_LOOKBACK = timedelta(days=3)  # late bursts can be ~half a day behind
CLIENT_ID = "coffwe-ingest"

INSERT_READINGS = """
INSERT INTO sensor_readings
    (sensor_id, measured_at, received_at, value, quality, late, seq, battery_v)
SELECT * FROM unnest(
    %s::int[], %s::timestamptz[], %s::timestamptz[], %s::float8[],
    %s::text[], %s::bool[], %s::int[], %s::float8[])
ON CONFLICT (sensor_id, measured_at) DO NOTHING
"""


# The estate's "now" for SQL (estate_now()): same anchor + speed as the MQTT clock.
UPSERT_CLOCK = """
INSERT INTO estate_clock (id, sim_time, wall_at, speed, running)
VALUES (1, %s, to_timestamp(%s), %s, %s)
ON CONFLICT (id) DO UPDATE SET sim_time = EXCLUDED.sim_time, wall_at = EXCLUDED.wall_at,
    speed = EXCLUDED.speed, running = EXCLUDED.running
"""


def psycopg_url(sqlalchemy_url: str) -> str:
    """'postgresql+psycopg://...' (SQLAlchemy) -> 'postgresql://...' (psycopg)."""
    return sqlalchemy_url.replace("postgresql+psycopg://", "postgresql://", 1)


@dataclass(frozen=True)
class SensorInfo:
    id: int
    station_id: int
    valid_min: float | None
    valid_max: float | None


@dataclass
class Catalog:
    sensors: dict[str, SensorInfo]
    stations: dict[str, int]
    station_status: dict[int, str]

    @classmethod
    async def load(cls, conn: psycopg.AsyncConnection) -> Catalog:
        cur = await conn.execute("SELECT code, id, station_id, valid_min, valid_max FROM sensors")
        sensors = {
            code: SensorInfo(
                sid, st, None if lo is None else float(lo), None if hi is None else float(hi)
            )
            for code, sid, st, lo, hi in await cur.fetchall()
        }
        cur = await conn.execute("SELECT code, id, status FROM sensor_stations")
        rows = await cur.fetchall()
        return cls(sensors, {c: i for c, i, _ in rows}, {i: s for _, i, s in rows})


@dataclass
class Batch:
    cols: list[list] = field(default_factory=lambda: [[] for _ in range(8)])
    station_seen: dict[int, datetime] = field(default_factory=dict)
    statuses: list[tuple[int, datetime, str]] = field(default_factory=list)
    rejects: list[tuple[datetime, str, str, str]] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.cols[0]) + len(self.statuses) + len(self.rejects)


def classify(reading, info: SensorInfo, received_at: datetime) -> tuple[str, bool]:
    """(quality, late) for a valid reading. Lateness is the field network's delay --
    the gateway's sent_at if the message carries one, else our own receive time."""
    out = (info.valid_min is not None and reading.value < info.valid_min) or (
        info.valid_max is not None and reading.value > info.valid_max
    )
    late = (reading.sent_at or received_at) - reading.measured_at > LATE_AFTER
    return ("out_of_range" if out else "ok"), late


@dataclass
class Ingestor:
    db_url: str
    host: str
    port: int
    username: str
    password: str
    client_id: str = CLIENT_ID
    stats: Counter = field(default_factory=Counter)
    clock: ClockState | None = None
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    _batch: Batch = field(default_factory=Batch)
    _seq: dict[int, int] = field(default_factory=dict)
    _last_refresh_day: object = None
    _clock_dirty: bool = False

    def now(self, fallback: datetime) -> datetime:
        """The estate clock's current sim time (or the reading's own time, if no clock yet)."""
        c = self.clock
        if c is None:
            return fallback
        if not c.running:
            return c.sim_time
        import time

        return c.sim_time + timedelta(seconds=(time.time() - c.wall_epoch) * c.speed)

    # --- message handling ------------------------------------------------------------

    def handle(self, topic: str, payload: bytes) -> None:
        self.stats["messages"] += 1
        if topic == CLOCK_TOPIC:
            self.clock = ClockState.model_validate_json(payload)
            self._clock_dirty = True  # mirror it into estate_clock on the next flush
            return
        parts = topic.split("/")
        if len(parts) == 5 and parts[4] == "status":
            self._status(topic, parts[3], payload)
        elif len(parts) == 5 and parts[4] not in ("clock",):
            self._reading(topic, payload)
        # cmd topics and clock/set are not ingestion's business

    def _reject(self, topic: str, payload: bytes, error: str) -> None:
        self.stats["rejected"] += 1
        text = payload.decode("utf-8", errors="replace")[:2000]
        now = self.now(datetime.now().astimezone())
        self._batch.rejects.append((now, topic[:200], text, error[:2000]))

    def _status(self, topic: str, station: str, payload: bytes) -> None:
        state = payload.decode(errors="replace").strip()
        station_id = self.catalog.stations.get(station)
        if station_id is None or state not in ("online", "offline"):
            self._reject(topic, payload, f"bad status for {station!r}: {state!r}")
            return
        if self.catalog.station_status.get(station_id) == state:
            return  # retained repeat (e.g. after a reconnect): not a new event
        self.catalog.station_status[station_id] = state
        when = self.now(datetime.now().astimezone())
        self._batch.statuses.append((station_id, when, state))
        self.stats["status_events"] += 1

    def _reading(self, topic: str, payload: bytes) -> None:
        try:
            r = parse_reading(payload)
        except ValidationError as err:
            self._reject(topic, payload, f"schema: {err.errors()[0]['msg']}")
            return
        info = self.catalog.sensors.get(r.sensor)
        if info is None:
            self._reject(topic, payload, f"unknown sensor {r.sensor!r}")
            return
        received = max(self.now(r.measured_at), r.measured_at)
        quality, late = classify(r, info, received)
        self.stats["out_of_range"] += quality != "ok"
        self.stats["late"] += late
        last = self._seq.get(info.id)
        if last is not None and r.seq > last + 1:
            self.stats["seq_gaps"] += r.seq - last - 1  # messages that never arrived (yet)
        self._seq[info.id] = max(r.seq, last or 0)
        for col, v in zip(
            self._batch.cols,
            (info.id, r.measured_at, received, r.value, quality, late, r.seq, r.battery_v),
            strict=True,
        ):
            col.append(v)
        seen = self._batch.station_seen.get(info.station_id)
        if seen is None or r.measured_at > seen:
            self._batch.station_seen[info.station_id] = r.measured_at

    # --- writing ---------------------------------------------------------------------

    async def flush(self, conn: psycopg.AsyncConnection) -> None:
        batch, self._batch = self._batch, Batch()
        clock, self._clock_dirty = (self.clock if self._clock_dirty else None), False
        if not len(batch) and clock is None:
            return
        async with conn.transaction():
            if clock is not None:
                await conn.execute(
                    UPSERT_CLOCK, (clock.sim_time, clock.wall_epoch, clock.speed, clock.running)
                )
            if batch.cols[0]:
                cur = await conn.execute(INSERT_READINGS, batch.cols)
                inserted = cur.rowcount
                self.stats["inserted"] += inserted
                self.stats["duplicates"] += len(batch.cols[0]) - inserted
            for station_id, when, state in batch.statuses:
                await conn.execute(
                    "INSERT INTO station_status_events (station_id, occurred_at, status)"
                    " VALUES (%s, %s, %s)",
                    (station_id, when, state),
                )
                await conn.execute(
                    "UPDATE sensor_stations SET status = %s WHERE id = %s", (state, station_id)
                )
            for station_id, seen in batch.station_seen.items():
                await conn.execute(
                    "UPDATE sensor_stations SET last_seen_at = GREATEST(last_seen_at, %s)"
                    " WHERE id = %s",
                    (seen, station_id),
                )
            if batch.rejects:
                async with conn.cursor() as cur:
                    await cur.executemany(
                        "INSERT INTO ingest_rejects (received_at, topic, payload, error)"
                        " VALUES (%s, %s, %s, %s)",
                        batch.rejects,
                    )

    async def refresh_aggregates(self, until: datetime) -> None:
        """Materialize the rollups up to `until` (sim time) -- needs its own autocommit
        connection: refresh_continuous_aggregate can't run inside a transaction."""
        start = until - REFRESH_LOOKBACK
        async with await psycopg.AsyncConnection.connect(self.db_url, autocommit=True) as conn:
            for view in AGGREGATES:
                await conn.execute(
                    "CALL refresh_continuous_aggregate(%s, %s, %s)", (view, start, until)
                )
        self.stats["aggregate_refreshes"] += 1

    async def _maybe_refresh(self) -> None:
        if self.clock is None:
            return
        today = pd.Timestamp(self.now(self.clock.sim_time)).tz_convert("Asia/Kolkata").normalize()
        if self._last_refresh_day is None:
            self._last_refresh_day = today
        elif today > self._last_refresh_day:
            self._last_refresh_day = today
            await self.refresh_aggregates(today.to_pydatetime())

    # --- main loop -------------------------------------------------------------------

    async def run(self) -> Counter:
        async with await psycopg.AsyncConnection.connect(self.db_url) as conn:
            self.catalog = await Catalog.load(conn)
            await conn.commit()
            async with aiomqtt.Client(
                self.host,
                self.port,
                username=self.username,
                password=self.password,
                identifier=self.client_id,
                clean_session=False,
            ) as client:
                await client.subscribe("coffwe/v1/#", qos=1)
                messages = client.messages.__aiter__()
                pending: asyncio.Task | None = None
                while not self.stop.is_set():
                    if pending is None:
                        pending = asyncio.ensure_future(messages.__anext__())
                    done, _ = await asyncio.wait({pending}, timeout=FLUSH_EVERY_S)
                    if pending in done:
                        m = pending.result()
                        pending = None
                        self.handle(m.topic.value, bytes(m.payload))
                        if len(self._batch) < BATCH_SIZE:
                            continue
                    await self.flush(conn)
                    await self._maybe_refresh()
                if pending is not None:
                    pending.cancel()
                await self.flush(conn)
        return self.stats


def from_env(stop: asyncio.Event | None = None, client_id: str = CLIENT_ID) -> Ingestor:
    from dotenv import load_dotenv

    from coffwe.feed import broker

    load_dotenv()
    s = broker.settings()
    ing = Ingestor(
        psycopg_url(os.environ["DATABASE_URL"]),
        s.host,
        s.port,
        *s.credentials("ingest"),
        client_id=client_id,
    )
    if stop is not None:
        ing.stop = stop
    return ing
