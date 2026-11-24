# Progress checkpoint — 2026-11-24

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

## Not done yet — the exact next steps

**Phase 2 — simulation engine**, starting with its own short planning pass: what the simulator generates first (weather → soil moisture → plant/pest state → worker activity → harvest batches + inventory movements), time-step size, and how it writes into the Phase 1 tables.

## Open decisions already made (don't re-ask)

- Beginner coder, intense pace, wants full/detailed learning — explain the *why*, not just the *what*.
- GCP always-free tier only, no spending, ever.
- Python end-to-end.
- Docker deferred to Phase 9 (containerize once there's something real to containerize).
- Postgres does triple duty: relational now → TimescaleDB (Phase 3) → pgvector (Phase 7), same engine throughout.
- Classical ML before deep learning (Phase 6), so it's possible to tell whether DL actually helped.
- Prompt for input liberally — the user explicitly asked to be consulted on real decisions and small distractions, not just big ones.
