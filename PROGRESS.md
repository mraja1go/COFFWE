# Progress checkpoint — 2026-11-25

Read this first when resuming in a new terminal/session. For the big picture see `README.md`; for the full original roadmap see `docs/plan-phase-0.md`; for the estate itself see `docs/estate-concept.md`.

## Done

**Phase 0 — complete**
- Git repo initialized, pushed to `github.com/mraja1go/COFFWE` (`main` branch).
- Python project scaffolded with `uv` (src-layout `coffwe` package), `pytest` + `ruff` + `pre-commit` all wired and passing.
- Git commit signing fixed (GPG key generated for `Raja <35078189+mraja1go@users.noreply.github.com>`, no passphrase).
- `docs/estate-concept.md` — the finalized COFFWE estate (70 ac, 10 blocks, 15,050 Arabica / 15,050 Robusta, full species/livestock/processing/corridor/pest-system detail). This is the ground truth for everything downstream.
- GCP project `coffwe-estate-dt` created, billing linked, budget alert active ($1 SGD, fires at 50/90/100%). **No APIs enabled yet** — nothing running, nothing that can cost money.
- Two design artifacts saved locally under `docs/artifacts/` (originals were Claude-published artifacts, now preserved as plain HTML — open either directly in a browser):
  - `estate-concept-sketch.html` — the visual estate map/sketch.
  - `system-architecture.html` — the full 12-layer system architecture + tool choices for every phase.

**Phase 1 — in progress (estate data model)**
- Architecture decision made: **native PostgreSQL via Homebrew** for now (not Docker — Docker is deliberately deferred to Phase 9), **Alembic** for migrations from day one (not plain `create_all()`).
- Dependencies added to `pyproject.toml`/`uv.lock`: `sqlalchemy`, `alembic`, `psycopg[binary]`, `python-dotenv`.
- `.gitignore` fixed: `.env.*` was accidentally also matching `.env.example`; added `!.env.example` so the template file can actually be committed.

**Phase 1 pipeline — done (2026-11-23)**
- Postgres 17 (Homebrew) running; database `coffwe_dev` created.
- `.env` (gitignored, real URL) + `.env.example` (committed template).
- `src/coffwe/db/base.py` — `Base` with constraint naming convention, `engine`, `SessionLocal`.
- `src/coffwe/db/models.py` — placeholder `Block` (id, code, name, area_acres).
- Alembic initialized in top-level `migrations/`; `env.py` reads `DATABASE_URL` from `.env` and targets `Base.metadata`. `alembic.ini` keeps the dummy URL on purpose.
- First revision `c70373fc9f50` ("create blocks table") applied; `downgrade base` ↔ `upgrade head` round-trip verified; `alembic check` clean.
- `alembic.ini` post-write hooks run `ruff check --fix` + `ruff format` on every new revision automatically.

**Phase 1 estate core — done (2026-11-24)**
- `src/coffwe/estate_data.py` — the estate concept as pure Python data (blocks, 25 species, shade-tree weights, tag generator). No DB dependency; `tests/test_estate_data.py` checks every total against the concept doc.
- `src/coffwe/db/models.py` — real `Block` (elevation range, slope, soil, shade %, buffer acres, notes), `Species` (code, names, category), `Plant` (tag, block, species, sequence, status). Enums are VARCHAR + CHECK, not native Postgres ENUMs (easier to alter later).
- Revision `dfcbd96746b4` — CHECK constraints on the existing `blocks` table were **added by hand** (autogenerate can't detect them). Round-trip from `base` verified.
- `uv run python -m coffwe.seed` — truncates and reloads 10 blocks / 25 species / 44,950 plants in ~1.5 s; idempotent.
- Tag format changed to `B3-RB-00147` (estate-concept v1.1).
- Caveat: `downgrade -1` then `upgrade head` fails while blocks has rows (new NOT NULL columns). Recover with `alembic downgrade base && alembic upgrade head && python -m coffwe.seed`.

**Phase 1 full schema — done (2026-11-24). Phase 1 complete.**
- 13 tables across 9 migrations (one per table group, each reviewed): `sites` → `workers` → `assets` → `sensor_stations` + `sensors` → `livestock_groups` → `inventory_items` + `inventory_movements` → `harvest_batches` + `harvest_picks`.
- Decisions: a `sites` table is the single "where" for everything; every person is a `workers` row (15 permanent + 45 seasonal); inventory is an append-only ledger (stock = SUM of movements; sign enforced per reason); sensors are stations → per-measurement sensors with valid physical ranges; harvest batch = one crop × block × day, with per-picker `harvest_picks`.
- The lab is the **Estate Lab** — all-purpose (fertilizer prep, soil testing, research), not a soil lab.
- Seed now loads all baseline data (23 sites, 60 workers, 41 assets, 18 stations / 65 sensors, 3 livestock groups, 23 inventory items + opening balances) in ~1.8 s. Harvest tables are seeded empty — events are the simulator's job.
- Verified: constraints reject 7 kinds of bad rows; a sample harvest + compost-usage scenario queries correctly; all 9 migrations round-trip `base` ↔ `head`; 27 tests pass.
- Concept numbers chosen for these tables are recorded in `docs/estate-concept.md` v1.2 ("Resources & sites").
- Known app-level rule (not a DB constraint): harvest batches should only reference crop species, not shade trees.

**Phase 1 addition — livestock detail (2026-11-24)**
- 3 more migrations (12 total): `ab8513646969` cattle, `8f7a93e1bf21` bees, `d4660c7330ef` livestock-group counts. 22 tables + 1 view.
- **Cattle:** `cattle` (individuals; `dam_id`/`sire_id` self-reference = family tree), `cattle_health_events` (checkups w/ weight/BCS/temp, vaccinations w/ next-due, deworming, illness, treatment), `cattle_breeding_events` (heat, mating w/ sire, pregnancy check, calving w/ calf, dry-off), `milkings` (per cow per morning/evening). Mixed herd (Malnad Gidda + Jersey cross), estate bull Bhima (C01).
- **Bees:** `hives` (Newton box: fence position, frames, supers), `bee_colonies` (occupancy, absconding/collapse), `queens` (reigns), `nectar_flows` (seasons), `honey_harvests`. Native *Apis cerana indica*. `hive_overview` **view** gives hive age, occupancy, queen age, harvest-cycle length.
- `livestock_groups.head_count` now only for poultry; cattle/bee counts derive from individual rows (data migration both ways — the downgrade rebuilds counts from `cattle`/`hives`).
- Lessons this round: autogenerate put everything in one revision → split by hand; data migrations need ordering care (the count change sits *on top* so its downgrade can still see both tables); views are hand-written (`op.execute`); partial unique indexes enforce "one living colony per box" / "one reigning queen per colony".
- Verified: 11 new constraint probes rejected; family-tree recursive query, herd status, hive durations, per-flow honey all query correctly; all 12 migrations round-trip; 39 tests pass.
- Caveat: `hive_overview` uses `CURRENT_DATE` (real clock); the simulator may need an as-of date.
- Detail recorded in `docs/estate-concept.md` v1.3 ("Cattle herd", "Apiary").

**Phase 2 — simulation engine: in progress.** Plan: `docs/plan-phase-2.md` (decisions: hourly physics / daily biology; synthetic weather validated against real; Parquet ground truth in `data/sim/<run_id>/`, events in Postgres; coffee chain first; physics spin-up from 2026-01-01, events from 2026-11-25).

**M1 — engine skeleton + weather: done (2026-11-25)**
- `src/coffwe/sim/`: `climate.py` (all parameters), `weather.py` (Markov-chain rain occurrence, gamma amounts, yearly monsoon strength, AR(1) temperature, cosine diurnal curve, solar geometry, consistent RH/wind/pressure), `rng.py` (per-subsystem seeded streams), `truth.py` (Parquet store, partitioned by year/month), `engine.py`, `validation.py`, CLI `__main__.py`.
- `uv run python -m coffwe.sim run` — a year of hourly weather in ~0.1 s; `uv run python -m coffwe.sim validate-weather` — downloads 10 years of real reanalysis (cached in `data/reference/`) and writes `docs/weather-validation.md`.
- Calibration v2 after validation: mean-preserving interpolation (plain interpolation shaved the monsoon peak ~5%), centred wet-day humidity, monthly clearness, more transition-month persistence, yearly monsoon-strength factor (fixed too-small year-to-year variability).
- Hypothesis found a real bug: the monsoon factor could shrink a wet day below 1 mm; now it scales only the excess.
- 52 tests pass (property tests for physical limits, rain conservation, determinism; 30-year climate statistics).

**M2 — soil water balance + block microclimate: done (2026-11-25)**
- `src/coffwe/sim/soil.py`: hourly bucket model for all 10 blocks at once — canopy interception (shade holds more), infiltration vs runoff (soil Ksat, halved on steep slopes), Priestley–Taylor ET with FAO-56 water-stress coefficient, drainage above field capacity. Soil properties per soil type in `SOIL_TYPES`.
- Block microclimate: lapse-rate + shade cooling air temperature, RH recomputed from constant vapour pressure, lagged soil temperature, leaf wetness (rain-wet canopy or dew at high RH). Column names match the field-station sensor measurements for Phase 3.
- Engine now has a real hourly loop; writes `block_hourly` (87,600 rows/yr) beside `weather_hourly`. A year runs in ~0.6 s. Irrigation is a `step(..., irrigation=)` hook, unused until the blossom/dry-season irrigation logic (M3) and Phase 8 valves.
- Year 1 (seed 42): ET0 1,551 mm; shaded blocks ET ~950–980 mm; thin rocky B5/B9 lose ~43% of rain to runoff and hit wilting point by March; open-sun B10 ~20% fewer leaf-wet hours than shaded B8.
- Tests: exact water conservation and physical bounds under random weather (Hypothesis); shade/slope/lapse effects; analytic cloudburst steady state. 63 tests pass.

**M3 — coffee phenology & yield: done (2026-11-25)**
- `src/coffwe/sim/coffee.py`: per (block, variety) unit, daily — bud dormancy (30 dry days since 1 Dec), blossom showers (AR ≥20 mm, RB ≥25 mm over 2 days, window 20 Jan–15 May) opening 75% of remaining buds as a *cohort* 9 days later, fruit set from water stress in the 21 days after opening (floor 0.65) and Robusta pollination rain penalty, fill from stress on days 60–150, ripening by degree-days >10 °C (AR 3,130, RB 3,460, spread 5%), ripe pool 12 days → over-ripe 10 days → fallen.
- `src/coffwe/sim/management.py`: `BaselineManagement` = the estate manager until the AI brains take over — blossom irrigation (Robusta blocks 20 Feb, Arabica 10 Mar) if showers failed, backing irrigation 18 days later if no ≥10 mm rain. Uses the soil model's irrigation hook.
- Engine is now a day loop (management → 24 soil hours → daily coffee). Default run is 1 Jan 2026 → 31 Mar 2027 (455 days, ~2 s) to cover the full 2026-27 harvest. New datasets: `coffee_daily`, `coffee_blossoms`, `irrigation_log`.
- Calibrated to the Karnataka calendar: Arabica peaks mid-Dec (high B8/B9 ~20 Jan), Robusta ~10–30 Jan, tails into early Mar; seed 42 gives ~83 t fresh cherry (~2.3–3.6 kg/plant; open-sun B10 highest).
- Nothing picks yet (M4), so every ripe cherry eventually falls in this milestone.
- 78 tests pass (blossom rules, bud-share bounds via Hypothesis, crop conservation, fall timing, warmth/stress/pollination effects, manager behaviour, end-to-end harvest calendar).

**M4 — harvest & processing → Postgres: done (2026-11-25)**
- `src/coffwe/sim/harvest.py`: seasonal pickers (1 Nov–28 Feb, ~90% attendance, no Sundays/heavy rain) then the 5 permanent field workers for the March tail; pick rate by variety (AR 45 / RB 60 kg/day), slope, ripe load per plant and personal skill; `CoffeeModel.pick` strips ripe + over-ripe proportionally. One batch per (block, variety, day); receiving weight 0.5–2% under field weights. Washed (AR): processing → 2 days → drying (8 good-day credits) → stored, outturn 20%; natural (RB): drying (14 credits), outturn 42%. Rain/cloud slow drying. Dispatch on the 1st/15th; bags issued per dispatch lot; daily pulper diesel + lorry trips.
- `management.py`: `plan_picking` (crews to the units with the most ripe/over-ripe, largest-remainder when short-handed) and `reorder` (top up below reorder level).
- `src/coffwe/sim/db.py` + `run --db`: reseeds Postgres, then writes `harvest_batches`, `harvest_picks`, `inventory_movements` for days ≥ 25 Nov 2026 (code → id translation in one place).
- Seed 42: ~80 of 83 t picked (Jan peak ~35 kg/picker-day), ~950 batches / 3,250 picks / 121 ledger lines in Postgres; ~412 jute bags, 1 diesel reorder.
- **Test database `coffwe_test`** (local, migrated to head) — the Postgres integration test writes there, never to `coffwe_dev`. Re-migrate it after new migrations: `DATABASE_URL=postgresql+psycopg://raja@localhost:5432/coffwe_test uv run alembic upgrade head`.
- 92 tests pass (crop accounting, picking rules, who-picks-when, batch weights, processing times/outturns, status/date consistency, ledger signs & non-negative stock, bag counts, determinism, Postgres write).

**M5 — livestock: done (2026-11-25)**
- `src/coffwe/sim/livestock.py`: starts from the seeded history on 25 Nov (no spin-up) — lactating Kaveri/Tunga/Gowri, pregnant Ganga/Lakshmi, cycling heifers, vaccine due dates, 23 live colonies, queens, empty HV-22.
- Cattle: Wood's lactation curve (MG peak ~3 L/d, JX ~11 L/d at day ~50–57), twice-daily milkings (55/45), fat by breed and stage; 21-day heats, 80% detection, Bhima serves eligible cows (60 d postpartum / heifers ≥450 d), re-served on return to heat, pregnancy check at 60 d, dry-off 60 d before calving or at end of lactation, calving creates a new calf (dam + sire linked), rare abortion; vaccinations on due dates (calves join at 120 d), brucellosis for female calves, quarterly deworming, monthly weigh-ins (calves grow), mastitis with milk withdrawal, occasional other illness.
- Bees: colony strength (frames) and honey stores driven by nectar flows, rain (no foraging) and **the simulated coffee bloom** (coffee flow boosted while flowers are open); absconding (likelier when weak), sacbrood collapse (Jan–Apr), queen supersedure; keeper harvests on Saturdays when stores ≥1.8 kg (≥30 days apart) and re-stocks long-empty boxes by splitting a ≥5-frame colony during a flow.
- `db.write_livestock`: inserts calves, health/breeding events, milkings, honey harvests, new colonies/queens; **updates** seeded colonies/queens that ended (no duplicates); keeps `hives.super_count` in step.
- Seed 42 (25 Nov → 31 Mar): 1 calf (Kapila), 952 milkings, 89 health + 23 breeding events, 23 honey harvests (~50 kg in the coffee flow), 2 colonies lost and 3 boxes re-stocked.
- Tests share one coffwe_test run via `tests/conftest.py`. 142 tests pass (history → state, Wood peak, milking rules, breeding rules, calving records, dry-off, conception per service, vaccination ages, checkups, long-run mastitis rate, apiary invariants, Postgres write).
- **Open issue for Phase 3/4:** `hive_overview` uses `CURRENT_DATE` (real clock) but Postgres now holds simulated events through Mar 2027, so durations for future-dated rows come out negative. Needs a decision on the twin's notion of "now" (sim clock vs wall clock).

**M6 — pests & disease + IPM: done (2026-11-25)**
- `src/coffwe/sim/pests.py`: **berry borer** per (block, variety) — infestation-pressure index (bored share = 1 − e^−P), one generation per 311 degree-days >14.9 °C, breeding only in host berries (≥120-day berries, ripe/over-ripe, fallen-berry reservoir that carries it through the dry season), losses to picking, heavy rain, starvation, trapping, Beauveria; weekly Poisson **pheromone trap counts** (flight peaks after harvest). **Leaf rust** — infection from temperature-weighted leaf-wet hours (M2 hourly), temperature-dependent latent period, sporulation, leaf fall and regrowth, Bordeaux protection washed off by rain; Robusta ~resistant.
- Coupled to coffee: bored berries lose 35% of their weight before picking; rust defoliation reduces berry fill this year and next year's bud set (1 Dec carry-over).
- `management.ipm`: monthly lures for 140 traps; Saturday scouting of 100 berries/leaves per block (sampling noise); Beauveria at ≥5% sampled bored (≥21 days apart); Bordeaux only above a rust threshold + 14-day infection-risk index (≥30 days apart); never on a wet day; gleaning once a block's harvest is over; planned jobs buy stock ahead.
- `run --no-ipm` = counterfactual estate (no traps, sprays or gleaning), saved as a separate `-noipm` run. Seed 42: borer loss 0.8 t (IPM) vs 5.6 t (none); unmanaged infestation 25–36% by Jan–Mar vs ≤5% managed; Arabica rust peaks in Oct at 29% (IPM) vs 37% (none).
- Fixed a latent truth-store bug: reads lost same-day row order (unstable sort), which made the inventory ledger look negative.
- 157 tests pass (rust/borer laws, physical bounds via Hypothesis, Bordeaux/Beauveria/gleaning effects, trap response, IPM rules and inventory, managed vs unmanaged estate).

**M7 — machines, organic loop, engine-wide validation: done (2026-11-25). Phase 2 complete.**
- New table `asset_events` (migration `3f01a1982438`): the maintenance log — failure / repair / maintenance with wear since last service. Both `coffwe_dev` and `coffwe_test` migrated.
- `src/coffwe/sim/machines.py`: every asset wears in its own unit (h, cycles, km, days); failures from a conditional Weibull (wear-out, k > 1), calibrated so each machine's yearly use gives a realistic breakdown chance (e.g. pulper ~once a season); repair times and spares (pulper screen, drip emitters); pre-season service on 15 Oct; rural power cuts run the generator. **Breakdowns have consequences**: no pulper → washed cherry waits; no polyhouse film/curtain → rain stops drying; failed pump/valve → missed irrigation; no pickup → dispatch waits.
- `src/coffwe/sim/organics.py`: dung (5% bodyweight/day) → biogas slurry (fertigates B6/B7) + vermicompost pits with coffee pulp → vermicompost 60 days later, spread on each block once its harvest is over; weekly cattle/chicken feed; petrol for weeding (Dec–Jan) and shade lopping (Feb–Mar); diesel for the generator.
- **Weather now depends only on (seed, calendar day)**: generated in calendar-year chunks with state carried across New Year. Previously the run length changed every day's weather (found when extending the run) — a twin must never rewrite past weather.
- Fixed: "harvest over" ignored the *next* season's young berries (so 2027 gleaning/feeding never happened); ledger stock now sums exactly what's recorded (rounding drift).
- Default run is now 1 Jan 2026 → 30 Apr 2027 (485 days, ~2.5 s) to include post-harvest work.
- `uv run python -m coffwe.sim report` → `docs/phase2-report.md` (weather, water balance, crop, harvest, livestock, pests with/without IPM, breakdowns, inventory flows).
- 195 tests pass, incl. engine-wide laws across 3 weather years (water, cherry and stock conservation; batch life-cycle order; failure→repair pairing; seasonal calendars; determinism), a KS test that simulated lifetimes are Weibull, and the Postgres write of the maintenance log.
- Lesson: a few test bounds had been tuned to one seed's weather year; the new weather exposed it, and the multi-seed engine tests now guard against that.

**Phase 3 — live feed: in progress.** Plan: `docs/plan-phase-3.md` (decisions: one sim clock with speed control as the estate's "now"; replay Parquet truth behind a `TruthSource` interface, live simulator in Phase 8; GCP Pub/Sub bridge last, quota-guarded).

**Environment change (2026-11-25):** Postgres moved **17 → 18** because the Homebrew TimescaleDB build (`timescale/tap/timescaledb` 2.32.1, formula trusted individually) requires 18. `postgresql@18` runs on port 5432 with `shared_preload_libraries = 'timescaledb'` (original conf saved as `postgresql.conf.before-timescaledb`); `postgresql@17` is stopped and unlinked but still installed as a fallback. Both databases were recreated and rebuilt from the repo (migrations + seed + `sim run --db`) with identical results. Mosquitto 2.1.4 installed (not yet configured/running).

**Phase 3 M1 — sim clock + virtual sensors: done (2026-11-25)**
- New ground truth in the simulator: `sim/hydrology.py` (quick-flow + baseflow linear reservoirs → Stream 1/2 flow; check-dam reservoir water balance with irrigation withdrawals, seepage, spill and recorded **shortfall** when irrigation outruns storage) and `sim/wildlife.py` (nocturnal background passes + seasonal elephant visits along the corridor). Datasets `water_hourly`, `wildlife_hourly`, `elephant_visits`.
- `src/coffwe/feed/`: `clock.py` (SimClock with speed/pause, ManualClock), `truth.py` (`TruthSource` + `ParquetTruthSource`: every one of the 65 sensors at 15-min reporting — interpolated state variables, amounts split to preserve totals), `devices.py` (per-sensor bias/gain, bounded drift, noise, resolution, stuck/spike faults, tipping-bucket rain gauge with clogging, rain detector false alarms/misses, motion misses/false triggers; per-station solar battery with brownouts and a Gilbert–Elliott radio link with store-and-forward late bursts, losses and duplicates), `schema.py` (Pydantic wire format + topic scheme), `observed.py` (writes `observed_messages` + `observed_labels` next to the truth).
- Seed 42: 3.03M readings in ~22 s; 98.6% on time, 1.3% late (bursts up to ~12 h), 0.08% lost; ~6,300 labelled faults. No brownouts in this monsoon (batteries sag to 3.74 V in July) — tests force one with a dull spell.
- Design note for Phase 8: devices compute the observed stream offline per sensor (vectorized); live mode will need an incremental version behind the same interfaces.
- 216 tests pass.

## Not done yet — the exact next steps

**Phase 3 M2 — Mosquitto + device publishers: done (2026-11-25)**
- `infra/mosquitto/acl` (committed): least-privilege roles — `devices` publish readings/status and read the clock, `ingest` read-only, `admin` runs the clock. `feed/broker.py`: `broker setup` writes random passwords into `.env` and config/password file into `data/mosquitto/` (localhost only, no anonymous); `broker start` runs Mosquitto in the foreground.
- `feed/publisher.py`: one MQTT client per station (own client id, retained "online", **last-will "offline"**), four device-group processes (weather, field, process+water, corridor) replaying `observed_messages` paced by the clock; brownouts → offline/online status; catch-up when behind.
- `feed/runner.py`: the orchestrator owns the clock — retained `coffwe/v1/clock` (sim time + wall anchor, so every process computes the same "now"), live `ClockCommand`s on `coffwe/v1/clock/set`, **ready barrier** (clock starts paused until all groups are loaded and connected — without it, startup at 3600× was hours of backlog).
- CLI: `python -m coffwe.feed broker setup|start`, `observe`, `run --speed --start --hours`, `clock --speed|--pause|--resume`.
- Measured: 6 sim-hours at 3600× → 1,566/1,566 readings delivered, 0 late; 2 sim-days at 86,400× → 12,543/12,543 delivered but the field group (≈3k msg/s per process) falls behind — complete but late above ~40,000×.
- Fixed a latent bug: `TruthStore.write` into an existing dataset *appended* files (running `feed observe` twice would have doubled the feed); writes now replace.
- 226 tests pass (incl. ACL, delivery completeness, last-will on a crashed station, live speed change, brownout status) — each test module runs its own throwaway broker on a free port.

## Not done yet — the exact next steps

**Phase 3 M3 — TimescaleDB + ingestion** (see plan): `CREATE EXTENSION timescaledb` via migration; `sensor_readings` hypertable (measured_at, received_at, sensor_id, value, quality flags), station status events, continuous aggregates; asyncio (`aiomqtt`) consumer with Pydantic validation, de-duplication, range flags, late-data handling; station status → `sensor_stations.status`. Remember to migrate `coffwe_test` too.

## Open decisions already made (don't re-ask)

- Beginner coder, intense pace, wants full/detailed learning — explain the *why*, not just the *what*.
- GCP always-free tier only, no spending, ever.
- Python end-to-end.
- Docker deferred to Phase 9 (containerize once there's something real to containerize).
- Postgres does triple duty: relational now → TimescaleDB (Phase 3) → pgvector (Phase 7), same engine throughout.
- Classical ML before deep learning (Phase 6), so it's possible to tell whether DL actually helped.
- Prompt for input liberally — the user explicitly asked to be consulted on real decisions and small distractions, not just big ones.
