"""The estate data must match docs/estate-concept.md exactly. No database needed."""

from collections import Counter
from datetime import date, timedelta

import pytest

from coffwe.estate_data import (
    ASSETS,
    BLOCKS,
    CATTLE,
    FOREST_WEIGHTS,
    HARVEST_PLAN,
    INVENTORY_ITEMS,
    LIVESTOCK,
    MEASUREMENTS,
    NECTAR_FLOWS,
    SHADE_WEIGHTS,
    SITES,
    SPECIES,
    STATIONS,
    apportion,
    block_species_counts,
    cattle_breeding_specs,
    cattle_health_specs,
    colony_alive_on,
    colony_specs,
    hive_specs,
    honey_harvest_specs,
    make_tag,
    plant_specs,
    queen_specs,
    worker_specs,
)

SPECIES_CODES = {s.code for s in SPECIES}
SHADE_CODES = {s.code for s in SPECIES if s.category == "shade"}


def test_ten_blocks_totalling_70_acres():
    assert [b.code for b in BLOCKS] == [f"B{i}" for i in range(1, 11)]
    assert sum(b.area_acres for b in BLOCKS) == 70


def test_species_codes_unique_and_complete():
    assert len(SPECIES_CODES) == len(SPECIES) == 25  # 2 coffee + 2 intercrop + 21 shade
    assert len(SHADE_CODES) == 21  # silver oak + 20 natives


def test_weights_only_reference_known_shade_species():
    assert set(SHADE_WEIGHTS) == SHADE_CODES
    assert set(FOREST_WEIGHTS) <= SHADE_CODES
    assert "SO" not in FOREST_WEIGHTS  # no non-native silver oak in the forest buffer


def test_crop_totals_match_concept():
    totals = Counter()
    for block in BLOCKS:
        totals.update(block.plantings)
    assert totals == {"AR": 15_050, "RB": 15_050, "PP": 8_690, "AN": 1_360}
    assert sum(b.shade_trees for b in BLOCKS) == 4_800


@pytest.mark.parametrize("block", BLOCKS, ids=lambda b: b.code)
def test_block_shade_trees_split_exactly(block):
    counts = block_species_counts(block)
    shade_total = sum(n for code, n in counts.items() if code in SHADE_CODES)
    assert shade_total == block.shade_trees


def test_apportion_sums_exactly_and_is_deterministic():
    assert sum(apportion(281, SHADE_WEIGHTS).values()) == 281
    assert apportion(281, SHADE_WEIGHTS) == apportion(281, SHADE_WEIGHTS)
    assert apportion(10, {"a": 1, "b": 1, "c": 1}) == {"a": 4, "b": 3, "c": 3}


def test_tag_format():
    assert make_tag("B3", "RB", 147) == "B3-RB-00147"


def test_44950_unique_tags():
    tags = [tag for *_, tag in plant_specs()]
    assert len(tags) == 44_950
    assert len(set(tags)) == len(tags)


# --- Resources & flows -------------------------------------------------------------

SITE_CODES = {s.code for s in SITES}


def _unique(codes):
    codes = list(codes)
    return len(codes) == len(set(codes))


def test_every_block_has_exactly_one_site():
    block_sites = [s.block_code for s in SITES if s.kind == "block"]
    assert sorted(block_sites) == sorted(b.code for b in BLOCKS)
    assert all(s.block_code is None for s in SITES if s.kind != "block")


def test_all_codes_unique():
    assert _unique(s.code for s in SITES)
    assert _unique(a.code for a in ASSETS)
    assert _unique(st.code for st in STATIONS)
    assert _unique(i.code for i in INVENTORY_ITEMS)
    assert _unique(lv.code for lv in LIVESTOCK)


def test_everything_points_at_a_real_site():
    referenced = (
        {a.site_code for a in ASSETS}
        | {st.site_code for st in STATIONS}
        | {lv.site_code for lv in LIVESTOCK}
        | {i.site_code for i in INVENTORY_ITEMS}
    )
    assert referenced <= SITE_CODES


def test_lab_is_the_all_purpose_estate_lab():
    lab = next(s for s in SITES if s.code == "LAB")
    assert lab.name == "Estate Lab"
    assert "fertilizer" in lab.description and "research" in lab.description


def test_workforce_15_permanent_plus_seasonal():
    workers = worker_specs()
    permanent = [w for w in workers if w.employment_type == "permanent"]
    seasonal = [w for w in workers if w.employment_type == "seasonal"]
    assert len(permanent) == 15
    assert len(seasonal) == 45
    assert _unique(w.code for w in workers)
    assert _unique(w.full_name for w in workers)
    # Mirrors the DB constraint: seasonal <=> has a contract end date
    assert all(
        (w.employment_type == "seasonal") == (w.contract_ends_on is not None) for w in workers
    )
    assert sum(w.role == "manager" for w in workers) == 1


def test_every_block_has_a_field_station_and_valve():
    for b in BLOCKS:
        assert any(st.code == f"FS-{b.code}" and st.site_code == b.code for st in STATIONS)
        assert any(a.code == f"VALVE-{b.code}" and a.actuatable for a in ASSETS)


def test_dryer_has_rain_sensor_and_actuatable_curtain():
    # The concept's first closed-loop actuation (Phase 8) needs both ends present.
    dry = next(st for st in STATIONS if st.code == "DRY-01")
    assert "rain_detected" in dry.measurements
    assert next(a for a in ASSETS if a.code == "POLY-01-CURTAIN").actuatable


def test_station_measurements_are_defined_with_sane_ranges():
    for st in STATIONS:
        assert st.measurements and _unique(st.measurements)
        for m in st.measurements:
            unit, lo, hi = MEASUREMENTS[m]
            assert lo is None or hi is None or lo < hi


def test_opening_balances_positive():
    assert all(i.opening_balance > 0 for i in INVENTORY_ITEMS)


# --- Livestock detail --------------------------------------------------------------

ANIMALS = {c.code: c for c in CATTLE}


def test_pedigree_is_biologically_sound():
    for calf in CATTLE:
        for parent_code, sex in ((calf.dam_code, "female"), (calf.sire_code, "male")):
            if parent_code is None:
                continue
            parent = ANIMALS[parent_code]
            assert parent.sex == sex, f"{calf.code}: {parent_code} has the wrong sex"
            # parents must be at least ~15 months old when the calf is conceived
            conceived = calf.born_on - timedelta(days=283)
            assert (conceived - parent.born_on).days > 450, f"{calf.code}: {parent_code} too young"


def test_parents_listed_before_offspring():
    seen = set()
    for c in CATTLE:
        assert c.dam_code in seen | {None} and c.sire_code in seen | {None}
        seen.add(c.code)


def test_one_estate_bull_sires_every_recorded_calf():
    bulls = [c for c in CATTLE if c.sex == "male" and c.notes == "Estate breeding bull"]
    assert [b.code for b in bulls] == ["C01"]
    assert {c.sire_code for c in CATTLE if c.sire_code} == {"C01"}


def test_every_calf_has_a_matching_calving_event():
    calvings = {e.calf_code: e for e in cattle_breeding_specs() if e.event_type == "calving"}
    for calf in CATTLE:
        if calf.dam_code:
            event = calvings[calf.code]
            assert event.cow_code == calf.dam_code and event.occurred_on == calf.born_on


def test_breeding_events_satisfy_db_rules():
    for e in cattle_breeding_specs():
        assert (e.event_type == "mating") == (e.sire_code is not None)
        assert (e.event_type == "calving") == (e.calf_code is not None)
        assert (e.event_type == "pregnancy_check") == (e.pregnant is not None)
        assert ANIMALS[e.cow_code].sex == "female"


def test_health_events_after_birth_and_vaccines_age_appropriate():
    for h in cattle_health_specs():
        animal = ANIMALS[h.cattle_code]
        assert h.occurred_on >= animal.born_on
        if h.event_type == "vaccination":
            assert (h.occurred_on - animal.born_on).days >= 120  # no vaccinating newborns
    checked = {h.cattle_code for h in cattle_health_specs() if h.weight_kg}
    assert checked == set(ANIMALS)  # every animal weighed at the Nov checkup


def test_at_most_one_living_colony_per_hive_at_any_time():
    colonies = colony_specs()
    for h in hive_specs():
        own = sorted((c for c in colonies if c.hive_code == h.code), key=lambda c: c.established_on)
        assert own[0].established_on >= h.installed_on
        for earlier, later in zip(own, own[1:], strict=False):
            assert earlier.ended_on is not None and earlier.ended_on <= later.established_on


def test_queen_reigns_are_contiguous_and_one_current_per_colony():
    queens = queen_specs()
    for c in colony_specs():
        reign = sorted(
            (q for q in queens
             if (q.hive_code, q.colony_established_on) == (c.hive_code, c.established_on)),
            key=lambda q: q.introduced_on,
        )  # fmt: skip
        assert reign[0].introduced_on == c.established_on
        for prev, nxt in zip(reign, reign[1:], strict=False):
            assert prev.replaced_on == nxt.introduced_on
        assert sum(q.replaced_on is None for q in reign) == 1


def test_empty_boxes_have_no_super():
    occupied = {c.hive_code for c in colony_specs() if c.ended_on is None}
    for h in hive_specs():
        assert (h.super_count > 0) == (h.code in occupied)


def test_nectar_flows_do_not_overlap():
    flows = sorted(NECTAR_FLOWS, key=lambda f: f.starts_on)
    for a, b in zip(flows, flows[1:], strict=False):
        assert a.ends_on < b.starts_on


def test_honey_harvests_fall_in_their_flow_from_a_living_colony():
    flows = {f.code: f for f in NECTAR_FLOWS}
    colonies = colony_specs()
    for h in honey_harvest_specs():
        assert flows[h.flow_code].starts_on <= h.harvested_on <= flows[h.flow_code].ends_on
        assert any(c.hive_code == h.hive_code and colony_alive_on(c, h.harvested_on)
                   for c in colonies)  # fmt: skip


def test_cerana_yields_are_realistic():
    # Apis cerana in Newton boxes: roughly 5-8 kg per hive per year
    season = [
        h for h in honey_harvest_specs() if date(2025, 10, 1) <= h.harvested_on < date(2026, 10, 1)
    ]
    per_hive: dict[str, float] = {}
    for h in season:
        per_hive[h.hive_code] = per_hive.get(h.hive_code, 0) + float(h.honey_kg)
    full_year = [kg for code, kg in per_hive.items()
                 if sum(x.hive_code == code for x in season) == len(HARVEST_PLAN) - 1]  # fmt: skip
    assert full_year and all(4 <= kg <= 8 for kg in full_year)
