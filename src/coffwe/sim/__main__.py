"""Command line for the simulator.

uv run python -m coffwe.sim run [--seed 42] [--start 2026-01-01] [--days 455] [--db]
uv run python -m coffwe.sim validate-weather [--years 30]
"""

import argparse
import time
from datetime import date

from coffwe.sim import engine


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m coffwe.sim")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="run the simulator and write ground truth")
    run_p.add_argument("--seed", type=int, default=42)
    run_p.add_argument("--start", type=date.fromisoformat, default=engine.SPINUP_START)
    run_p.add_argument("--days", type=int, default=engine.DEFAULT_DAYS)
    run_p.add_argument("--db", action="store_true", help="reseed Postgres, then write events")
    run_p.add_argument("--no-ipm", action="store_true", help="counterfactual: no pest management")

    val_p = sub.add_parser("validate-weather", help="compare synthetic weather with real data")
    val_p.add_argument("--years", type=int, default=30, help="synthetic years to generate")

    args = parser.parse_args()
    if args.command == "run":
        t0 = time.perf_counter()
        store = engine.run(
            seed=args.seed, start=args.start, days=args.days, write_db=args.db, ipm=not args.no_ipm
        )
        info = store.read_info()
        print(f"Run {info.run_id}: {info.start} .. {info.end} in {time.perf_counter() - t0:.2f}s")
        print(f"Ground truth: {store.run_dir}/  ({', '.join(info.datasets)})")
        if store.db_counts:
            print("Postgres events:", ", ".join(f"{k} {v:,}" for k, v in store.db_counts.items()))
    elif args.command == "validate-weather":
        from coffwe.sim import validation

        report = validation.run(years=args.years)
        print(f"Report written to {report}")


if __name__ == "__main__":
    main()
