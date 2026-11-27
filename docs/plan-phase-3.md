# Phase 3 — Live feed / integration layer (plan)

_Agreed 2026-11-25; **completed 2026-11-27** (M1–M5, see `PROGRESS.md` and `docs/phase3-report.md`). Phase 2 built "the real estate" (ground truth in Parquet). Phase 3 builds how the estate is **observed**: imperfect field hardware reporting over a real IoT protocol into a time-series store — the data the digital twin (Phase 4) and the AI brains (Phases 5–7) will actually see._

## Decisions

| Decision | Choice | Why |
|---|---|---|
| "Now" | **One estate sim clock with speed control** (1× = real time, 3600× = a sim-hour per second; pause/resume). Published retained on MQTT; mirrored into Postgres so views and queries share it | A season can be watched in minutes or run in real time; every component agrees on "now". Fixes `hive_overview`'s negative ages |
| Truth source | **Replay a finished Parquet run**, paced by the clock, behind a small `TruthSource` interface | Reuses the tested Phase 2 engine unchanged; Phase 8 swaps in a live-stepping simulator (needed once actuators change the estate) without touching devices or ingestion |
| Devices | Python publishers (`paho-mqtt`), one process per device group (weather mast, field stations, process/water, corridor) | The architecture doc's pick: genuine IoT protocol, QoS, retained messages, last-will |
| Device realism | Truth + calibration bias, slow **drift**, measurement **noise**, sensor **resolution**, **dropouts** (radio/battery), **store-and-forward late bursts**, occasional **stuck values / spikes**, whole-station outages. Every injected fault is also written to a truth label file | The twin must cope with real-world data quality; Phase 6 anomaly detection gets ground-truth fault labels |
| Broker | **Mosquitto** (Homebrew, native — Docker stays deferred to Phase 9), localhost only, password auth | Tiny, free, standard |
| Ingestion | asyncio consumer (`aiomqtt`) + **Pydantic** validation; idempotent writes; late data placed by *measurement* time | Hand-written first, so later managed tools (Dataflow, Airflow, Dagster) don't feel magic |
| Time-series store | **TimescaleDB** extension on the same Postgres (moved 17 → 18 on 2026-11-25: the Homebrew TimescaleDB build requires 18): hypertable for readings, continuous aggregates (hourly/daily), compression | One engine to run and back up; real hypertables, retention and aggregates |
| Scheduled jobs | Deferred: TimescaleDB continuous aggregates cover Phase 3's rollups; Prefect arrives with retraining jobs (Phase 6) | One new tool at a time |
| Cloud | **GCP Pub/Sub bridge as the last milestone**, batched, behind a **monthly byte budget guard** well under the free tier (10 GiB; each publish request bills ≥ 1 KB) | Prove the local pipeline first; a fast replay must never be able to run up a bill |

## MQTT design

```
coffwe/v1/clock                                   retained  {sim_time, speed, running}
coffwe/v1/<site>/<station>/status                 retained  "online" | "offline" (last-will)
coffwe/v1/<site>/<station>/<measurement>          QoS 1     {sensor, measured_at, value, unit, seq, battery_v}
coffwe/v1/<site>/<station>/cmd/<actuator>         QoS 1     reserved for Phase 8 (dryer curtain, valves)
```

`measured_at` is the sensor's own timestamp (sim time); the broker/ingestion adds `received_at`. Late, duplicated or out-of-order messages are normal and handled by design.

## Milestones

**M1 — Sim clock + virtual sensor models (no network yet).** `SimClock` (speed, pause, deterministic stepping for tests); `TruthSource` over a Parquet run; per-measurement device models (bias, drift as a bounded random walk, noise, resolution, valid range) and per-station link models (Markov dropouts, store-and-forward bursts, outages); fault labels. Pydantic message schema. Property tests: noise and drift statistics, dropout rates, readings never leave the physical range except as labelled faults, same seed → same readings.

**M2 — Mosquitto + device publishers.** Install and configure Mosquitto (localhost, auth); topic tree above; one publisher process per device group driven by the clock; last-will "offline"; clock publisher. A CLI to start/stop the estate feed at a chosen speed. Integration tests against the local broker.

**M3 — TimescaleDB + ingestion.** Install TimescaleDB; migrations for a `sensor_readings` hypertable (measured_at, received_at, sensor_id, value, quality flags), station status events, continuous aggregates; the asyncio consumer with validation, de-duplication and range flags; station status → `sensor_stations.status`.

**M4 — The twin's "now" + end-to-end validation.** Clock mirrored into Postgres; `hive_overview` (and future views) use it; replay the seed-42 season end to end at speed; report completeness, latency, and how far observed values sit from the truth (`docs/phase3-report.md`).

**M5 — GCP Pub/Sub bridge (quota-guarded).** You enable the Pub/Sub API and run `gcloud auth` (I'll give the exact commands); service account with publish-only rights; bridge MQTT → Pub/Sub with batching and a persistent monthly byte budget; dry-run mode; a pull-subscriber check. Budget alert stays as the backstop.

## Done when

- `uv run python -m coffwe.feed run --speed 3600` streams a replayed season from virtual devices through Mosquitto into TimescaleDB, and the twin's clock follows it.
- Observed data shows realistic noise, drift, gaps and late bursts — with every injected fault labelled.
- End-to-end report written; tests pass; `PROGRESS.md` updated; committed and pushed.
