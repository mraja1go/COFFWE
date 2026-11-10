# COFFWE — Coffee Estate Digital Twin

A learning playground, built from scratch, around a fully imaginary coffee estate called **COFFWE**: first a simulator plays the role of "the real estate," then a digital twin ingests a live feed from it, then a roster of specialized AI "brain" agents observe the twin and make decisions — routine ones autonomously, bigger ones with human approval, strategic ones only by a human.

The goal is hands-on depth across ML/DL, statistics, data engineering, agentic AI, LLM-powered apps, digital twins, IoT/networking, cloud, and professional software practices (testing, CI/CD, deployment, MLOps) — not just a finished app.

See **[docs/estate-concept.md](docs/estate-concept.md)** for the full, finalized estate: 70 acres, 10 blocks, Arabica/Robusta mix, native shade trees, livestock, processing, wildlife corridor, and the organic pest-management system. That document is the ground truth every later phase builds against.

## Roadmap

| # | Phase | Status |
|---|---|---|
| 0 | Foundations & setup (this repo, GCP free-tier account) | in progress |
| 1 | Estate data model | next |
| 2 | Simulation engine | planned |
| 3 | Live feed / integration layer (MQTT → Pub/Sub) | planned |
| 4 | Digital twin core + dashboard | planned |
| 5 | AI brains v1 (rule-based) | planned |
| 6 | AI brains v2 (ML/stats) | planned |
| 7 | AI brains v3 (LLM/agentic) | planned |
| 8 | Closed loop + human-in-the-loop governance | planned |
| 9 | Productionization (tests, CI/CD, Terraform, GCP) | planned |
| 10 | Optimization & ongoing learning | planned |

## Stack

Python end-to-end, managed with [uv](https://docs.astral.sh/uv/). Google Cloud (always-free tier only) once Phase 3 needs a live feed. Linting/formatting via `ruff`, tests via `pytest`, git hooks via `pre-commit`.

## Getting started

```bash
uv sync              # install dependencies into .venv
uv run pytest        # run the test suite
uv run pre-commit install   # wire up git hooks (ruff + basic hygiene) once per clone
```

## Task autonomy

- **Built without asking each time:** code, tests, docs, synthetic data, routine refactors — anything inside an already-agreed phase.
- **Needs a decision from the estate owner:** estate-design facts, phase/architecture approvals, each AI brain's guardrail thresholds, ambiguous data labeling.
- **Only the estate owner can do:** own the GCP account and billing, run interactive cloud-auth logins, approve anything that could cost money, give final sign-off on a milestone.
