"""Coffee phenology tests: blossom rules, crop conservation, and the seasonal calendar."""

from dataclasses import replace
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from coffwe.estate_data import BLOCKS
from coffwe.sim import engine
from coffwe.sim.coffee import (
    BLOSSOM_DELAY_DAYS,
    OVERRIPE_DAYS,
    RIPE_DAYS,
    SET_FLOOR,
    CoffeeModel,
    shade_yield_factor,
)
from coffwe.sim.management import BaselineManagement
from coffwe.sim.rng import stream

N = len(BLOCKS)


def step(model, day, water=0.0, rain=None, tmean=21.0, stress=1.0):
    """One day of uniform conditions; `water` may also be a per-block array."""
    water = np.broadcast_to(np.asarray(water, dtype=float), (N,))
    return model.on_day(
        day,
        water_mm=water,
        rain_mm=water if rain is None else np.full(N, rain),
        tmean_c=np.full(N, tmean),
        water_stress=np.full(N, stress),
    )


def days(start, n):
    return [start + timedelta(days=i) for i in range(n)]


# --- blossom rules -----------------------------------------------------------------


def test_shower_opens_flowers_after_delay():
    model = CoffeeModel(stream(1, "coffee"))
    step(model, date(2026, 2, 10), water=30)  # dormancy satisfied (assumed dry December)
    assert model.blossom_log == []  # buds respond, but flowers aren't open yet
    for d in days(date(2026, 2, 11), BLOSSOM_DELAY_DAYS):
        step(model, d)
    opened = {b["opened_on"] for b in model.blossom_log}
    assert opened == {date(2026, 2, 10) + timedelta(days=BLOSSOM_DELAY_DAYS)}
    assert len(model.blossom_log) == len(model.units)  # 30 mm clears both thresholds


def test_small_shower_opens_arabica_but_not_robusta():
    model = CoffeeModel(stream(1, "coffee"))
    step(model, date(2026, 2, 10), water=22)  # AR threshold 20, RB threshold 25
    for d in days(date(2026, 2, 11), BLOSSOM_DELAY_DAYS):
        step(model, d)
    assert {b["variety"] for b in model.blossom_log} == {"AR"}


def test_no_blossom_without_a_dry_spell():
    model = CoffeeModel(stream(1, "coffee"))
    for u in model.units:
        u.dry_days = 0  # it has been raining all along
    for d in days(date(2026, 2, 1), 20):
        step(model, d, water=30)
    assert model.blossom_log == []


def test_no_blossom_outside_the_window():
    model = CoffeeModel(stream(1, "coffee"))
    for d in [date(2026, 1, 5), date(2026, 6, 1), date(2026, 10, 1)]:
        for dd in days(d, 12):
            step(model, dd, water=40)
    assert model.blossom_log == []


@settings(max_examples=25, deadline=None)
@given(showers=st.lists(st.integers(0, 120), min_size=1, max_size=12))
def test_flower_shares_never_exceed_the_bud_load(showers):
    model = CoffeeModel(stream(2, "coffee"))
    for k, mm in enumerate(showers):
        for d in days(date(2026, 1, 25) + timedelta(days=9 * k), 9):
            step(model, d, water=mm if d.day % 9 == 0 else 0)
    shares = pd.DataFrame(
        model.blossom_log or [{"block_code": "", "variety": "", "flowers_share": 0}]
    )
    per_unit = shares.groupby(["block_code", "variety"])["flowers_share"].sum()
    assert (per_unit <= 1 + 1e-9).all()
    for u in model.units:
        assert 0 <= u.buds_remaining <= 1


# --- crop development --------------------------------------------------------------


def run_season(model, blossom_day=date(2026, 2, 20), tmean=21.0, stress=1.0, rain_on_open=0.0):
    """Blossom shower, then a year of constant conditions. Returns daily rows."""
    rows = []
    for d in days(date(2026, 1, 20), 420):
        water = 30.0 if d == blossom_day else 0.0
        opening = d == blossom_day + timedelta(days=BLOSSOM_DELAY_DAYS)
        rows += [
            {"date": d, **r}
            for r in step(model, d, water=water, rain=rain_on_open if opening else water,
                          tmean=tmean, stress=stress)
        ]  # fmt: skip
    return pd.DataFrame(rows)


def test_crop_is_conserved_from_branch_to_ground():
    model = CoffeeModel(stream(3, "coffee"))
    rows = run_season(model)
    for (_, _), g in rows.groupby(["block_code", "variety"]):
        ripened, fallen = g["ripened_kg"].sum(), g["fallen_kg"].sum()
        on_branch = g["ripe_kg"].iloc[-1] + g["overripe_kg"].iloc[-1]
        assert ripened == pytest.approx(fallen + on_branch, rel=1e-9, abs=1e-6)
        assert (g[["green_kg", "ripe_kg", "overripe_kg", "fallen_kg"]] >= -1e-9).all().all()


def test_unpicked_cherries_fall_after_ripe_plus_overripe_days():
    model = CoffeeModel(stream(3, "coffee"))
    rows = run_season(model)
    g = rows[(rows.block_code == "B8") & (rows.variety == "AR")].reset_index(drop=True)
    first_ripe = g.index[g["ripened_kg"] > 0][0]
    first_fall = g.index[g["fallen_kg"] > 0][0]
    assert first_fall - first_ripe == RIPE_DAYS + OVERRIPE_DAYS


def test_warmer_blocks_ripen_sooner():
    peaks = {}
    for t in (19.0, 23.0):
        rows = run_season(CoffeeModel(stream(4, "coffee")), tmean=t)
        g = rows[(rows.block_code == "B8")]
        peaks[t] = g.loc[g["ripened_kg"].idxmax(), "date"]
    assert peaks[23.0] < peaks[19.0]


def test_water_stress_cuts_the_crop_but_not_below_the_floor():
    totals = {}
    for ks in (1.0, 0.0):
        rows = run_season(CoffeeModel(stream(5, "coffee")), stress=ks)
        totals[ks] = rows["ripened_kg"].sum()
    ratio = totals[0.0] / totals[1.0]
    assert SET_FLOOR * 0.7 - 1e-9 <= ratio < 1  # set floor x worst fill factor


def test_rain_during_robusta_blossom_hurts_its_fruit_set_only():
    dry = run_season(CoffeeModel(stream(6, "coffee")))
    wet = run_season(CoffeeModel(stream(6, "coffee")), rain_on_open=20.0)
    by = lambda df: df.groupby("variety")["ripened_kg"].sum()  # noqa: E731
    assert by(wet)["RB"] < by(dry)["RB"]
    assert by(wet)["AR"] == pytest.approx(by(dry)["AR"])


def test_open_sun_has_higher_potential_than_full_shade():
    assert shade_yield_factor(0) > shade_yield_factor(85) > shade_yield_factor(100)
    b10 = replace(BLOCKS[9], shade_pct=0)
    assert b10.code == "B10"


# --- management --------------------------------------------------------------------


def test_manager_forces_blossom_when_showers_fail_and_backs_it_up():
    model, manager = CoffeeModel(stream(7, "coffee")), BaselineManagement()
    for d in days(date(2026, 1, 20), 80):
        plan = manager.irrigation_for(d, model)
        step(model, d, water=plan, rain=0.0)  # each block gets its own irrigation
        manager.observe_rain(np.zeros(N))
    log = pd.DataFrame(manager.log)
    blossom = log[log.reason == "blossom"]
    assert set(blossom[blossom.date == date(2026, 2, 20)].block_code) == {
        b.code for b in BLOCKS if "RB" in b.plantings
    }
    assert set(blossom[blossom.date == date(2026, 3, 10)].block_code) == {"B8", "B9"}
    assert (log[log.reason == "backing"].groupby("block_code").size() == 1).all()
    assert len(model.blossom_log) > 0


def test_manager_does_nothing_if_nature_blossoms_first():
    model, manager = CoffeeModel(stream(8, "coffee")), BaselineManagement()
    for d in days(date(2026, 1, 20), 80):
        plan = manager.irrigation_for(d, model)
        natural = 35.0 if d == date(2026, 2, 5) else 0.0
        step(model, d, water=natural + plan, rain=natural)
        manager.observe_rain(np.full(N, natural))
    assert manager.log == []


# --- the whole season, end to end --------------------------------------------------


@pytest.fixture(scope="module")
def season(tmp_path_factory):
    store = engine.run(seed=42, root=tmp_path_factory.mktemp("sim"))
    return store.read("coffee_daily").reset_index()


def test_harvest_calendar_matches_karnataka_practice(season):
    s = season[season["date"] >= "2026-06-01"]
    peak = s.loc[s.groupby(["block_code", "variety"])["ripened_kg"].idxmax()]
    for _, row in peak.iterrows():
        month = row["date"].month
        if row["variety"] == "AR":
            assert month in (11, 12, 1), row
        else:
            assert month in (12, 1, 2), row


def test_estate_crop_is_plausible(season):
    total_t = season[season["date"] >= "2026-06-01"]["ripened_kg"].sum() / 1000
    # ~30,100 plants x 2-4 kg fresh cherry each
    assert 50 < total_t < 130
