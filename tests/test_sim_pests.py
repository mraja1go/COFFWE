"""Pest & disease tests: biology laws for berry borer and leaf rust, IPM behaviour,
and the managed-vs-unmanaged counterfactual."""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from coffwe.estate_data import BLOCKS
from coffwe.sim import engine
from coffwe.sim.coffee import CoffeeModel
from coffwe.sim.management import CBB_THRESHOLD, SPRAY_MAX_RAIN_MM
from coffwe.sim.pests import (
    CBB_PRESSURE_FLOOR,
    PestModel,
    latent_days,
    rust_infection_potential,
    rust_temperature_weight,
)
from coffwe.sim.rng import stream

N = len(BLOCKS)


class HostCoffee(CoffeeModel):
    """A coffee model whose borer-host supply is fixed, to isolate pest behaviour."""

    def __init__(self, host_kg_per_plant: float) -> None:
        super().__init__(stream(0, "coffee"))
        self.host = host_kg_per_plant

    def susceptible_kg(self, code: str) -> float:
        return self.host * self.unit(code).plants


def step(pests, coffee, day, tmean=21.0, wet_hours=0.0, rain=0.0):
    pests.on_day(
        day, coffee, tmean_c=np.full(N, tmean), rust_wet_hours=np.full(N, wet_hours),
        rain_mm=rain, fallen_kg={}, picked_kg={},
    )  # fmt: skip


def days(n, start=date(2026, 7, 1)):
    return [start + timedelta(days=i) for i in range(n)]


# --- leaf rust ---------------------------------------------------------------------


def test_rust_needs_wet_leaves_at_the_right_temperature():
    assert rust_infection_potential(0) == 0 and rust_infection_potential(3.9) == 0
    assert rust_infection_potential(24) == 1
    w = rust_temperature_weight(np.array([10.0, 15.0, 22.0, 29.0, 35.0]))
    assert w[2] == 1 and w[0] == w[1] == w[3] == w[4] == 0


def test_latent_period_is_longer_in_the_cold():
    assert latent_days(16) > latent_days(22) > latent_days(26)
    assert 20 <= latent_days(5) <= 50 and 20 <= latent_days(40) <= 50


def test_no_new_rust_without_wet_hours():
    coffee = HostCoffee(0.0)
    pests = PestModel(stream(1, "pests"), coffee)
    for d in days(120):
        step(pests, coffee, d, wet_hours=0.0)
    assert all(p.latent == [] for p in pests.units)
    assert all(p.rust_incidence < 0.04 for p in pests.units)  # only the old lesions, dying out


def test_wet_season_builds_an_epidemic_on_arabica_not_robusta():
    coffee = HostCoffee(0.0)
    pests = PestModel(stream(1, "pests"), coffee)
    for d in days(150):
        step(pests, coffee, d, tmean=21, wet_hours=20)
    ar = np.mean([p.rust_incidence for p in pests.units if p.variety == "AR"])
    rb = np.mean([p.rust_incidence for p in pests.units if p.variety == "RB"])
    assert ar > 0.2 and rb < ar / 5


def test_bordeaux_protects_leaves():
    results = {}
    for sprayed in (False, True):
        coffee = HostCoffee(0.0)
        pests = PestModel(stream(2, "pests"), coffee)
        for d in days(120):
            if sprayed and (d - date(2026, 7, 1)).days % 25 == 0:
                pests.spray_bordeaux("B8")
            step(pests, coffee, d, wet_hours=20)
        results[sprayed] = pests.unit("B8-AR").rust_incidence
    # copper wears off between sprays and existing lesions keep spreading: ~half the rust
    assert results[True] < 0.7 * results[False]


@settings(max_examples=30, deadline=None)
@given(weather=st.lists(st.tuples(st.floats(10, 30), st.floats(0, 24), st.floats(0, 80)),
                        min_size=1, max_size=150))  # fmt: skip
def test_pest_state_stays_physical(weather):
    coffee = HostCoffee(0.25)
    pests = PestModel(stream(3, "pests"), coffee)
    for d, (t, wet, rain) in zip(days(len(weather)), weather, strict=True):
        step(pests, coffee, d, tmean=t, wet_hours=wet, rain=rain)
        for p in pests.units:
            assert 0 <= p.rust_incidence <= 1 and p.defoliated >= 0
            assert p.rust_incidence + p.defoliated <= 1 + 1e-9
            assert 0.3 <= p.leaf_health <= 1 and 0 <= p.protection <= 1
            assert p.pressure >= CBB_PRESSURE_FLOOR and 0 <= p.infestation < 1
            assert p.reservoir_kg >= 0


# --- berry borer -------------------------------------------------------------------


def test_borers_multiply_only_where_there_are_host_berries():
    final = {}
    for host in (0.0, 0.3):
        coffee = HostCoffee(host)
        pests = PestModel(stream(4, "pests"), coffee)
        for d in days(120):
            step(pests, coffee, d, tmean=22)
        final[host] = np.mean([p.pressure for p in pests.units])
    assert final[0.3] > 10 * final[0.0]
    assert final[0.0] == pytest.approx(CBB_PRESSURE_FLOOR)  # starved down to immigration


def test_warmth_speeds_borer_generations():
    final = {}
    for t in (17.0, 24.0):
        coffee = HostCoffee(0.3)
        pests = PestModel(stream(5, "pests"), coffee)
        for d in days(90):
            step(pests, coffee, d, tmean=t)
        final[t] = np.mean([p.pressure for p in pests.units])
    assert final[24.0] > final[17.0]


def test_beauveria_and_gleaning_knock_borers_back():
    coffee = HostCoffee(0.3)
    sprayed, control = PestModel(stream(6, "pests"), coffee), PestModel(stream(6, "pests"), coffee)
    for d in days(60):
        if d.day in (1, 21):
            sprayed.spray_beauveria("B2", d)
        step(sprayed, coffee, d)
        step(control, coffee, d)
    assert sprayed.unit("B2-RB").pressure < 0.6 * control.unit("B2-RB").pressure
    u = control.unit("B2-RB")
    u.reservoir_kg, before = 500.0, u.pressure
    control.glean("B2", 2027)
    assert u.reservoir_kg == pytest.approx(100.0) and u.pressure < before


def test_more_borers_more_trap_catches():
    catches = {}
    for pressure in (0.02, 0.2):
        coffee = HostCoffee(0.0)
        pests = PestModel(stream(7, "pests"), coffee)
        for d in days(28):
            for p in pests.units:
                p.pressure = pressure
            step(pests, coffee, d)
        catches[pressure] = sum(t["catch"] for t in pests.trap_counts)
    assert catches[0.2] > 5 * catches[0.02]
    assert all(t["day"].weekday() == 5 for t in pests.trap_counts)  # counted on Saturdays


# --- the whole estate, with and without pest management ----------------------------


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    root = tmp_path_factory.mktemp("pests")
    return {ipm: engine.run(seed=42, root=root, ipm=ipm) for ipm in (True, False)}


def test_ipm_cuts_borer_losses_and_rust(runs):
    loss = {k: s.read("coffee_daily")["borer_loss_kg"].sum() for k, s in runs.items()}
    assert loss[True] < 0.4 * loss[False]
    peak = {}
    for k, s in runs.items():
        p = s.read("pest_daily")
        peak[k] = p[p["variety"] == "AR"].groupby(level=0)["rust_incidence"].mean().max()
    assert peak[True] < peak[False]


def test_unmanaged_estate_gets_realistically_bad(runs):
    p = runs[False].read("pest_daily")
    ar = p[p["variety"] == "AR"]
    harvest = ar[(ar.index >= "2027-01-01") & (ar.index < "2027-03-01")]
    assert harvest["cbb_infestation"].mean() > 0.15  # neglected estates: 15-40% bored
    rust = ar.groupby(ar.index.month)["rust_incidence"].mean()
    assert rust.idxmax() in (9, 10, 11)  # rust peaks after the monsoon
    assert rust.loc[[9, 10, 11]].max() > 0.15  # ~15-40% depending on the monsoon


def test_managed_estate_holds_borers_near_the_threshold(runs):
    p = runs[True].read("pest_daily")
    season = p[(p.index >= "2026-11-01") & (p.index < "2027-03-01")]
    assert season["cbb_infestation"].mean() < 2 * CBB_THRESHOLD


def test_ipm_actions_respect_their_rules_and_draw_on_inventory(runs):
    store = runs[True]
    log = store.read("ipm_log").reset_index()
    weather = store.read("weather_hourly")
    rain = weather["rainfall"].groupby(weather.index.date).sum()
    sprays = log[log["action"].isin(["beauveria", "bordeaux"])]
    assert len(sprays) > 0
    for _, s in sprays.iterrows():
        assert rain[s["day"].date()] < SPRAY_MAX_RAIN_MM  # never on a wet day
        assert s["day"].weekday() == 5  # decided on the Saturday scouting round
    bord = log[log["action"] == "bordeaux"]
    assert set(bord["block_code"]) <= {b.code for b in BLOCKS if "AR" in b.plantings}
    for _, g in sprays.groupby(["block_code", "action"]):
        gaps = g["day"].sort_values().diff().dropna().dt.days
        assert (gaps >= 21).all()
    # The ledger (like Postgres) only starts on EVENTS_START: actions before it -- e.g.
    # the monsoon Bordeaux sprays -- happened, but aren't stock movements.
    moves = store.read("inventory_movements")
    used = moves[moves["reason"] == "used"].groupby("item_code")["quantity"].sum()
    recorded = log[log["day"].dt.date >= engine.EVENTS_START]
    needs = {"CBB-LURE": True,
             "BEAUVERIA": (recorded["action"] == "beauveria").any(),
             "CU-SULPHATE": (recorded["action"] == "bordeaux").any(),
             "LIME": (recorded["action"] == "bordeaux").any()}  # fmt: skip
    for item, expected in needs.items():
        assert (used.get(item, 0) < 0) == expected, item


def test_no_ipm_means_no_actions_and_no_traps(runs):
    info = runs[False].read_info()
    assert "ipm_log" not in info.datasets and "trap_counts" not in info.datasets
    moves = runs[False].read("inventory_movements")
    assert not moves["item_code"].isin(["BEAUVERIA", "CU-SULPHATE", "CBB-LURE"]).any()
    assert len(pd.unique(moves["item_code"])) > 0  # harvest still uses diesel and bags
