"""The estate's one clock: simulated time that runs at a chosen speed.

    sim_now = sim_anchor + (wall_now - wall_anchor) * speed      (while running)

speed 1 = real time; 3600 = one simulated hour per real second. Pausing freezes
sim time; changing speed re-anchors so time never jumps. Every component asks the
clock for "now", so devices, ingestion and the twin always agree.

ManualClock is the same interface stepped by hand -- for tests and offline replays.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass
class SimClock:
    sim_start: datetime  # timezone-aware
    speed: float = 1.0
    wall: Callable[[], float] = time.monotonic
    running: bool = True
    _sim_anchor: datetime = field(init=False)
    _wall_anchor: float = field(init=False)

    def __post_init__(self) -> None:
        if self.sim_start.tzinfo is None:
            raise ValueError("sim_start must be timezone-aware")
        self._sim_anchor, self._wall_anchor = self.sim_start, self.wall()

    def now(self) -> datetime:
        if not self.running:
            return self._sim_anchor
        return self._sim_anchor + timedelta(seconds=(self.wall() - self._wall_anchor) * self.speed)

    def _reanchor(self) -> None:
        self._sim_anchor, self._wall_anchor = self.now(), self.wall()

    def pause(self) -> None:
        self._reanchor()
        self.running = False

    def resume(self) -> None:
        self._wall_anchor = self.wall()
        self.running = True

    def set_speed(self, speed: float) -> None:
        if speed <= 0:
            raise ValueError("speed must be positive (use pause() to stop)")
        self._reanchor()
        self.speed = speed

    def wall_seconds_until(self, sim_time: datetime) -> float:
        """How long to sleep (real seconds) until the clock reaches sim_time."""
        if not self.running:
            return float("inf")
        return max((sim_time - self.now()).total_seconds() / self.speed, 0.0)

    def to_message(self) -> dict:
        return {"sim_time": self.now().isoformat(), "speed": self.speed, "running": self.running}


@dataclass
class ManualClock:
    current: datetime

    def now(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> datetime:
        self.current += delta
        return self.current
