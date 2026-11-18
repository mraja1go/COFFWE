# Progress checkpoint — 2026-11-18

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

## Not done yet — the exact next steps

Picking up Phase 1 means walking through the Alembic setup instructions already given (not yet executed):

1. `brew install postgresql@17` + `brew services start postgresql@17`
2. `createdb coffwe_dev`
3. Create `.env` (real connection string, gitignored) and `.env.example` (template, committed) — format: `DATABASE_URL=postgresql+psycopg://<your-username>@localhost:5432/coffwe_dev`
4. `src/coffwe/db/__init__.py` + `src/coffwe/db/base.py` (SQLAlchemy `Base`, engine, session)
5. `src/coffwe/db/models.py` — placeholder `Block` model just to prove the pipeline (will be replaced by the real schema next)
6. `uv run alembic init migrations` (top-level `migrations/` folder, not nested in `src/`)
7. Edit `migrations/env.py`: inject `DATABASE_URL` from `.env`, point `target_metadata` at `Base.metadata` (remember to import `models.py` so tables actually register!)
8. `uv run alembic revision --autogenerate -m "create blocks table"`, then `uv run alembic upgrade head`
9. Verify with `psql coffwe_dev -c '\dt'`, prove `alembic downgrade base` / `upgrade head` round-trips cleanly

**After that:** design the real schema together — `Block` (properly, from the estate-concept table), `Plant` (all 44,950 tagged individuals), `Worker`, `Asset`, `SensorStation`, `LivestockGroup`, `InventoryItem`, `HarvestBatch` — then regenerate the migration for real.

## Open decisions already made (don't re-ask)

- Beginner coder, intense pace, wants full/detailed learning — explain the *why*, not just the *what*.
- GCP always-free tier only, no spending, ever.
- Python end-to-end.
- Docker deferred to Phase 9 (containerize once there's something real to containerize).
- Postgres does triple duty: relational now → TimescaleDB (Phase 3) → pgvector (Phase 7), same engine throughout.
- Classical ML before deep learning (Phase 6), so it's possible to tell whether DL actually helped.
- Prompt for input liberally — the user explicitly asked to be consulted on real decisions and small distractions, not just big ones.
