# Phase 2 — Simulation engine (plan)

_Agreed 2026-11-25. The simulator is "the real estate": it generates the ground truth that the Phase 3 virtual sensors will observe (imperfectly) and the digital twin will try to mirror._

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Engine | Custom Python + NumPy (SciPy for fitting/validation); SimPy only if scheduling gets hairy | Writing the agronomy and statistics by hand is the learning; a framework would hide it |
| Time step | **Hourly physics, daily biology** — weather, soil water, leaf wetness every hour; growth, pests, harvest, health once a day; milking twice a day | Rust risk needs leaf-wetness *hours*; Phase 3 sensors need sub-daily truth to sample |
| Weather | **Synthetic generator, validated against real data** — built from first principles, then checked once against ~10 years of real hourly weather for a Western Ghats coffee town | Learn stochastic modelling *and* prove the model is realistic |
| Ground truth storage | **Parquet files** under `data/sim/<run_id>/` (gitignored), partitioned by dataset | Columnar, fast with pandas, ideal for Phase 6 ML. **Events** (harvest batches, milkings, honey, inventory, health) still go into the Phase 1 Postgres tables |
| Build order | **Coffee chain first**: weather → soil water → coffee phenology → harvest & processing; then livestock; then pests & disease; then machines & inventory | The coffee chain is the estate's reason to exist and the longest causal chain to get right |
| Randomness | One seed per run; `numpy.random.SeedSequence` spawns an independent stream per subsystem | Same seed ⇒ identical estate. Adding a new subsystem never changes the others' random numbers |
| Testing | pytest + **Hypothesis** property tests ("soil water never exceeds saturation", "rain is never negative"), plus statistical validation reports | Simulations are best tested by their laws, not by fixed examples |
| Libraries | `numpy`, `pandas`, `pyarrow` (Parquet), `scipy`; dev: `hypothesis` | pandas is the industry default and pairs with pyarrow for Parquet |

## Time model

- Local time zone: `Asia/Kolkata` (IST, no DST). Timestamps stored as timezone-aware.
- **Physics spin-up from 1 Jan 2026** — weather, soil and coffee phenology run silently from New Year so the 2026 blossom showers (Feb–Mar) and cherry development exist when harvest starts. Truth for the spin-up is still written to Parquet.
- **Events from 25 Nov 2026** — the seeded Phase 1 data is the estate's recorded history up to 24 Nov; the simulator takes over the day after. No double-recording.
- A run = `(seed, start, end)`. Postgres holds one run's events at a time: `python -m coffwe.seed` resets, then the simulator writes. Run metadata is saved as JSON next to the Parquet files.

## Package layout

```
src/coffwe/sim/
  __main__.py   CLI: uv run python -m coffwe.sim run --days 365 --seed 42
  clock.py      simulated time, hourly ticks, daily/twice-daily hooks
  rng.py        seeded per-subsystem random streams
  climate.py    climate parameters (monthly rain, temperature normals, lapse rate)
  weather.py    stochastic weather generator (estate-wide + per-block adjustment)
  soil.py       per-block hourly water balance
  coffee.py     phenology, yield potential, ripening
  harvest.py    picking rounds, harvest batches/picks, processing
  livestock.py  cattle (lactation, breeding, health) and bees (colonies, flows, honey)
  pests.py      berry borer, leaf rust, traps, IPM interventions
  machines.py   wear, failures, inventory consumption
  truth.py      Parquet writer for continuous ground truth
  engine.py     the main loop that steps every subsystem in order
```

## Milestones

**M1 — Engine skeleton + weather** *(starts now)*
Clock, RNG streams, Parquet writer, CLI. Weather generator:
- Daily rain occurrence as a 2-state **Markov chain** (P(wet | dry), P(wet | wet)) varying by month.
- Wet-day amounts from a **gamma distribution**, monthly parameters; disaggregated into hours (afternoon convective showers outside the monsoon, longer spells in it).
- Temperature: annual cycle + diurnal sine + **AR(1)** day-to-day anomalies, cooler on wet days; −6.5 °C/km lapse rate across block elevations.
- Relative humidity, solar radiation, wind derived consistently (cloudy/wet ⇒ high RH, low radiation).
- Calibrated to Western Ghats coffee climate: ~2,000–2,800 mm/yr, Jun–Sep monsoon, Feb–Mar blossom showers, dry Dec–Jan.
- **Validation:** download ~10 years of real hourly reanalysis weather once (free archive API, a public town location, no personal data) and compare monthly totals, wet-day frequency, spell lengths and temperature ranges. Report saved under `docs/`.

**M2 — Soil water balance** — per block, hourly: canopy interception (shade %), infiltration vs runoff (slope), storage between wilting point and field capacity (soil type), evapotranspiration (radiation/temperature-based, reduced under shade), drainage. Leaf wetness hours. Irrigation input hook for the Phase 8 valves.

**M3 — Coffee phenology & yield** — blossom triggered by a ≥ ~25 mm shower after a dry spell; cherry development by thermal time (Arabica ~7–8 months, Robusta ~9–10); water stress reduces fruit set; daily ripe-cherry pool per block per variety; over-ripe/fallen losses if not picked.

**M4 — Harvest & processing** — daily picking plan (which pickers, which blocks), pick rates, `harvest_batches` + `harvest_picks`; washed (mostly Arabica) vs natural (mostly Robusta); status progression and dried output weights; jute bags and fuel drawn from inventory.

**M5 — Livestock** — milk via a lactation curve (Wood's model) per cow, twice-daily `milkings`; 21-day heat cycles, matings with Bhima, pregnancy checks, calvings that create new `cattle` rows; health incidents and due vaccinations. Bees: colony strength driven by nectar flows and weather, honey harvests, absconding/swarming risk.

**M6 — Pests & disease** — coffee berry borer population by degree-days (pheromone trap counts as the observable), leaf rust infection from leaf-wetness hours + temperature, IPM interventions drawing biocontrols from inventory.

**M7 — Machines & full-year run** — asset wear and failures (Weibull), remaining inventory flows (vermicompost production and application, feed), a full one-year run, property tests across the engine, and a validation report.

## Done when

- `uv run python -m coffwe.sim run --seed 42` (default 455 days, 1 Jan 2026 → 31 Mar 2027) produces a season of Parquet truth and Postgres events in well under a minute, identically every time for the same seed.
- Every subsystem has property tests for its physical laws, and weather has a written validation against real data.
- `PROGRESS.md` updated; committed and pushed.
