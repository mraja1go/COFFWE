"""Whole-estate tests: laws that must hold end to end, for any seed.

Every subsystem has its own tests; these check that, wired together over a full season,
nothing leaks -- water, cherry and stock are conserved, records are consistent across
subsystems, and the same seed always gives the same estate.
"""

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import func, select

from coffwe.estate_data import BLOCKS, INVENTORY_ITEMS
from coffwe.sim import engine
from coffwe.sim.soil import SOIL_TYPES

SEEDS = [1, 7, 2026]
DEPTH_MM = {b.code: SOIL_TYPES[b.soil].root_depth_m * 1000 for b in BLOCKS}


@pytest.fixture(scope="module", params=SEEDS)
def store(request, tmp_path_factory):
    return engine.run(seed=request.param, root=tmp_path_factory.mktemp(f"seed{request.param}"))


def test_water_is_conserved_on_every_block_all_season(store):
    bh = store.read("block_hourly")
    for code, g in bh.groupby("block_code"):
        g = g.iloc[1:]  # from the end of hour 1 to the end of the run
        soil_change = (
            g["soil_moisture"].iloc[-1] - bh[bh.block_code == code]["soil_moisture"].iloc[0]
        )
        soil_change *= DEPTH_MM[code]
        soil_flux = (g["infiltration_mm"] - g["et_soil_mm"] - g["drainage_mm"]).sum()
        assert soil_flux == pytest.approx(soil_change, abs=0.5), code  # 4-dp rounding
        canopy_change = (
            g["canopy_store_mm"].iloc[-1] - bh[bh.block_code == code]["canopy_store_mm"].iloc[0]
        )
        canopy_flux = (g["interception_mm"] - g["canopy_evap_mm"]).sum()
        assert canopy_flux == pytest.approx(canopy_change, abs=0.5), code


def test_every_ripened_kilo_is_accounted_for(store):
    c = store.read("coffee_daily").reset_index()
    h = store.read("harvest_daily").reset_index()
    last_day = c["date"].max()
    last = c[c["date"] == last_day]
    hanging = (last["ripe_kg"] + last["overripe_kg"]).sum() - h.loc[
        h["date"] == last_day, "picked_kg"
    ].sum()
    total = h["picked_kg"].sum() + c["fallen_kg"].sum() + hanging
    assert total == pytest.approx(c["ripened_kg"].sum(), abs=2.0)


def test_stores_never_run_negative(store):
    m = store.read("inventory_movements").reset_index()
    opening = {i.code: float(i.opening_balance) for i in INVENTORY_ITEMS}
    for item, g in m.groupby("item_code"):
        balance = opening[item] + g["quantity"].cumsum()
        assert balance.min() >= -1e-6, item
    assert (m.loc[m["reason"] == "used", "quantity"] < 0).all()
    assert (m.loc[m["reason"].isin(["purchase", "produced"]), "quantity"] > 0).all()


def test_batch_life_cycle_dates_are_in_order(store):
    b = store.read("harvest_batches").reset_index()
    order = ["harvested_on", "pulped_on", "drying_started_on", "stored_on", "dispatched_on"]
    dates = b[order].apply(pd.to_datetime)
    for earlier, later in zip(order, order[1:], strict=False):
        both = dates[earlier].notna() & dates[later].notna()
        assert (dates.loc[both, later] >= dates.loc[both, earlier]).all(), (earlier, later)
    assert (b.loc[b["method"] == "natural", "pulped_on"].isna()).all()
    assert b["status"].isin(["received", "processing", "drying", "stored", "dispatched"]).all()


def test_every_failure_is_repaired_or_still_open(store):
    e = store.read("asset_events").reset_index()
    for code, g in e.sort_values("day", kind="stable").groupby("asset_code"):
        open_failure = False
        for kind in g["event_type"]:
            if kind == "failure":
                assert not open_failure, f"{code} failed while already down"
                open_failure = True
            elif kind == "repair":
                assert open_failure, f"{code} repaired without a failure"
                open_failure = False
    assert (e["usage_since_service"] >= 0).all()


def test_season_is_plausible_for_any_weather(store):
    c = store.read("coffee_daily").reset_index()
    season = c[c["date"] >= "2026-06-01"]
    total_t = season["ripened_kg"].sum() / 1000
    assert 35 < total_t < 130
    peak = season.loc[season.groupby(["block_code", "variety"])["ripened_kg"].idxmax()]
    for _, row in peak.iterrows():
        allowed = (11, 12, 1, 2) if row["variety"] == "AR" else (12, 1, 2, 3)
        assert row["date"].month in allowed, row.to_dict()


def test_milk_and_honey_records_are_consistent(store):
    milk = store.read("milkings").reset_index()
    herd = store.read("herd_daily").reset_index()
    lactating = herd[herd["lactating"]].set_index(["date", "cattle_code"]).index
    milked = pd.MultiIndex.from_frame(milk[["day", "cattle_code"]].rename(columns={"day": "date"}))
    assert milked.isin(lactating).mean() > 0.99  # (dry-off is decided after the day's milking)
    honey = store.read("honey_harvests")
    assert (honey["honey_kg"] > 0).all()


def test_same_seed_same_estate(tmp_path):
    a = engine.run(seed=5, root=tmp_path / "a", days=120)
    b = engine.run(seed=5, root=tmp_path / "b", days=120)
    for dataset in a.read_info().datasets:
        pd.testing.assert_frame_equal(a.read(dataset), b.read(dataset), obj=dataset)


def test_maintenance_log_and_asset_status_reach_postgres(db_run, test_db):
    from coffwe.db.models import Asset, AssetEvent

    events = db_run.read("asset_events").reset_index()
    expected = events[pd.to_datetime(events["day"]).dt.date >= engine.EVENTS_START]
    final = db_run.read("asset_daily").reset_index()
    final = final[final["day"] == final["day"].max()].set_index("asset_code")["status"]
    with test_db() as s:
        assert s.scalar(select(func.count()).select_from(AssetEvent)) == len(expected)
        statuses = dict(s.execute(select(Asset.code, Asset.status)).all())
    assert statuses == final.to_dict()
    assert np.all(expected["usage_since_service"] >= 0)
