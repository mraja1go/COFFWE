"""Command line for the live feed.

uv run python -m coffwe.feed broker setup          # passwords (.env) + broker config
uv run python -m coffwe.feed broker start          # run Mosquitto (foreground)
uv run python -m coffwe.feed observe [--run DIR]   # generate the observed sensor feed
uv run python -m coffwe.feed run [--speed 3600] [--start ISO] [--hours N]
uv run python -m coffwe.feed clock (--speed X | --pause | --resume)
"""

import argparse
import time
from pathlib import Path

import pandas as pd

from coffwe.feed import broker


def latest_run() -> Path:
    runs = sorted(
        p
        for p in Path("data/sim").iterdir()
        if (p / "run.json").exists() and not p.name.endswith("-noipm")
    )
    if not runs:
        raise SystemExit("no simulator run found -- uv run python -m coffwe.sim run")
    return runs[-1]


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m coffwe.feed")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("broker", help="manage the local Mosquitto broker")
    b.add_argument("action", choices=["setup", "start"])
    o = sub.add_parser("observe", help="generate the observed sensor feed for a run")
    o.add_argument("--run", type=Path)
    r = sub.add_parser("run", help="replay a run's sensor feed through the broker")
    r.add_argument("--run", type=Path)
    r.add_argument("--speed", type=float, default=3600.0)
    r.add_argument("--start", help="sim start (default: the run's events start)")
    r.add_argument("--hours", type=float, default=24.0, help="sim hours to replay")
    c = sub.add_parser("clock", help="change the running feed's clock")
    g = c.add_mutually_exclusive_group(required=True)
    g.add_argument("--speed", type=float)
    g.add_argument("--pause", action="store_true")
    g.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    if args.command == "broker":
        if args.action == "setup":
            s = broker.setup()
            print(
                f"Broker config in {broker.DATA_DIR}; users {', '.join(s.passwords)}; "
                f"listening on {s.host}:{s.port}"
            )
        else:
            s = broker.settings()
            conf = broker.DATA_DIR / "mosquitto.conf"
            if not conf.exists():
                raise SystemExit("run `python -m coffwe.feed broker setup` first")
            proc = broker.start(conf, s.host, s.port)
            print(f"Mosquitto running on {s.host}:{s.port} (pid {proc.pid}) -- Ctrl-C to stop")
            try:
                proc.wait()
            except KeyboardInterrupt:
                proc.terminate()
    elif args.command == "observe":
        from coffwe.feed.observed import generate

        run_dir = args.run or latest_run()
        n_msgs, n_labels = generate(run_dir)
        print(f"{run_dir.name}: {n_msgs:,} messages, {n_labels:,} labelled readings")
    elif args.command == "run":
        from coffwe.feed.runner import run_feed
        from coffwe.sim.truth import TruthStore

        run_dir = args.run or latest_run()
        info = TruthStore(run_dir).read_info()
        start = pd.Timestamp(args.start or info.events_start, tz="Asia/Kolkata")
        until = start + pd.Timedelta(hours=args.hours)
        s = broker.settings()
        t0 = time.perf_counter()
        print(f"Feeding {run_dir.name}: {start} -> {until} at {args.speed:g}x")
        results = run_feed(
            run_dir,
            start,
            until,
            args.speed,
            s.host,
            s.port,
            s.credentials("devices"),
            s.credentials("admin"),
        )
        for res in results:
            print(
                f"  {res['group']:<14} {res['sent']:>9,} readings"
                f"  ({res['late_publishes']:,} more than a sim-hour late)"
            )
        print(f"Done in {time.perf_counter() - t0:.1f}s")
    elif args.command == "clock":
        from coffwe.feed.publisher import connect, new_client
        from coffwe.feed.schema import CLOCK_SET_TOPIC, ClockCommand

        s = broker.settings()
        cmd = ClockCommand(
            speed=args.speed, running=False if args.pause else (True if args.resume else None)
        )
        client = new_client("clock-cli", *s.credentials("admin"))
        connect(client, s.host, s.port)
        client.publish(
            CLOCK_SET_TOPIC, cmd.model_dump_json(exclude_none=True), qos=1
        ).wait_for_publish(5)
        client.disconnect()
        print(f"Sent {cmd.model_dump(exclude_none=True)}")


if __name__ == "__main__":
    main()
