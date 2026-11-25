"""Phase 2 validation report: one season of the simulated estate, end to end.

    uv run python -m coffwe.sim report [--seed 42]

Runs the estate with and without pest management and writes docs/phase2-report.md --
the numbers behind every "this looks realistic" claim, regenerated from the code.
"""

import time
from pathlib import Path

import pandas as pd

from coffwe.estate_data import BLOCKS
from coffwe.sim import engine
from coffwe.sim.truth import TruthStore
from coffwe.sim.validation import _md_table

REPORT = Path("docs/phase2-report.md")
PLANTS = {f"{b.code}-{v}": n for b in BLOCKS for v, n in b.plantings.items() if v in ("AR", "RB")}


def _table(df: pd.DataFrame, fmt: str = "{:.1f}") -> str:
    return _md_table(df, fmt)


def _weather(s: TruthStore) -> str:
    w = s.read("weather_hourly")
    y = w[w.index.year == 2026]
    daily = y["rainfall"].groupby(y.index.date).sum()
    monthly = y["rainfall"].groupby(y.index.month).sum()
    return (
        f"2026 at the mast: **{y['rainfall'].sum():.0f} mm** rain on {int((daily >= 1).sum())} "
        f"wet days ({monthly.loc[[6, 7, 8, 9]].sum() / y['rainfall'].sum():.0%} in Jun-Sep), "
        f"mean temperature {y['air_temperature'].mean():.1f} degC. The generator itself is "
        "validated against 10 years of real data in `docs/weather-validation.md`."
    )


def _water(s: TruthStore) -> str:
    b = s.read("block_hourly")
    b = b[b.index.year == 2026]
    cols = ["rain_mm", "irrigation_mm", "interception_mm", "runoff_mm", "et_soil_mm",
            "canopy_evap_mm", "drainage_mm"]  # fmt: skip
    t = b.groupby("block_code")[cols].sum()
    t["total_et_mm"] = t["et_soil_mm"] + t["canopy_evap_mm"]
    t["runoff_%"] = 100 * t["runoff_mm"] / t["rain_mm"]
    t = t.loc[[x.code for x in BLOCKS], ["rain_mm", "irrigation_mm", "runoff_mm", "runoff_%",
                                         "total_et_mm", "drainage_mm"]]  # fmt: skip
    return _table(t, "{:.0f}")


def _coffee(s: TruthStore) -> tuple[str, str]:
    c = s.read("coffee_daily").reset_index()
    c = c[c["date"] >= "2026-06-01"]
    c["unit"] = c["block_code"] + "-" + c["variety"]
    h = s.read("harvest_daily").reset_index()
    h["unit"] = h["block_code"] + "-" + h["variety"]
    t = c.groupby("unit").agg(ripened_kg=("ripened_kg", "sum"), fallen_kg=("fallen_kg", "sum"),
                              borer_loss_kg=("borer_loss_kg", "sum"))  # fmt: skip
    t["picked_kg"] = h.groupby("unit")["picked_kg"].sum()
    t["kg_per_plant"] = t["ripened_kg"] / pd.Series(PLANTS)
    peak = c.loc[c.groupby("unit")["ripened_kg"].idxmax()].set_index("unit")["date"]
    t["peak_ripening"] = peak.dt.strftime("%d %b").map(lambda d: d)  # keep as text column
    numeric = t.drop(columns="peak_ripening")
    table = _table(numeric, "{:.1f}")
    peaks = ", ".join(f"{u} {d}" for u, d in t["peak_ripening"].items())
    total = (
        f"Estate: **{t['ripened_kg'].sum() / 1000:.1f} t** fresh cherry ripened, "
        f"**{t['picked_kg'].sum() / 1000:.1f} t picked** "
        f"({t['picked_kg'].sum() / t['ripened_kg'].sum():.0%}), "
        f"{t['fallen_kg'].sum() / 1000:.1f} t fell, {t['borer_loss_kg'].sum() / 1000:.2f} t lost "
        f"to berry borer. Peak ripening: {peaks}."
    )
    return total, table


def _harvest(s: TruthStore) -> str:
    b = s.read("harvest_batches").reset_index()
    p = s.read("harvest_picks").reset_index()
    p["month"] = pd.to_datetime(p["harvested_on"]).dt.strftime("%Y-%m")
    rate = p.groupby(["month", "harvested_on", "worker_code"])["kg"].sum().groupby("month").mean()
    done = b.dropna(subset=["stored_on"])
    days = (pd.to_datetime(done["stored_on"]) - pd.to_datetime(done["harvested_on"])).dt.days
    by_method = done.assign(days=days).groupby("method").agg(
        batches=("code", "count"), cherry_t=("cherry_kg", lambda x: x.sum() / 1000),
        dried_t=("output_kg", lambda x: x.sum() / 1000), median_days_to_store=("days", "median"),
    )  # fmt: skip
    return (
        f"{len(b)} batches. Picking rate (kg fresh cherry per picker-day) by month: "
        + ", ".join(f"{m} {v:.0f}" for m, v in rate.items())
        + ".\n\n"
        + _table(by_method, "{:.1f}")
    )


def _livestock(s: TruthStore) -> str:
    milk = s.read("milkings").reset_index()
    per_cow = milk.groupby("cattle_code").agg(
        days=("day", "nunique"), litres=("litres", "sum"), fat_pct=("fat_pct", "mean")
    )
    per_cow["l_per_day"] = per_cow["litres"] / per_cow["days"]
    breeding = s.read("cattle_breeding_events").reset_index()
    health = s.read("cattle_health_events").reset_index()
    honey = s.read("honey_harvests").reset_index()
    colonies = s.read("bee_colonies").reset_index()
    calvings = int((breeding["event_type"] == "calving").sum())
    return (
        _table(per_cow[["days", "litres", "l_per_day", "fat_pct"]], "{:.1f}")
        + f"\n\nBreeding events: {breeding['event_type'].value_counts().to_dict()} "
        f"({calvings} calving). Health events: {health['event_type'].value_counts().to_dict()}. "
        f"Honey: {len(honey)} harvests, {honey['honey_kg'].sum():.1f} kg. Colonies ended in the "
        f"run: {int(colonies['ended_on'].notna().sum())}; new (re-stocked): "
        f"{int(colonies['is_new'].sum())}."
    )


def _pests(on: TruthStore, off: TruthStore) -> str:
    rows = {}
    for label, s in (("With IPM", on), ("No pest management", off)):
        p = s.read("pest_daily").reset_index()
        ar = p[p["variety"] == "AR"]
        harvest = p[(p["day"] >= "2027-01-01") & (p["day"] < "2027-03-01")]
        c = s.read("coffee_daily")
        rows[label] = {
            "bored berries Jan-Feb (%)": 100 * harvest["cbb_infestation"].mean(),
            "Arabica rust peak (%)": 100 * ar.groupby("day")["rust_incidence"].mean().max(),
            "borer loss (t)": c["borer_loss_kg"].sum() / 1000,
            "cherry picked (t)": s.read("harvest_daily")["picked_kg"].sum() / 1000,
        }
    return _table(pd.DataFrame(rows), "{:.1f}")


def _machines(s: TruthStore) -> str:
    e = s.read("asset_events").reset_index()
    f = e[e["event_type"] == "failure"].copy()
    if f.empty:
        return "No failures this season."
    f["usage"] = f["usage_since_service"].round(1).astype(str) + " " + f["usage_unit"]
    lines = [f"| {r.day:%d %b %Y} | {r.asset_code} | {r.usage} |" for r in f.itertuples()]
    return "| Failed on | Asset | Wear since service |\n|---|---|---|\n" + "\n".join(lines)


def _inventory(s: TruthStore) -> str:
    m = s.read("inventory_movements").reset_index()
    t = m.pivot_table(index="item_code", columns="reason", values="quantity", aggfunc="sum").fillna(
        0
    )
    return _table(t, "{:.0f}")


def run(seed: int = 42) -> Path:
    t0 = time.perf_counter()
    on = engine.run(seed=seed)
    off = engine.run(seed=seed, ipm=False)
    elapsed = time.perf_counter() - t0
    info = on.read_info()
    total, crop_table = _coffee(on)
    REPORT.write_text(f"""# Phase 2 validation report — the simulated estate

_Generated by `uv run python -m coffwe.sim report --seed {seed}`: one run {info.start} →
{info.end} (physics from New Year; Postgres events from {info.events_start}), plus the same
estate with no pest management. Both runs took {elapsed:.1f} s together._

## Weather
{_weather(on)}

## Water balance by block (2026, mm)
Shade catches rain (interception), steep thin soils shed it (runoff), the rest
evaporates or drains below the roots.

{_water(on)}

## Coffee crop (2026-27 season, kg fresh cherry)
{total}

{crop_table}

## Harvest & processing
{_harvest(on)}

## Livestock (from {info.events_start})
{_livestock(on)}

## Pests & disease — managed vs unmanaged estate
{_pests(on, off)}

## Machine breakdowns (whole run)
Weibull wear in each machine's own unit of use; the pre-season service on 15 Oct resets it.

{_machines(on)}

## Inventory flows (from {info.events_start})
{_inventory(on)}

## What's checked automatically
`uv run pytest` covers, among others: exact water conservation, crop and stock
accounting, batch life-cycle order, failure → repair pairing and Weibull lifetimes
(KS test), weather depending only on (seed, day), seasonal calendars across several
weather years, managed vs unmanaged pest outcomes, and the Postgres write.
""")
    return REPORT
