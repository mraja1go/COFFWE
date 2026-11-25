"""The simulation run: generates forcing, steps every subsystem, writes the results.

M1 scope: weather only. Later milestones add subsystems (soil, coffee, harvest,
livestock, pests, machines) that step through the hourly/daily loop here.
"""

from datetime import date, timedelta
from pathlib import Path

from coffwe.sim.rng import stream
from coffwe.sim.truth import DEFAULT_ROOT, RunInfo, TruthStore, run_id_for
from coffwe.sim.weather import generate_weather

# Physics spins up from New Year so the 2026 blossom showers and cherry development
# exist by harvest time; Postgres events begin the day after the seeded history ends.
SPINUP_START = date(2026, 1, 1)
EVENTS_START = date(2026, 11, 25)


def run(
    seed: int = 42,
    start: date = SPINUP_START,
    days: int = 365,
    root: Path = DEFAULT_ROOT,
) -> TruthStore:
    end = start + timedelta(days=days)
    store = TruthStore.create(root, run_id_for(seed, start, end))

    weather = generate_weather(start, end, stream(seed, "weather"))
    store.write("weather_hourly", weather)

    store.write_info(
        RunInfo(
            run_id=store.run_dir.name,
            seed=seed,
            start=start.isoformat(),
            end=end.isoformat(),
            events_start=EVENTS_START.isoformat(),
            datasets=["weather_hourly"],
        )
    )
    return store
