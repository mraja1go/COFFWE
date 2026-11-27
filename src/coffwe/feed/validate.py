"""Phase 3 end-to-end validation: replay the twin era through the real pipeline and
measure what reaches TimescaleDB against the devices' answer key.

    uv run python -m coffwe.feed validate [--speed 40000] [--hours N]

Self-contained: it starts its own throwaway broker (fresh random passwords), empties
only the time-series tables of the dev database, runs ingestion and the feed, then
writes docs/phase3-report.md.
"""

import asyncio
import os
import secrets
import tempfile
import threading
import time
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg

from coffwe.feed import broker
from coffwe.feed.ingest import LATE_AFTER, Ingestor, psycopg_url
from coffwe.feed.observed import generate
from coffwe.feed.runner import run_feed
from coffwe.sim.truth import TruthStore
from coffwe.sim.validation import _md_table

REPORT = Path("docs/phase3-report.md")
TZ = "Asia/Kolkata"


def _prepare_db(url: str) -> None:
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("TRUNCATE sensor_readings, station_status_events, ingest_rejects")
        for view in ("sensor_readings_hourly", "sensor_readings_daily"):
            c.execute(f"CALL refresh_continuous_aggregate('{view}', NULL, NULL)")


def replay(run_dir: Path, start, until, speed: float, db_url: str) -> dict:
    """Throwaway broker + ingestion + feed. Returns timings and counters."""
    passwords = {r: secrets.token_urlsafe(12) for r in broker.ROLES}
    port = broker.free_port()
    conf = broker.write_config(Path(tempfile.mkdtemp()), port, passwords)
    proc = broker.start(conf, "127.0.0.1", port)
    ing = Ingestor(
        db_url, "127.0.0.1", port, "ingest", passwords["ingest"], client_id="validate-ingest"
    )
    loop = asyncio.new_event_loop()

    def run_ingest():
        asyncio.set_event_loop(loop)
        ing.stop = asyncio.Event()
        loop.run_until_complete(ing.run())

    thread = threading.Thread(target=run_ingest, daemon=True)
    thread.start()
    time.sleep(1.5)
    t0 = time.perf_counter()
    groups = run_feed(
        run_dir,
        start,
        until,
        speed,
        "127.0.0.1",
        port,
        ("devices", passwords["devices"]),
        ("admin", passwords["admin"]),
    )
    wall = time.perf_counter() - t0
    # Stop ingestion only once it has gone quiet: at ~3k msg/s it can be seconds behind
    # the publishers, and this throwaway broker won't keep its queue afterwards.
    last, quiet_since = -1, time.monotonic()
    while time.monotonic() - quiet_since < 3.0:
        seen = ing.stats["messages"]
        if seen != last:
            last, quiet_since = seen, time.monotonic()
        time.sleep(0.2)
    loop.call_soon_threadsafe(ing.stop.set)
    thread.join(60)
    proc.terminate()
    log = conf.parent / "mosquitto.log"
    drops = log.read_text().count("being dropped") if log.exists() else 0
    with psycopg.connect(db_url, autocommit=True) as c:
        for view in ("sensor_readings_hourly", "sensor_readings_daily"):
            c.execute(f"CALL refresh_continuous_aggregate('{view}', NULL, NULL)")
    return {"wall_s": wall, "groups": groups, "ingest": Counter(ing.stats), "broker_drops": drops}


def measure(run_dir: Path, start, until, db_url: str) -> dict:
    store = TruthStore(run_dir)
    labels = store.read("observed_labels").reset_index()
    labels = labels[(labels["measured_at"] >= start) & (labels["measured_at"] < until)]
    with psycopg.connect(db_url) as c:
        db = pd.DataFrame(
            c.execute("""
            SELECT s.code AS sensor_code, r.measured_at, r.value AS db_value, r.quality, r.late
            FROM sensor_readings r JOIN sensors s ON s.id = r.sensor_id""").fetchall(),
            columns=["sensor_code", "measured_at", "db_value", "quality", "late"],
        )
        daily = pd.DataFrame(
            c.execute("""
            SELECT d.bucket, d.avg_value FROM sensor_readings_daily d
            JOIN sensors s ON s.id = d.sensor_id WHERE s.code = 'WX-CORE-air_temperature'
            ORDER BY d.bucket""").fetchall(),
            columns=["bucket", "avg_value"],
        )
        clock = c.execute("SELECT estate_now(), estate_today()").fetchone()
        status = c.execute("SELECT count(*) FROM station_status_events").fetchone()[0]
    db["measured_at"] = pd.to_datetime(db["measured_at"], utc=True).dt.tz_convert(TZ)
    joined = labels.merge(db, on=["sensor_code", "measured_at"], how="left")
    joined["stored"] = joined["db_value"].notna()
    weather = store.read("weather_hourly")
    truth_daily = weather["air_temperature"].groupby(weather.index.normalize()).mean()
    daily["bucket"] = pd.to_datetime(daily["bucket"], utc=True).dt.tz_convert(TZ).dt.normalize()
    daily = daily.set_index("bucket")["avg_value"].astype(float)
    common = truth_daily.index.intersection(daily.index)
    return {
        "labels": joined,
        "daily_db": daily.loc[common],
        "daily_truth": truth_daily.loc[common],
        "clock": clock,
        "status_events": status,
    }


def _fmt(df: pd.DataFrame, formats: dict[str, str], default: str = "{}") -> pd.DataFrame:
    """Format each column on its own (counts as integers, errors to 4 places...)."""
    out = df.copy().astype(object)
    for col in df.columns:
        f = formats.get(col, default)
        out[col] = [f.format(v) for v in df[col]]
    return out


def _funnel(j: pd.DataFrame) -> pd.DataFrame:
    n = len(j)
    rows = {
        "scheduled (every sensor, every 15 min)": n,
        "measured (station had power)": int(j["measured"].sum()),
        "transmitted (radio got it through)": int(j["delivered"].sum()),
        "stored in TimescaleDB": int(j["stored"].sum()),
    }
    return pd.DataFrame({"readings": rows, "share %": {k: 100 * v / n for k, v in rows.items()}})


def _accuracy(j: pd.DataFrame) -> pd.DataFrame:
    ok = j[j["stored"] & (j["fault"] == "")]
    err = ok["db_value"] - ok["true_value"]
    g = err.groupby(ok["measurement"])
    t = pd.DataFrame(
        {
            "n": g.size(),
            "bias": g.mean(),
            "MAE": g.apply(lambda e: e.abs().mean()),
            "RMSE": g.apply(lambda e: float(np.sqrt((e**2).mean()))),
        }
    )
    return t


def _faults(j: pd.DataFrame) -> pd.DataFrame:
    f = j[j["stored"] & (j["fault"] != "")]
    g = f.groupby("fault")
    return pd.DataFrame(
        {
            "stored": g.size(),
            "flagged out_of_range": g.apply(lambda x: (x["quality"] == "out_of_range").sum()),
        }
    )


def write_report(
    run_dir: Path, start, until, rep: dict, m: dict, speed: float, path: Path = REPORT
) -> Path:
    j = m["labels"]
    ing = rep["ingest"]
    delivered = j[j["delivered"]]
    lag_min = (delivered["delivered_at"] - delivered["measured_at"]).dt.total_seconds() / 60
    stored_late = int(j.loc[j["stored"], "late"].sum())
    lost = j[~j["delivered"]]["link_status"].value_counts().to_dict()
    pipeline_lost = int((j["delivered"] & ~j["stored"]).sum())
    in_buffer = int((j["delivered"] & ~j["stored"] & (j["delivered_at"] >= until)).sum())
    lost_in_pipeline = pipeline_lost - in_buffer
    stored_j = j[j["stored"]]
    stored_lag = (stored_j["delivered_at"] - stored_j["measured_at"]).dt.total_seconds()
    late_expected_stored = int((stored_lag > LATE_AFTER.total_seconds()).sum())
    pipeline_sentence = (
        "**Everything the radio delivered within the window was stored — the pipeline itself"
        " (broker → ingestion → database) lost nothing.**"
        if lost_in_pipeline == 0
        else f"**{lost_in_pipeline:,} readings were delivered by the radio but never stored — a"
        " pipeline loss to investigate.**"
    )
    daily_err = (m["daily_db"] - m["daily_truth"]).abs()
    by_station = j.groupby("station_code")["stored"].mean().mul(100)
    groups = ", ".join(
        f"{g['group']} {g['sent']:,} ({g['late_publishes']} behind)" for g in rep["groups"]
    )
    n_msgs = sum(g["sent"] for g in rep["groups"])
    path.write_text(f"""# Phase 3 validation report — the live feed, end to end

_Generated by `uv run python -m coffwe.feed validate --speed {speed:g}`: run `{run_dir.name}`,
sim window {start:%d %b %Y} → {until:%d %b %Y} replayed at {speed:,.0f}× through a throwaway
Mosquitto broker and the ingestion service into TimescaleDB, then compared reading by
reading with the virtual devices' answer key (`observed_labels`)._

## Pipeline run
- {n_msgs:,} MQTT messages in {rep["wall_s"]:.0f} s real time ({n_msgs / rep["wall_s"]:,.0f} msg/s). Per group: {groups}.
- Ingestion: {ing["messages"]:,} messages handled, {ing["inserted"]:,} readings stored,
  **{ing["duplicates"]:,} duplicates absorbed** by the primary key, {ing["rejected"]} rejected,
  {ing["status_events"]} station status events, {ing["aggregate_refreshes"]} rollup refreshes.
- Broker queue-overflow warnings ("outgoing messages are being dropped"): {rep["broker_drops"]}.
- The database's clock after the run: `estate_now()` = {m["clock"][0]}, `estate_today()` = {m["clock"][1]}.

## Completeness: where readings go missing
{_md_table(_fmt(_funnel(j), {"readings": "{:,.0f}", "share %": "{:.2f}"}), "{}")}

Readings that never arrived, by cause: {lost}. {pipeline_sentence} ({in_buffer:,} readings
measured near the end were still in a station's store-and-forward buffer when the window closed.)

Stored share by station (min / median / max): {by_station.min():.2f}% / {by_station.median():.2f}% / {by_station.max():.2f}%.

## Timeliness
Field-network delay (measured → transmitted): median {lag_min.median():.2f} min, 99th percentile
{lag_min.quantile(0.99):.1f} min, worst {lag_min.max() / 60:.1f} h (store-and-forward bursts after radio
outages). Of the stored readings, the answer key says {late_expected_stored:,} were delayed more than
{LATE_AFTER.total_seconds() / 60:.0f} min; the database flagged {stored_late:,} as `late`.

## Accuracy: observed vs truth (fault-free readings)
Calibration bias, drift, noise and resolution — what a real deployment would see.

{_md_table(_fmt(_accuracy(j), {"n": "{:,.0f}"}, default="{:.4f}"), "{}")}

## Faults: what simple rules catch
Every injected fault is stored (none are dropped). The only automatic rule so far is the
physical-range check:

{_md_table(_fmt(_faults(j), {}, default="{:,.0f}"), "{}")}

Spikes beyond the physical range are flagged; stuck values, false triggers, missed detections
and clogged gauges look like plausible numbers and pass straight through. Detecting those needs
context across time and neighbouring sensors — the job of Phase 6 anomaly detection, which can
be trained and scored against these labels.

## Rollups vs truth
Daily mean air temperature at the mast from the `sensor_readings_daily` continuous aggregate vs
the simulator's true daily mean, over {len(daily_err)} days: mean absolute difference
{daily_err.mean():.2f} degC, worst {daily_err.max():.2f} degC (sensor calibration + noise).
""")
    return path


def run(speed: float = 40000.0, hours: float | None = None, run_dir: Path | None = None) -> Path:
    from dotenv import load_dotenv

    from coffwe.feed.__main__ import latest_run

    load_dotenv()
    run_dir = run_dir or latest_run()
    store = TruthStore(run_dir)
    if not (run_dir / "observed_messages").exists():
        generate(run_dir)
    info = store.read_info()
    start = pd.Timestamp(info.events_start, tz=TZ)
    end = pd.Timestamp(info.end, tz=TZ)
    until = min(end, start + pd.Timedelta(hours=hours)) if hours else end
    db_url = psycopg_url(os.environ["DATABASE_URL"])
    _prepare_db(db_url)
    rep = replay(run_dir, start, until, speed, db_url)
    m = measure(run_dir, start, until, db_url)
    return write_report(run_dir, start, until, rep, m, speed)
