"""The simulation run: generates forcing, steps every subsystem, writes the results.

    weather (generated up front -- nothing on the estate changes the weather)
    for each day:
        management decides today's irrigation (start of day)
        24 x hourly: soil + microclimate for every block
        daily: coffee phenology from the day's rain, warmth and water stress
        daily: picking plan -> picking -> processing -> reorders
        daily (from EVENTS_START): cattle herd, then the apiary
        daily: pests & disease, then the manager's IPM response
        daily: organic loop, field work, machine wear and failures; reorders last

Subsystems added in later milestones slot into the day loop below.
"""

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from coffwe.estate_data import BLOCKS
from coffwe.sim import soil as soil_mod
from coffwe.sim.coffee import CoffeeModel
from coffwe.sim.harvest import HarvestModel
from coffwe.sim.livestock import BeeModel, CattleModel
from coffwe.sim.machines import (
    PICKUP_DAILY_KM,
    PICKUP_TRIP_KM,
    TILLER_HOURS_PER_PICKING_DAY,
    MachineModel,
)
from coffwe.sim.management import BaselineManagement
from coffwe.sim.organics import OrganicsModel
from coffwe.sim.pests import PestModel, rust_temperature_weight
from coffwe.sim.rng import stream
from coffwe.sim.truth import DEFAULT_ROOT, RunInfo, TruthStore, run_id_for
from coffwe.sim.weather import generate_weather_for_seed

# Physics spins up from New Year so the 2026 blossom showers and cherry development
# exist by harvest time; Postgres events begin the day after the seeded history ends.
SPINUP_START = date(2026, 1, 1)
EVENTS_START = date(2026, 11, 25)
DEFAULT_DAYS = (date(2027, 5, 1) - SPINUP_START).days  # harvest + post-harvest work
BLOOM_DAYS = 4  # coffee flowers stay open (and full of nectar) ~3-4 days
GENSET_L_PER_HOUR = 2.5
PETROL_L_PER_HOUR = 0.6


def run(
    seed: int = 42,
    start: date = SPINUP_START,
    days: int = DEFAULT_DAYS,
    root: Path = DEFAULT_ROOT,
    write_db: bool = False,
    session_factory=None,  # defaults to the dev database (coffwe.db.base.SessionLocal)
    ipm: bool = True,  # False = counterfactual estate with no pest management at all
) -> TruthStore:
    end = start + timedelta(days=days)
    run_id = run_id_for(seed, start, end) + ("" if ipm else "-noipm")
    store = TruthStore.create(root, run_id)

    weather = generate_weather_for_seed(start, end, seed)  # same day, same weather
    store.write("weather_hourly", weather)
    wx = {c: weather[c].to_numpy() for c in weather.columns}  # fast per-hour access

    soil = soil_mod.SoilModel()
    coffee = CoffeeModel(stream(seed, "coffee"))
    harvest = HarvestModel(stream(seed, "harvest"), ledger_from=EVENTS_START)
    pests = PestModel(stream(seed, "pests"), coffee)
    manager = BaselineManagement(ipm_enabled=ipm)
    scouting = stream(seed, "scouting")  # sampling noise in the keeper's pest counts
    machines = MachineModel(stream(seed, "machines"))
    organics = OrganicsModel(harvest.ledger, ledger_from=EVENTS_START)
    block_hours: list[dict[str, np.ndarray]] = []
    coffee_rows: list[dict] = []
    harvest_rows: list[dict] = []
    cattle: CattleModel | None = None
    bees: BeeModel | None = None
    herd_rows: list[dict] = []

    for d in range(days):
        day = start + timedelta(days=d)
        caps = machines.capabilities(day)  # which machines work today
        plan = manager.irrigation_for(day, coffee)
        # A failed pump or stuck valve means the planned irrigation simply doesn't happen.
        plan = plan * np.array([caps["irrigation"][b.code] for b in BLOCKS])

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

        # Harvest: the crew sees the day's weather; the drying yard feels it.
        mast_rain = float(wx["rainfall"][d * 24 : (d + 1) * 24].sum())
        mast_solar = float(wx["solar_radiation"][d * 24 : (d + 1) * 24].mean())
        crews = manager.plan_picking(day, coffee, harvest, mast_rain)
        for row in harvest.pick_day(day, crews, coffee):
            harvest_rows.append({"date": pd.Timestamp(day), **row})
        activity = harvest.process_day(day, mast_rain, mast_solar, caps)

        # Pests and disease, then the manager's IPM response (before reordering stock).
        today_coffee = coffee_rows[-len(coffee.units) :]
        today_harvest = [r for r in harvest_rows if r["date"] == pd.Timestamp(day)]
        leaf_wet = np.array([hr["leaf_wetness"] for hr in day_hours]) >= 50
        block_temp = np.array([hr["air_temperature"] for hr in day_hours])
        pests.on_day(
            day,
            coffee,
            tmean_c=block_temp.mean(axis=0),
            rust_wet_hours=(leaf_wet * rust_temperature_weight(block_temp)).sum(axis=0),
            rain_mm=mast_rain,
            fallen_kg={f"{r['block_code']}-{r['variety']}": r["fallen_kg"] for r in today_coffee},
            picked_kg={f"{r['block_code']}-{r['variety']}": r["picked_kg"] for r in today_harvest},
        )
        manager.ipm(day, coffee, pests, harvest.ledger, EVENTS_START, mast_rain, scouting)

        # Livestock takes over from the seeded history on EVENTS_START.
        if day == EVENTS_START:
            cattle = CattleModel(stream(seed, "cattle"), day)
            bees = BeeModel(stream(seed, "bees"), day)
        if cattle is not None and bees is not None:
            cattle.on_day(day)
            herd_rows += [
                {"date": pd.Timestamp(day), "cattle_code": a.code, "lactating": a.lactating,
                 "pregnant": a.pregnant, "days_in_milk": a.days_in_milk(day) if a.lactating else 0,
                 "weight_kg": round(a.weight_kg, 1)}
                for a in cattle.herd.values()
            ]  # fmt: skip
            blooming = sum(
                1 for b in coffee.blossom_log if 0 <= (day - b["opened_on"]).days < BLOOM_DAYS
            ) / len(coffee.units)
            week_rain = float(wx["rainfall"][max(0, (d - 6) * 24) : (d + 1) * 24].sum())
            bees.on_day(day, mast_rain, min(blooming, 1.0), week_rain)

        # The organic loop, routine field work, and machine wear from today's work.
        organics.on_day(day, list(cattle.herd.values()) if cattle else None, activity["pulped_kg"])
        manager.feed_blocks(day, coffee, organics)
        field_hours = manager.field_operations(day)
        sprays = [x for x in manager.ipm_log if x["day"] == day and x["action"] != "gleaning"]
        irrigated = [b.code for b, mm in zip(BLOCKS, plan, strict=True) if mm > 0]
        usage = {
            "PULPER-01": activity["pulped_kg"] / 500,  # ~500 kg cherry an hour
            "RECV-SCALE": activity["received_batches"],
            "POLY-01-CURTAIN": 2.0 if activity["drying_batches"] and mast_rain >= 2 else 0.0,
            "GENSET-01": caps["power_cut_hours"],
            "PUMP-01": 6.0 * len(irrigated),
            "PICKUP-01": (PICKUP_DAILY_KM if day.weekday() != 6 else 0.0)
            + (PICKUP_TRIP_KM if activity["dispatched"] else 0.0),
            "TILLER-01": TILLER_HOURS_PER_PICKING_DAY if today_harvest else 0.0,
            **{f"VALVE-{code}": 1.0 for code in irrigated},
            **{f"SPRAY-0{i % 3 + 1}": 1.0 for i, _ in enumerate(sprays)},
            **field_hours,
        }
        machines.on_day(day, usage)
        if day >= EVENTS_START:
            ledger = harvest.ledger
            fuel = [("DIESEL", caps["power_cut_hours"] * GENSET_L_PER_HOUR, "generator, power cut")]
            fuel += [("PETROL-2T", h * PETROL_L_PER_HOUR, f"{code} field work")
                     for code, h in field_hours.items()]  # fmt: skip
            for item, qty, note in fuel:
                if qty > 0:
                    ledger.record(day, item, -qty, "used", notes=note)
            for spare_day, item, qty, asset in machines.spares_used:
                if spare_day == day:
                    ledger.record(day, item, -qty, "used", notes=f"repair of {asset}")
        manager.reorder(day, harvest.ledger, EVENTS_START)

    store.write("block_hourly", soil_mod.to_frame(weather.index, block_hours))
    store.write("coffee_daily", pd.DataFrame(coffee_rows).set_index("date").round(3))
    datasets = ["weather_hourly", "block_hourly", "coffee_daily"]
    batch_log = [
        {k: v for k, v in vars(b).items() if k != "picks"} | {"pickers": len(b.picks)}
        for b in harvest.batches
    ]
    pick_log = [
        {"harvested_on": b.harvested_on, "batch_code": b.code, "worker_code": w, "kg": kg}
        for b in harvest.batches
        for w, kg in b.picks
    ]
    movement_log = [vars(m) for m in harvest.ledger.movements]
    livestock_logs = []
    if cattle is not None and bees is not None:
        colony_log = [
            {k: v for k, v in vars(c).items() if k != "old_queens"} for c in bees.colonies
        ]
        livestock_logs = [
            ("milkings", cattle.milkings, "day"),
            ("cattle_health_events", cattle.health, "day"),
            ("cattle_breeding_events", cattle.breeding, "day"),
            ("herd_daily", herd_rows, "date"),
            ("hive_daily", bees.daily, "day"),
            ("honey_harvests", bees.harvests, "day"),
            ("bee_colonies", colony_log, "established_on"),
        ]
    for name, log, key in (
        ("coffee_blossoms", coffee.blossom_log, "opened_on"),
        ("irrigation_log", manager.log, "date"),
        ("harvest_daily", harvest_rows, "date"),
        ("harvest_batches", batch_log, "harvested_on"),
        ("harvest_picks", pick_log, "harvested_on"),
        ("inventory_movements", movement_log, "day"),
        ("pest_daily", pests.daily, "day"),
        ("trap_counts", pests.trap_counts, "day"),
        ("ipm_log", manager.ipm_log, "day"),
        ("asset_daily", machines.daily, "day"),
        ("asset_events", machines.events, "day"),
        ("organics_log", organics.log, "day"),
        *livestock_logs,
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

    if write_db:
        # Imported here so the simulator itself never needs a database to run.
        from coffwe.seed import seed as reseed
        from coffwe.sim.db import write_events, write_livestock, write_machines

        if session_factory is None:
            from coffwe.db.base import SessionLocal as session_factory
        with session_factory() as session:
            reseed(session)  # baseline state the run started from (commits)
            counts = write_events(session, harvest.batches, harvest.ledger.movements, EVENTS_START)
            if cattle is not None and bees is not None:
                counts |= write_livestock(session, cattle, bees)
            counts |= write_machines(session, machines, EVENTS_START)
            session.commit()
        store.db_counts = counts
    return store


__all__ = ["BLOCKS", "DEFAULT_DAYS", "EVENTS_START", "SPINUP_START", "run"]
