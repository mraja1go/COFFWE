"""Harvest & processing tests: picking rules, batch bookkeeping, processing, inventory,
and the Postgres write (against the separate coffwe_test database)."""

import math

import pandas as pd
import pytest
from sqlalchemy import func, select

from coffwe.estate_data import SEASON_END, SEASON_START, worker_specs
from coffwe.sim import engine
from coffwe.sim.harvest import (
    DRYING_DAYS_NEEDED,
    FERMENTATION_DAYS,
    NO_PICKING_RAIN_MM,
    OUTTURN,
    RECEIVING_LOSS,
)

WORKERS = {w.code: w for w in worker_specs()}


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    store = engine.run(seed=42, root=tmp_path_factory.mktemp("sim"))
    return {
        "coffee": store.read("coffee_daily").reset_index(),
        "harvest": store.read("harvest_daily").reset_index(),
        "batches": store.read("harvest_batches").reset_index(),
        "picks": store.read("harvest_picks").reset_index(),
        "moves": store.read("inventory_movements").reset_index(),
        "weather": store.read("weather_hourly"),
    }


# --- picking -----------------------------------------------------------------------


def test_every_ripened_kilo_is_picked_fallen_or_still_hanging(run):
    c, h = run["coffee"], run["harvest"]
    last_day = c["date"].max()
    last = c[c["date"] == last_day]
    # coffee_daily records the pool *before* that day's picking, so subtract the last
    # day's pick from what was hanging, or it would be counted twice.
    hanging = (
        last["ripe_kg"].sum() + last["overripe_kg"].sum()
        - h.loc[h["date"] == last_day, "picked_kg"].sum()
    )  # fmt: skip
    accounted = h["picked_kg"].sum() + c["fallen_kg"].sum() + hanging
    assert accounted == pytest.approx(c["ripened_kg"].sum(), abs=1.0)  # rounding only


def test_most_of_the_crop_is_picked(run):
    season = run["coffee"][run["coffee"]["date"] >= "2026-06-01"]
    assert run["harvest"]["picked_kg"].sum() / season["ripened_kg"].sum() > 0.85


def test_no_picking_on_sundays_or_in_heavy_rain(run):
    picked_days = pd.to_datetime(run["harvest"]["date"]).dt.date.unique()
    daily_rain = run["weather"]["rainfall"].groupby(run["weather"].index.date).sum()
    for d in picked_days:
        assert d.weekday() != 6
        assert daily_rain[d] <= NO_PICKING_RAIN_MM


def test_right_people_pick_in_and_after_the_season(run):
    p = run["picks"]
    p["day"] = pd.to_datetime(p["harvested_on"]).dt.date
    for _, row in p.iterrows():
        role = WORKERS[row["worker_code"]].role
        if SEASON_START <= row["day"] <= SEASON_END:
            assert role == "picker"
        else:
            assert row["day"] > SEASON_END and role == "field_worker"


def test_nobody_is_in_two_places_at_once(run):
    per_worker_day = run["picks"].groupby(["harvested_on", "worker_code"]).size()
    assert (per_worker_day == 1).all()


def test_peak_season_picking_rates_are_realistic(run):
    p = run["picks"]
    jan = p[pd.to_datetime(p["harvested_on"]).dt.month == 1]
    assert 25 < jan["kg"].mean() < 70  # selective picking, kg fresh cherry per picker-day


# --- batches -----------------------------------------------------------------------


def test_one_batch_per_unit_per_day_with_unique_codes(run):
    b = run["batches"]
    assert b["code"].is_unique
    assert not b.duplicated(["block_code", "variety", "harvested_on"]).any()


def test_batch_weights_add_up(run):
    b = run["batches"].set_index("code")
    picks = run["picks"].groupby("batch_code")["kg"].sum()
    pd.testing.assert_series_equal(
        picks.sort_index(), b["field_kg"].sort_index(), check_names=False, atol=0.05
    )
    loss = 1 - b["cherry_kg"] / b["field_kg"]
    lo, hi = RECEIVING_LOSS
    assert loss.between(lo - 0.001, hi + 0.001).all()


def test_processing_follows_variety_and_takes_realistic_time(run):
    b = run["batches"]
    assert (b.loc[b["variety"] == "AR", "method"] == "washed").all()
    assert (b.loc[b["variety"] == "RB", "method"] == "natural").all()
    done = b.dropna(subset=["stored_on"])
    days = (pd.to_datetime(done["stored_on"]) - pd.to_datetime(done["harvested_on"])).dt.days
    minimum = done["method"].map(
        {
            "washed": FERMENTATION_DAYS + DRYING_DAYS_NEEDED["washed"] - 1,
            "natural": 1 + DRYING_DAYS_NEEDED["natural"] - 1,
        }  # fmt: skip
    )
    assert (days >= minimum).all()
    assert (days <= minimum + 20).all()  # even a wet spell doesn't stall drying for weeks
    ratio = done["output_kg"] / done["cherry_kg"]
    assert (ratio - done["method"].map(OUTTURN)).abs().max() < 0.01


def test_status_matches_dates(run):
    b = run["batches"]
    assert b.loc[b["status"] == "dispatched", "dispatched_on"].notna().all()
    assert b.loc[b["status"] == "stored", "dispatched_on"].isna().all()
    stored = b.dropna(subset=["dispatched_on"])
    assert (pd.to_datetime(stored["dispatched_on"]) >= pd.to_datetime(stored["stored_on"])).all()
    assert set(pd.to_datetime(stored["dispatched_on"]).dt.day) <= {1, 15}


# --- inventory ---------------------------------------------------------------------


def test_ledger_never_goes_negative_and_signs_match_reasons(run):
    m = run["moves"].sort_values("day", kind="stable")
    assert (m.loc[m["reason"] == "used", "quantity"] < 0).all()
    assert (m.loc[m["reason"] == "purchase", "quantity"] > 0).all()
    assert (pd.to_datetime(m["day"]).dt.date >= engine.EVENTS_START).all()
    from coffwe.estate_data import INVENTORY_ITEMS

    opening = {i.code: float(i.opening_balance) for i in INVENTORY_ITEMS}
    for item, g in m.groupby("item_code"):
        assert (opening[item] + g["quantity"].cumsum() >= -1e-6).all(), item


def test_bags_cover_every_dispatched_lot(run):
    m, b = run["moves"], run["batches"]
    bags = -m.loc[m["item_code"] == "JUTE-BAG", "quantity"].sum()
    dispatched = b[
        b["dispatched_on"].notna()
        & (pd.to_datetime(b["dispatched_on"]).dt.date >= engine.EVENTS_START)
    ]
    kg = dispatched.groupby(["dispatched_on", "method"])["output_kg"].sum()
    assert bags == sum(math.ceil(v / 60) for v in kg)


# --- reproducibility ---------------------------------------------------------------


def test_same_seed_same_harvest(tmp_path, run):
    again = engine.run(seed=42, root=tmp_path).read("harvest_batches").reset_index()
    pd.testing.assert_frame_equal(again, run["batches"])


# --- Postgres (coffwe_test) --------------------------------------------------------


def test_run_writes_events_to_postgres(db_run, test_db):
    from coffwe.db.models import HarvestBatch, HarvestPick, InventoryMovement, Plant

    store = db_run
    batches = store.read("harvest_batches").reset_index()
    expected = batches[pd.to_datetime(batches["harvested_on"]).dt.date >= engine.EVENTS_START]
    with test_db() as s:
        assert s.scalar(select(func.count()).select_from(Plant)) == 44_950  # reseeded
        assert s.scalar(select(func.count()).select_from(HarvestBatch)) == len(expected)
        assert s.scalar(select(func.min(HarvestBatch.harvested_on))) >= engine.EVENTS_START
        db_kg = float(s.scalar(select(func.sum(HarvestBatch.cherry_kg))))
        assert db_kg == pytest.approx(expected["cherry_kg"].sum(), abs=0.5)
        assert (
            s.scalar(select(func.count()).select_from(HarvestPick))
            == store.db_counts["harvest_picks"]
        )
        assert s.scalar(select(func.count()).select_from(InventoryMovement)) == (
            23 + store.db_counts["inventory_movements"]  # opening balances + simulated
        )
