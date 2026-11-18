# Coffee Estate Digital Twin — Learning Ecosystem

_Saved copy of the original Phase 0 plan (written in Claude Code plan mode, approved 2026-11-03). Kept verbatim as a historical record — see `PROGRESS.md` at the repo root for current status and `docs/estate-concept.md` for the estate itself._

## Context

You want to build, from an empty folder, a fully imaginary coffee estate that teaches you — hands-on — ML/DL, statistics, data science, agentic AI, LLM-powered apps, digital twins, IoT/networking, data engineering, cloud, and professional software/project practices (testing, CI/CD, deployment, optimization, evaluation, maintenance). There is no real data: you will build a **simulator** that plays the role of "the real estate" (ground truth generator), then a **digital twin** that ingests a live feed from it, then a constellation of **AI "brain" agents** that observe the twin and make decisions — small/routine ones autonomously, bigger ones with your approval, strategic ones only by you.

Decisions you've already made that shape this plan:
- You're a **beginner coder**, want an **intense pace with complete, detailed learning** (not shortcuts).
- Build on **Google Cloud (GCP)** from the start, but **strictly within the always-free tier** — no spending.
- **Python end-to-end.**

This plan lays out the full roadmap (so you can see where this goes), the AI-brain roster, the autonomy split you asked for, and a concrete, detailed Phase 0 to start on immediately.

---

## The full roadmap (reference — we execute one phase at a time)

Each phase lists what gets built and the learning domains it exercises. Phases are sequential but each is a real learning unit — we won't rush past the understanding to just get code written.

| # | Phase | Builds | Learning focus |
|---|-------|--------|-----------------|
| 0 | Foundations & setup | Estate concept doc, git repo, Python project scaffold, GCP free account + billing safety net | git, Python tooling, cloud account basics, IAM |
| 1 | Estate data model | Pydantic/DB schema for blocks, trees, workers, machines, sensors, inventory, harvest batches | domain modeling, SQL, time-series DB design |
| 2 | Simulation engine | `estate-simulator`: weather, soil, plant growth, pest outbreaks, worker activity, machine wear, yield — all as a standalone "ground truth" service | discrete-event simulation, stochastic processes, statistics |
| 3 | Live feed / integration layer | MQTT broker (authentic IoT protocol) bridged to GCP Pub/Sub; ingestion service | event-driven architecture, networking, IoT protocols, data pipelines |
| 4 | Digital twin core + dashboard | Twin service mirroring simulator state, FastAPI, web dashboard with live charts/map | APIs, state sync patterns, data viz, frontend basics |
| 5 | AI brains v1 (rule-based) | First agents as explicit rule engines + orchestrator skeleton + human-notification inbox | agent design, control loops, human-in-the-loop UX |
| 6 | AI brains v2 (ML/stats) | Forecasting, anomaly detection, optimization models trained on simulator history | regression/classification, time-series forecasting, model evaluation |
| 7 | AI brains v3 (LLM/agentic) | LLM reasoning layer per brain + Supervisor meta-agent for coordination/escalation | agentic AI systems, LLM tool-use, multi-agent orchestration |
| 8 | Closed loop + governance | Agent decisions actuate back into the simulator; verification; human approval queue for critical calls | control systems, workflow/audit logging |
| 9 | Productionization | Tests, CI/CD (GitHub Actions), Docker, Terraform to GCP free tier, monitoring, cost alerts | testing, DevOps, IaC, observability |
| 10 | Optimization & ongoing learning | Scenario simulations (drought/pest/price shock), drift detection, retraining pipelines, security review | MLOps, optimization, evaluation, maintenance |

We will treat each phase as its own mini project with its own plan when we get there — this roadmap is the map, not the turn-by-turn directions.

---

## The AI brains (your list + suggested additions)

Your original six — **Cultivation & Harvest**, **Fertilizer**, **Soil/Environment/Climate**, **Pest Control**, **Worker Management**, **Biodiversity & Estate Demography** — plus **Logistics**, are all kept. Suggested additions to round out a believable estate:

- **Irrigation & Water Management** — can start merged inside Soil/Climate, split out once water scheduling gets complex (water scarcity is a real coffee-estate pressure point).
- **Post-Harvest Processing & Quality** — pulping, fermentation, drying, grading/cupping score. This is where "coffee quality" actually gets decided, distinct from cultivation.
- **Equipment & Asset Maintenance** — predictive maintenance on pulpers, dryers, vehicles.
- **Finance & Cost** — budget tracking, per-block ROI, cost of each agent's recommendations.
- **Market Intelligence & Sales** — commodity price tracking, sell/hold timing.
- **Sustainability & Certification** — organic/carbon/compliance tracking.
- **Safety & Compliance** — worker safety incidents, regulatory checks.
- **Supervisor / Orchestrator** — not a domain brain but a required coordination layer: routes tasks, resolves conflicts between brains (e.g., Fertilizer wants more nitrogen, Biodiversity flags runoff risk into a buffer zone), and manages the human-escalation queue.

We won't build all of these at once — Phase 5 starts with 2–3 brains (recommend **Soil/Climate** and **Cultivation & Harvest** first, since they drive the core growth/yield loop everything else reacts to), and the roster grows through Phases 5–7.

---

## Task autonomy (as you asked)

**I can do without any intervention from you** (once a phase's direction is agreed):
- Write and scaffold all code — simulator, data models, agents, APIs, dashboards, tests, Dockerfiles, CI configs, Terraform (I'll always explain what it does, since you're learning).
- Generate synthetic data, run local simulations, run test suites, debug failures.
- Within an approved phase, implement an agent's routine/non-critical decision logic once you've approved its *policy thresholds* (see below).
- Documentation, architecture diagrams, refactors, routine maintenance.

**Tasks that need your input** (decisions, not keystrokes):
- One-time estate design choices: location/climate analog, acreage, crop mix, number of blocks/workers — needed before Phase 0 finishes.
- Approving the plan/architecture at each phase boundary before I build it.
- Setting each agent's **guardrails/policy thresholds** — e.g. "irrigate automatically if soil moisture < X, but any fertilizer dose above Y requires your sign-off." This *is* the human-in-the-loop governance layer you described — it lives in decisions you make, not code I write unsupervised.
- Reviewing/accepting ambiguous labeling for supervised-learning data the simulator produces.
- Picking which brain/phase to tackle next when there's a real tradeoff.

**Only you can do:**
- Create and own the GCP account — identity/phone/card verification is tied to your identity; I cannot do this for you. (GCP requires a card on file even for always-free resources — I'll walk you through setting a $0/$1 billing budget alert and disabling auto-upgrade as a safety net, but *you* enter the payment details.)
- Run any interactive `gcloud auth login` / browser-based consent — I'll hand you the exact command to run yourself (via `!command` in this session).
- Approve anything that could ever incur real cloud cost.
- Final accept/reject on each milestone — it's your learning project.

---

## Phase 0 — Foundations & Setup (do this now)

**Goal:** a real git repo, a working Python environment, the estate concept defined on paper, and a GCP account configured so it's physically incapable of silently costing you money.

### Step 1 — Define the imaginary estate (needs your input, ~15 min conversation)
Before any code, we fix the "facts" the simulator will be grounded in:
- Name, fictional location/climate analog (e.g., Western Ghats–like monsoon climate), elevation band.
- Size in acres/hectares, number of blocks/plots.
- Arabica vs Robusta mix, shade-grown or not.
- Rough workforce size, whether there's on-site wet/dry processing.
- Any other estate flavor you want captured.

### Step 2 — Local project scaffold (I do this)
- `git init`, initial commit structure.
- Python env via `uv` (modern, fast, beginner-friendly — I'll explain why vs. pip/venv/poetry).
- `src/`-layout package, `pytest`, `ruff`/`black` for linting/formatting, pre-commit hooks.
- `README.md` capturing the estate facts from Step 1 and this roadmap.
- `.gitignore`, `pyproject.toml`.

### Step 3 — GCP account setup (you do the account/billing parts; I guide every click)
- Create GCP account, create a dedicated project (e.g. `coffee-estate-dt`).
- Set a billing budget alert at $0 and $1 thresholds.
- Enable only the free-tier services we'll actually touch in Phase 1–3 (defer enabling anything until needed).
- Install and auth the `gcloud` CLI locally (you run the login command yourself).

### Step 4 — GitHub remote (you create it, I push)
- You create an empty GitHub repo (your account) and give me the URL; I wire up the remote and push.

**Verification for Phase 0:**
- `git log` shows the initial commit; `pytest` runs (even with zero tests, shows green).
- `gcloud config list` shows the right project with no billing surprises (budget alert confirmed active).
- README accurately reflects the estate you described.

Once Phase 0 is done, we move to Phase 1 (the data model) with its own short planning pass.
