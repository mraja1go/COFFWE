"""Soil water balance tests: conservation and physical bounds under any weather."""

from dataclasses import replace
from datetime import date

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from coffwe.estate_data import BLOCKS
from coffwe.sim import engine
from coffwe.sim.soil import SOIL_TYPES, SoilModel, priestley_taylor_mm_h

# One hour of weather: anything from bone-dry noon to a cloudburst at night.
hours = st.fixed_dictionaries(
    {
        "rain": st.one_of(st.just(0.0), st.floats(0, 120)),
        "air_temp": st.floats(5, 38),
        "rh": st.floats(20, 100),
        "solar": st.one_of(st.just(0.0), st.floats(0, 1100)),
        "pressure": st.floats(860, 885),
    }
)
weather_sequences = st.lists(hours, min_size=1, max_size=200)


def test_every_block_soil_is_defined_and_ordered():
    for b in BLOCKS:
        s = SOIL_TYPES[b.soil]
        assert 0 < s.wilting_point < s.field_capacity < s.saturation < 0.6  # sensor max


@settings(max_examples=60, deadline=None)
@given(sequence=weather_sequences)
def test_water_is_conserved_exactly(sequence):
    model = SoilModel()
    start_soil, start_canopy = model.soil.copy(), model.canopy.copy()
    totals = {k: np.zeros(len(BLOCKS)) for k in ("in", "runoff", "et", "evap", "drain")}
    for h in sequence:
        irrigation = np.full(len(BLOCKS), 2.0)
        out = model.step(**h, irrigation=irrigation)
        totals["in"] += h["rain"] + irrigation
        totals["runoff"] += out["runoff_mm"]
        totals["et"] += out["et_soil_mm"]
        totals["evap"] += out["canopy_evap_mm"]
        totals["drain"] += out["drainage_mm"]
    stored = (model.soil - start_soil) + (model.canopy - start_canopy)
    outflow = totals["runoff"] + totals["et"] + totals["evap"] + totals["drain"]
    np.testing.assert_allclose(totals["in"], stored + outflow, atol=1e-6)


@settings(max_examples=60, deadline=None)
@given(sequence=weather_sequences)
def test_state_stays_within_physical_bounds(sequence):
    model = SoilModel()
    for h in sequence:
        out = model.step(**h)
        assert (model.soil >= model.s_wp - 1e-9).all()  # never below wilting point
        assert (model.soil <= model.s_sat + 1e-9).all()  # never above saturation
        assert (model.canopy >= 0).all() and (model.canopy <= model.canopy_capacity + 1e-9).all()
        assert ((out["water_stress"] >= 0) & (out["water_stress"] <= 1)).all()
        assert ((out["leaf_wetness"] >= 0) & (out["leaf_wetness"] <= 100)).all()
        assert ((out["relative_humidity"] >= 0) & (out["relative_humidity"] <= 100)).all()
        for flux in ("runoff_mm", "infiltration_mm", "et_soil_mm", "drainage_mm"):
            assert (out[flux] >= -1e-12).all()


def test_no_evaporation_without_sunlight():
    assert priestley_taylor_mm_h(np.array(25.0), np.array(0.0), np.array(870.0)) == 0
    model = SoilModel()
    out = model.step(rain=0, air_temp=18, rh=90, solar=0, pressure=870)
    assert (out["et_soil_mm"] == 0).all() and (out["canopy_evap_mm"] == 0).all()


def test_shade_catches_more_rain():
    shaded = replace(BLOCKS[7], shade_pct=100)
    open_sun = replace(BLOCKS[7], shade_pct=0)
    out = SoilModel([shaded, open_sun]).step(rain=5, air_temp=22, rh=95, solar=0, pressure=870)
    assert out["interception_mm"][0] > out["interception_mm"][1]


def test_steep_slopes_shed_more_water():
    gentle = replace(BLOCKS[2], slope="gentle")
    steep = replace(BLOCKS[2], slope="steep")
    out = SoilModel([gentle, steep]).step(rain=40, air_temp=21, rh=98, solar=50, pressure=870)
    assert out["runoff_mm"][1] > out["runoff_mm"][0]


def test_cloudburst_cannot_overfill_the_bucket():
    model = SoilModel()
    for _ in range(240):  # ten days of 100 mm/h -- long enough to reach steady state
        model.step(rain=100, air_temp=20, rh=100, solar=0, pressure=870)
    assert (model.soil <= model.s_sat + 1e-9).all()
    # Water drains out the bottom while it soaks in at the top, so a free-draining soil
    # settles where the two balance, not necessarily at saturation. Within an hour,
    # infiltration (cap) comes first, then a fraction r of the water above field
    # capacity drains. Steady state S = (S + cap) - r(S + cap - FC) gives
    #   S = FC + cap (1/r - 1)            if infiltration is the limit, or
    #   S = sat - r (sat - FC)            if the bucket tops up to saturation first.
    fc, sat, cap, r = model.s_fc, model.s_sat, model.infiltration_cap, model.drain_rate
    equilibrium = np.minimum(fc + cap * (1 / r - 1), sat - r * (sat - fc))
    np.testing.assert_allclose(model.soil, equilibrium, rtol=1e-3)
    assert (model.soil > model.s_fc).all()


def test_dry_sunny_weeks_dry_the_soil_towards_wilting_point():
    model = SoilModel()
    levels = []
    for hour in range(24 * 60):  # 60 rainless days
        solar = max(0.0, 900 * np.sin(np.pi * ((hour % 24) - 6) / 12))
        model.step(rain=0, air_temp=26, rh=50, solar=solar, pressure=870)
        levels.append(model.soil.copy())
    levels = np.array(levels)
    assert (np.diff(levels, axis=0) <= 1e-12).all()  # only ever goes down
    stress = model._water_stress()
    assert (stress < 0.5).any()  # shallow soils are badly stressed after two dry months


def test_lapse_rate_makes_high_blocks_cooler():
    out = SoilModel().step(rain=0, air_temp=22, rh=70, solar=0, pressure=870)
    b1, b9 = BLOCKS[0], BLOCKS[8]
    assert b9.elevation_min_m > b1.elevation_min_m
    assert out["air_temperature"][8] < out["air_temperature"][0]


@pytest.mark.parametrize("dataset", ["weather_hourly", "block_hourly"])
def test_engine_writes_both_datasets(tmp_path, dataset):
    store = engine.run(seed=3, start=date(2026, 3, 1), days=10, root=tmp_path)
    df = store.read(dataset)
    expected_rows = 10 * 24 * (len(BLOCKS) if dataset == "block_hourly" else 1)
    assert len(df) == expected_rows
    assert dataset in store.read_info().datasets
