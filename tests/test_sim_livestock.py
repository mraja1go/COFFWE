"""Livestock tests: starting state from history, lactation, breeding biology, health
schedule, apiary invariants, and the Postgres write (coffwe_test)."""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import func, select

from coffwe.sim import engine
from coffwe.sim.livestock import (
    BREEDS,
    DRY_OFF_BEFORE_CALVING_DAYS,
    GESTATION_DAYS,
    HARVEST_MIN_GAP_DAYS,
    HEIFER_BREEDING_AGE_DAYS,
    MAX_FRAMES,
    MIN_VACCINATION_AGE_DAYS,
    POSTPARTUM_WAIT_DAYS,
    PREGNANCY_CHECK_AFTER_DAYS,
    SUPER_CAPACITY_KG,
    Animal,
    BeeModel,
    CattleModel,
)
from coffwe.sim.rng import stream

START = engine.EVENTS_START
DAYS = (date(2027, 4, 1) - START).days


def simulate_cattle(seed: int) -> CattleModel:
    model = CattleModel(stream(seed, "cattle"), START)
    for i in range(DAYS):
        model.on_day(START + timedelta(days=i))
    return model


@pytest.fixture(scope="module", params=[1, 2, 3, 42])
def herd(request):
    return simulate_cattle(request.param)


# --- starting state ----------------------------------------------------------------


def test_starting_state_matches_the_recorded_history():
    m = CattleModel(stream(0, "cattle"), START)
    lactating = {a.code for a in m.herd.values() if a.lactating}
    pregnant = {a.code for a in m.herd.values() if a.pregnant}
    assert lactating == {"C02", "C04", "C06"}  # Kaveri, Tunga, Gowri
    assert pregnant == {"C03", "C05"}  # Ganga, Lakshmi
    assert m.herd["C05"].due_on == date(2026, 3, 5) + timedelta(days=GESTATION_DAYS)
    assert m.herd["C06"].weight_kg == 325  # 1 Nov checkup


# --- lactation ---------------------------------------------------------------------


@pytest.mark.parametrize(("breed", "peak"), [("malnad_gidda", 3.0), ("jersey_cross", 11.0)])
def test_wood_curve_peaks_where_the_breed_should(breed, peak):
    cow = Animal("X", "X", "female", breed, date(2020, 1, 1), None, None, 200,
                 last_calved_on=date(2026, 1, 1))  # fmt: skip
    curve = np.array([cow.wood_yield(date(2026, 1, 1) + timedelta(days=t)) for t in range(1, 301)])
    p = BREEDS[breed]
    assert abs(int(curve.argmax()) + 1 - p.wood_b / p.wood_c) <= 1  # peak at day b/c
    assert curve.max() == pytest.approx(peak, rel=0.02)
    assert (curve > 0).all()


def test_only_lactating_females_are_milked_twice_a_day(herd):
    m = pd.DataFrame(herd.milkings)
    assert set(m["cattle_code"]) <= {a.code for a in herd.herd.values() if a.sex == "female"}
    per_day = m.groupby(["day", "cattle_code"])["session"].agg(sorted)
    assert per_day.map(lambda s: s == ["evening", "morning"]).all()
    assert (m["litres"] > 0).all()
    by_session = m.groupby("session")["litres"].mean()
    assert by_session["morning"] > by_session["evening"]


def test_breeds_yield_and_fat_differ_as_expected(herd):
    m = pd.DataFrame(herd.milkings)
    m["breed"] = m["cattle_code"].map(lambda c: herd.herd[c].breed)
    daily = m.groupby(["breed", "cattle_code", "day"])["litres"].sum().groupby("breed").mean()
    assert 1.0 < daily["malnad_gidda"] < 3.5
    assert 6.0 < daily["jersey_cross"] < 13.0
    fat = m.groupby("breed")["fat_pct"].mean()
    assert fat["malnad_gidda"] > fat["jersey_cross"]


# --- breeding ----------------------------------------------------------------------


def test_matings_respect_age_postpartum_and_pregnancy_rules(herd):
    b = pd.DataFrame(herd.breeding)
    for _, ev in b[b["event_type"] == "mating"].iterrows():
        cow = herd.herd[ev["cow_code"]]
        assert cow.sex == "female" and ev["sire_code"] == "C01"
        calvings = b[(b["cow_code"] == cow.code) & (b["event_type"] == "calving")
                     & (b["day"] <= ev["day"])]  # fmt: skip
        last_calved = calvings["day"].max() if len(calvings) else cow.last_calved_on
        if last_calved and last_calved <= ev["day"]:
            assert (ev["day"] - last_calved).days >= POSTPARTUM_WAIT_DAYS
        else:
            assert (ev["day"] - cow.born_on).days >= HEIFER_BREEDING_AGE_DAYS


def test_pregnancy_checks_follow_the_last_service(herd):
    b = pd.DataFrame(herd.breeding)
    for _, chk in b[b["event_type"] == "pregnancy_check"].iterrows():
        services = b[(b["cow_code"] == chk["cow_code"]) & (b["event_type"] == "mating")
                     & (b["day"] < chk["day"])]  # fmt: skip
        gap = (chk["day"] - services["day"].max()).days
        assert gap == PREGNANCY_CHECK_AFTER_DAYS


def test_calves_have_correct_parents_and_a_calving_record(herd):
    b = pd.DataFrame(herd.breeding)
    for calf in (a for a in herd.herd.values() if a.is_new):
        dam = herd.herd[calf.dam_code]
        assert dam.sex == "female" and calf.sire_code == "C01" and calf.breed == dam.breed
        ev = b[(b["event_type"] == "calving") & (b["calf_code"] == calf.code)]
        assert len(ev) == 1 and ev.iloc[0]["cow_code"] == dam.code
        assert ev.iloc[0]["day"] == calf.born_on


def test_cows_are_dried_off_before_calving(herd):
    b = pd.DataFrame(herd.breeding)
    for _, calving in b[b["event_type"] == "calving"].iterrows():
        cow = calving["cow_code"]
        milked = [m["day"] for m in herd.milkings if m["cattle_code"] == cow
                  and m["day"] < calving["day"]]  # fmt: skip
        if milked:
            assert (calving["day"] - max(milked)).days >= DRY_OFF_BEFORE_CALVING_DAYS - 1


def test_conception_per_service_matches_breed_rates():
    # Open cows return to heat at ~21 days and are re-served, so by the 60-day check
    # nearly every checked cow *is* pregnant. The honest measure is per service: of
    # all matings, how many led to a pregnancy (the next breeding event for that cow
    # is a positive check rather than another heat/mating).
    outcomes = []
    for seed in range(30):
        b = pd.DataFrame(simulate_cattle(seed).breeding).sort_values("day", kind="stable")
        for _, g in b.groupby("cow_code"):
            events = g[g["event_type"].isin(["mating", "pregnancy_check"])].reset_index()
            for i, ev in events.iterrows():
                if ev["event_type"] == "mating" and i + 1 < len(events):
                    nxt = events.iloc[i + 1]
                    outcomes.append(
                        nxt["event_type"] == "pregnancy_check" and bool(nxt["pregnant"])
                    )
    rate = np.mean(outcomes)
    assert 0.38 < rate < 0.62, rate  # breed conception rates are 0.45 (JX) and 0.55 (MG)


# --- health ------------------------------------------------------------------------


def test_vaccinations_only_for_old_enough_animals_and_herd_gets_fmd(herd):
    h = pd.DataFrame(herd.health)
    vax = h[h["event_type"] == "vaccination"]
    for _, v in vax.iterrows():
        assert (v["day"] - herd.herd[v["cattle_code"]].born_on).days >= MIN_VACCINATION_AGE_DAYS
    fmd = vax[vax["subject"].str.startswith("Foot-and-mouth")]
    assert date(2027, 3, 10) in set(fmd["day"])  # the herd's booster date from history


def test_monthly_checkups_weigh_every_animal_over_a_month_old(herd):
    h = pd.DataFrame(herd.health)
    feb = h[(h["day"] == date(2027, 2, 1)) & (h["subject"] == "Monthly herd checkup")]
    old_enough = {a.code for a in herd.herd.values() if (date(2027, 2, 1) - a.born_on).days >= 30}
    assert set(feb["cattle_code"]) == old_enough
    assert feb["body_condition_score"].between(1, 5).all()


def test_mastitis_rate_matches_its_parameters():
    cases, cow_days = 0, 0
    for seed in range(40):
        m = CattleModel(stream(seed, "cattle"), START)
        for i in range(DAYS):
            cow_days += sum(a.lactating for a in m.herd.values())
            m.on_day(START + timedelta(days=i))
        cases += sum(1 for h in m.health if str(h["subject"]).startswith("Clinical mastitis")
                     and h["event_type"] == "illness")  # fmt: skip
    rate = cases / cow_days
    assert 0.0002 < rate < 0.002  # between the Malnad Gidda and Jersey-cross daily risks


# --- bees --------------------------------------------------------------------------


@pytest.fixture(scope="module", params=[1, 7, 42])
def apiary(request):
    bees = BeeModel(stream(request.param, "bees"), START)
    rng = stream(request.param, "test-weather")
    for i in range(DAYS):
        day = START + timedelta(days=i)
        rain = float(rng.gamma(0.3, 10)) if rng.random() < 0.15 else 0.0
        bloom = 1.0 if date(2027, 3, 1) <= day <= date(2027, 3, 4) else 0.0
        bees.on_day(day, rain, bloom, recent_rain_mm=rain * 3)
    return bees


def test_colony_state_stays_physical(apiary):
    d = pd.DataFrame(apiary.daily)
    assert d["strength_frames"].between(0.5, MAX_FRAMES).all()
    assert d["stores_kg"].between(0, SUPER_CAPACITY_KG).all()


def test_at_most_one_living_colony_per_box_at_any_time(apiary):
    for hive in apiary.hives:
        own = sorted(
            (c for c in apiary.colonies if c.hive_code == hive), key=lambda c: c.established_on
        )
        for a, b in zip(own, own[1:], strict=False):
            assert a.ended_on is not None and a.ended_on <= b.established_on


def test_honey_harvests_are_sane(apiary):
    h = pd.DataFrame(apiary.harvests)
    assert len(h) > 0
    assert (h["honey_kg"] > 0).all()
    assert h["frames_extracted"].between(1, 8).all()
    assert h["moisture_pct"].between(10, 30).all()
    for _, g in h.groupby("hive_code"):
        assert (
            g["day"].sort_values().diff().dropna().map(lambda x: x.days) >= HARVEST_MIN_GAP_DAYS
        ).all()
    per_hive_season = h.groupby("hive_code")["honey_kg"].sum()
    assert (per_hive_season < 8).all()  # Apis cerana, one main flow in this window


def test_queens_reign_contiguously(apiary):
    for c in apiary.colonies:
        reigns = [*c.old_queens, (c.queen_origin, c.queen_since, None)]
        for (_, _, until), (_, since, _) in zip(reigns, reigns[1:], strict=False):
            assert until == since


# --- Postgres ----------------------------------------------------------------------


def test_livestock_events_land_in_postgres(db_run, test_db):
    from coffwe.db.models import BeeColony, Cattle, HoneyHarvest, Milking, Queen

    counts = db_run.db_counts
    with test_db() as s:
        assert s.scalar(select(func.count()).select_from(Milking)) == counts["milkings"]
        assert s.scalar(select(func.count()).select_from(Cattle)) == 12 + counts["new_calves"]
        new = s.scalars(select(Cattle).where(Cattle.born_on >= START)).all()
        assert all(c.dam is not None and c.sire.code == "C01" for c in new)
        assert (
            s.scalar(select(func.count()).select_from(HoneyHarvest))
            == 94 + counts["honey_harvests"]
        )
        living = s.scalar(
            select(func.count()).select_from(BeeColony).where(BeeColony.ended_on.is_(None))
        )
        reigning = s.scalar(
            select(func.count()).select_from(Queen).where(Queen.replaced_on.is_(None))
        )
        # every living colony has exactly one reigning queen (ended ones may keep theirs)
        assert reigning >= living
