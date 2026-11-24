"""The estate data must match docs/estate-concept.md exactly. No database needed."""

from collections import Counter

import pytest

from coffwe.estate_data import (
    ASSETS,
    BLOCKS,
    FOREST_WEIGHTS,
    INVENTORY_ITEMS,
    LIVESTOCK,
    MEASUREMENTS,
    SHADE_WEIGHTS,
    SITES,
    SPECIES,
    STATIONS,
    apportion,
    block_species_counts,
    make_tag,
    plant_specs,
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
