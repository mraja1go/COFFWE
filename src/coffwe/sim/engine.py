"""The simulation run: generates forcing, steps every subsystem, writes the results.

    weather (generated up front -- nothing on the estate changes the weather)
    for each day:
        management decides today's irrigation (start of day)
        24 x hourly: soil + microclimate for every block
        daily: coffee phenology from the day's rain, warmth and water stress
        (M4+) harvest, livestock, pests, machines

Subsystems added in later milestones slot into the day loop below.
"""

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from coffwe.estate_data import BLOCKS
from coffwe.sim import soil as soil_mod
from coffwe.sim.coffee import CoffeeModel
from coffwe.sim.management import BaselineManagement
from coffwe.sim.rng import stream
from coffwe.sim.truth import DEFAULT_ROOT, RunInfo, TruthStore, run_id_for
from coffwe.sim.weather import generate_weather

# Physics spins up from New Year so the 2026 blossom showers and cherry development
# exist by harvest time; Postgres events begin the day after the seeded history ends.
SPINUP_START = date(2026, 1, 1)
EVENTS_START = date(2026, 11, 25)
DEFAULT_DAYS = (date(2027, 4, 1) - SPINUP_START).days  # through the 2026-27 harvest


def run(
    seed: int = 42,
    start: date = SPINUP_START,
    days: int = DEFAULT_DAYS,
    root: Path = DEFAULT_ROOT,
) -> TruthStore:
    end = start + timedelta(days=days)
    store = TruthStore.create(root, run_id_for(seed, start, end))

    weather = generate_weather(start, end, stream(seed, "weather"))
    store.write("weather_hourly", weather)
    wx = {c: weather[c].to_numpy() for c in weather.columns}  # fast per-hour access

    soil = soil_mod.SoilModel()
    coffee = CoffeeModel(stream(seed, "coffee"))
    manager = BaselineManagement()
    block_hours: list[dict[str, np.ndarray]] = []
    coffee_rows: list[dict] = []

    for d in range(days):
        day = start + timedelta(days=d)
        plan = manager.irrigation_for(day, coffee)

        day_hours = []
        for h in range(24):
            i = d * 24 + h
            day_hours.append(
                soil.step(
                    rain=wx["rainfall"][i],
                    air_temp=wx["air_temperature"][i],
                    rh=wx["relative_humidity"][i],
                    solar=wx["solar_radiation"][i],
                    pressure=wx["barometric_pressure"][i],
                    irrigation=manager.hourly(plan, h),
                )
            )
        block_hours.extend(day_hours)

        def daily(col: str, how=np.sum, hours=day_hours) -> np.ndarray:
            return how(np.array([hr[col] for hr in hours]), axis=0)

        rain = daily("rain_mm")
        manager.observe_rain(rain)
        for row in coffee.on_day(
            day,
            water_mm=rain + daily("irrigation_mm"),
            rain_mm=rain,
            tmean_c=daily("air_temperature", np.mean),
            water_stress=daily("water_stress", np.mean),
        ):
            coffee_rows.append({"date": pd.Timestamp(day), **row})

    store.write("block_hourly", soil_mod.to_frame(weather.index, block_hours))
    store.write("coffee_daily", pd.DataFrame(coffee_rows).set_index("date").round(3))
    datasets = ["weather_hourly", "block_hourly", "coffee_daily"]
    for name, log, key in (
        ("coffee_blossoms", coffee.blossom_log, "opened_on"),
        ("irrigation_log", manager.log, "date"),
    ):
        if log:
            frame = pd.DataFrame(log)
            frame.index = pd.DatetimeIndex(pd.to_datetime(frame.pop(key)), name=key)
            store.write(name, frame)
            datasets.append(name)

    store.write_info(
        RunInfo(
            run_id=store.run_dir.name,
            seed=seed,
            start=start.isoformat(),
            end=end.isoformat(),
            events_start=EVENTS_START.isoformat(),
            datasets=datasets,
        )
    )
    return store


__all__ = ["BLOCKS", "DEFAULT_DAYS", "EVENTS_START", "SPINUP_START", "run"]
