"""The simulation run: generates forcing, steps every subsystem, writes the results.

    weather (generated up front -- nothing on the estate changes the weather)
      -> for each hour: soil + microclimate for every block
      -> (M3+) once a day: coffee, harvest, livestock, pests, machines

Subsystems added in later milestones slot into the hourly/daily loop below.
"""

from datetime import date, timedelta
from pathlib import Path

from coffwe.sim import soil as soil_mod
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

    soil = soil_mod.SoilModel()
    columns = {c: weather[c].to_numpy() for c in weather.columns}  # fast per-hour access
    block_hours = []
    for i in range(len(weather)):
        block_hours.append(
            soil.step(
                rain=columns["rainfall"][i],
                air_temp=columns["air_temperature"][i],
                rh=columns["relative_humidity"][i],
                solar=columns["solar_radiation"][i],
                pressure=columns["barometric_pressure"][i],
            )
        )
    store.write("block_hourly", soil_mod.to_frame(weather.index, block_hours))

    store.write_info(
        RunInfo(
            run_id=store.run_dir.name,
            seed=seed,
            start=start.isoformat(),
            end=end.isoformat(),
            events_start=EVENTS_START.isoformat(),
            datasets=["weather_hourly", "block_hourly"],
        )
    )
    return store
