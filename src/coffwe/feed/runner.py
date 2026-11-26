"""The feed orchestrator: owns the estate clock and runs the device-group processes.

    uv run python -m coffwe.feed run --speed 3600              # a sim-hour per second
    uv run python -m coffwe.feed clock --speed 60 | --pause | --resume

The orchestrator publishes the clock (retained) on coffwe/v1/clock, re-anchoring it
whenever someone publishes a ClockCommand on coffwe/v1/clock/set, and republishes it
every few seconds so late joiners and drifting clocks stay in step. Device groups run
as separate OS processes (multiprocessing, "spawn") -- like separate field gateways.
"""

import multiprocessing as mp
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from coffwe.feed.publisher import GROUPS, connect, new_client, run_group
from coffwe.feed.schema import CLOCK_SET_TOPIC, CLOCK_TOPIC, ClockCommand, ClockState

REPUBLISH_EVERY_S = 5.0


@dataclass
class Clock:
    """Authoritative clock state, held by the orchestrator."""

    sim_time: datetime
    speed: float
    running: bool = True
    wall_epoch: float = 0.0

    def __post_init__(self) -> None:
        self.wall_epoch = time.time()

    def now(self) -> datetime:
        if not self.running:
            return self.sim_time
        return self.sim_time + pd.Timedelta(seconds=(time.time() - self.wall_epoch) * self.speed)

    def apply(self, cmd: ClockCommand) -> None:
        self.sim_time, self.wall_epoch = self.now(), time.time()  # re-anchor: no jump
        if cmd.speed is not None:
            self.speed = cmd.speed
        if cmd.running is not None:
            self.running = cmd.running

    def state(self) -> ClockState:
        return ClockState(
            sim_time=self.sim_time,
            speed=self.speed,
            running=self.running,
            wall_epoch=self.wall_epoch,
        )


def _group_entry(kwargs: dict, results: mp.Queue, ready) -> None:
    results.put(run_group(**kwargs, ready=ready))


def run_feed(
    run_dir: Path,
    start: datetime,
    until: datetime,
    speed: float,
    host: str,
    port: int,
    devices_credentials: tuple[str, str],
    admin_credentials: tuple[str, str],
    groups: list[str] | None = None,
    on_tick=None,
) -> list[dict]:
    # The clock starts *paused*: group processes need a few real seconds to load their
    # feed and connect their stations, which at 3600x would already be hours of backlog.
    clock = Clock(pd.Timestamp(start).to_pydatetime(), speed, running=False)
    admin = new_client("feed-orchestrator", *admin_credentials)

    def publish_clock() -> None:
        admin.publish(CLOCK_TOPIC, clock.state().model_dump_json(), qos=1, retain=True)

    def on_command(_c, _u, msg) -> None:
        try:
            clock.apply(ClockCommand.model_validate_json(msg.payload))
        except ValueError:
            return  # malformed command: ignore it rather than crash the feed
        publish_clock()

    admin.on_message = on_command
    connect(admin, host, port)
    admin.subscribe(CLOCK_SET_TOPIC, qos=1)
    publish_clock()

    ctx = mp.get_context("spawn")
    results: mp.Queue = ctx.Queue()
    stop_file = Path(tempfile.mkdtemp()) / "stop"
    procs, ready = [], []
    for group in groups or list(GROUPS):
        ready.append(ctx.Event())
        kwargs = dict(
            group=group,
            run_dir=str(run_dir),
            start=str(pd.Timestamp(start)),
            until=str(pd.Timestamp(until)),
            host=host,
            port=port,
            username=devices_credentials[0],
            password=devices_credentials[1],
            stop_file=str(stop_file),
        )
        p = ctx.Process(
            target=_group_entry, args=(kwargs, results, ready[-1]), name=f"feed-{group}"
        )
        p.start()
        procs.append(p)

    # Ready barrier: start the clock only once every group is connected and loaded.
    deadline = time.monotonic() + 120
    while not all(e.is_set() for e in ready) and time.monotonic() < deadline:
        if not any(p.is_alive() for p in procs):
            break
        time.sleep(0.05)
    clock.apply(ClockCommand(running=True))
    publish_clock()

    last = time.monotonic()
    try:
        while any(p.is_alive() for p in procs):
            # No stop at `until`: each group's timeline already ends there, and a group
            # running behind must still publish its backlog (stopping it here lost data).
            if time.monotonic() - last > REPUBLISH_EVERY_S:
                publish_clock()
                last = time.monotonic()
            if on_tick:
                on_tick(clock)
            time.sleep(0.1)
    except KeyboardInterrupt:
        stop_file.touch()
    for p in procs:
        p.join(timeout=30)
    final = min(clock.now(), pd.Timestamp(until).to_pydatetime())  # read before pausing
    clock.sim_time, clock.wall_epoch, clock.running = final, time.time(), False
    publish_clock()
    admin.disconnect()
    admin.loop_stop()
    out = []
    while not results.empty():
        out.append(results.get())
    return sorted(out, key=lambda r: r["group"])
