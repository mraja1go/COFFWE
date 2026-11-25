"""Machines and the organic loop: Weibull wear, breakdown consequences, maintenance,
dung -> biogas/vermicompost, feed, and post-harvest feeding."""

from datetime import date, timedelta

import numpy as np
import pytest
from scipy import stats

from coffwe.estate_data import ASSETS
from coffwe.sim.harvest import (
    CLOUDY_DAY_CREDIT,
    RAIN_DAY_CREDIT,
    Batch,
    HarvestModel,
    InventoryLedger,
)
from coffwe.sim.livestock import Animal
from coffwe.sim.machines import (
    PREVENTIVE_ASSETS,
    PREVENTIVE_SERVICE_DAY,
    MachineModel,
    WearModel,
    failure_probability,
    wear_model,
)
from coffwe.sim.organics import (
    DUNG_SHARE_OF_BODYWEIGHT,
    DUNG_TO_DIGESTER,
    SLURRY_L_PER_KG_DUNG,
    VERMI_DAYS,
    VERMI_YIELD,
    OrganicsModel,
    cattle_feed_kg,
)
from coffwe.sim.rng import stream

D0 = date(2026, 12, 1)


# --- Weibull wear ------------------------------------------------------------------


def test_daily_failure_chances_compound_to_the_weibull_survival_curve():
    m = WearModel("h", eta=100, k=2.0, repair_days=1)
    survive = 1.0
    for day in range(60):  # 2 h a day for 60 days
        survive *= 1 - failure_probability(m, 2 * day, 2)
    assert survive == pytest.approx(np.exp(-((120 / 100) ** 2)), rel=1e-9)


def test_simulated_lifetimes_are_weibull_distributed():
    """Run many fresh machines at 1 h/day until they fail; KS-test against Weibull."""
    m = WearModel("h", eta=80, k=2.2, repair_days=1)
    rng = stream(11, "test-weibull")
    lifetimes = []
    for _ in range(600):
        used = 0.0
        while rng.random() >= failure_probability(m, used, 1.0):
            used += 1.0
        lifetimes.append(used + 1.0)
    # Daily steps make lifetimes whole numbers; compare against the same discretisation.
    _, p = stats.kstest(np.array(lifetimes) - 0.5, "weibull_min", args=(m.k, 0, m.eta))
    assert p > 0.01


def test_wear_out_means_older_machines_fail_more():
    m = wear_model("PULPER-01")
    assert m.k > 1
    assert failure_probability(m, 60, 5) > failure_probability(m, 5, 5) > 0
    assert failure_probability(m, 100, 0) == 0


def test_every_asset_has_a_wear_model_and_valves_use_cycles():
    for a in ASSETS:
        assert wear_model(a.code).eta > 0
    assert wear_model("VALVE-B3").unit == "cycles"


def run_until_failure(machines: MachineModel, code: str, per_day: float, start=D0):
    day = start
    for _ in range(5000):
        machines.on_day(day, {code: per_day})
        if machines.machines[code].status == "faulty":
            return day
        day += timedelta(days=1)
        machines.capabilities(day)
    raise AssertionError("never failed")


def test_failure_takes_the_machine_out_then_repair_resets_wear():
    machines = MachineModel(stream(1, "machines"))
    failed_on = run_until_failure(machines, "PULPER-01", 8.0)
    assert not machines.capabilities(failed_on + timedelta(days=1))["pulping"]
    m = machines.machines["PULPER-01"]
    back = m.back_on
    machines.capabilities(back)
    assert m.status == "operational" and m.usage == 0
    kinds = [e["event_type"] for e in machines.events if e["asset_code"] == "PULPER-01"]
    assert kinds[-2:] == ["failure", "repair"]
    fail = next(e for e in machines.events if e["event_type"] == "failure")
    assert fail["usage_since_service"] > 0 and fail["usage_unit"] == "h"


def test_a_failed_valve_or_pump_stops_that_irrigation():
    machines = MachineModel(stream(2, "machines"))
    machines.machines["VALVE-B5"].status = "faulty"
    machines.machines["VALVE-B5"].back_on = D0 + timedelta(days=3)
    caps = machines.capabilities(D0)
    assert caps["irrigation"]["B5"] is False and caps["irrigation"]["B4"] is True
    machines.machines["PUMP-01"].status = "faulty"
    machines.machines["PUMP-01"].back_on = D0 + timedelta(days=3)
    assert not any(machines.capabilities(D0)["irrigation"].values())


def test_pre_season_service_resets_wear_on_key_machines():
    machines = MachineModel(stream(3, "machines"))
    service = date(2026, *PREVENTIVE_SERVICE_DAY)
    machines.on_day(service, {})
    for code in PREVENTIVE_ASSETS:
        assert machines.machines[code].status == "maintenance"
    machines.capabilities(service + timedelta(days=1))
    for code in PREVENTIVE_ASSETS:
        assert machines.machines[code].usage == 0
    assert all(e["event_type"] != "repair" for e in machines.events)  # service isn't a repair


# --- breakdowns change what happens to the coffee ----------------------------------


def washed_batch(day):
    return Batch("HB-X", "B8", "AR", day, 100.0, 99.0, 0.0, "washed", [("W020", 100.0)])


def test_pulper_breakdown_makes_washed_cherry_wait():
    h = HarvestModel(stream(4, "harvest"), ledger_from=date(2030, 1, 1))
    h.batches = [washed_batch(D0)]
    down = {"pulping": False, "rain_cover": True, "transport": True}
    for i in range(3):
        h.process_day(D0 + timedelta(days=i), 0, 200, down)
    assert h.batches[0].status == "received" and h.batches[0].pulped_on is None
    up = dict(down, pulping=True)
    activity = h.process_day(D0 + timedelta(days=3), 0, 200, up)
    assert h.batches[0].status == "processing" and activity["pulped_kg"] == 99.0


def test_without_rain_cover_wet_days_dry_nothing():
    for cover, credit in ((True, RAIN_DAY_CREDIT), (False, 0.0)):
        h = HarvestModel(stream(5, "harvest"), ledger_from=date(2030, 1, 1))
        b = washed_batch(D0)
        b.status, b.drying_started_on = "drying", D0
        h.batches = [b]
        h.process_day(D0, 30.0, 50, {"pulping": True, "rain_cover": cover, "transport": True})
        assert b.drying_credit == pytest.approx(credit)
    assert CLOUDY_DAY_CREDIT > 0


def test_no_pickup_no_dispatch():
    h = HarvestModel(stream(6, "harvest"), ledger_from=date(2030, 1, 1))
    b = washed_batch(D0)
    b.status, b.stored_on, b.output_kg = "stored", D0, 19.8
    h.batches = [b]
    h.process_day(date(2026, 12, 15), 0, 200, {"pulping": True, "rain_cover": True,
                                               "transport": False})  # fmt: skip
    assert b.status == "stored"
    h.process_day(date(2027, 1, 1), 0, 200, {"pulping": True, "rain_cover": True,
                                             "transport": True})  # fmt: skip
    assert b.status == "dispatched"


# --- organic loop ------------------------------------------------------------------


def cow(weight=300.0, lactating=True, breed="jersey_cross"):
    return Animal("C99", "Test", "female", breed, date(2019, 1, 1), None, None, weight,
                  last_calved_on=date(2026, 9, 1), lactating=lactating)  # fmt: skip


def test_dung_becomes_slurry_now_and_vermicompost_sixty_days_later():
    ledger = InventoryLedger()
    org = OrganicsModel(ledger, ledger_from=D0)
    herd = [cow(300.0)]
    dung = 300.0 * DUNG_SHARE_OF_BODYWEIGHT
    start_slurry, start_vermi = ledger.on_hand["BIOSLURRY"], ledger.on_hand["VERMICOMPOST"]
    produced = {"BIOSLURRY": 0.0, "VERMICOMPOST": 0.0}
    for i in range(VERMI_DAYS + 14):
        day = D0 + timedelta(days=i)
        org.on_day(day, herd, pulped_kg=0.0)
    for m in ledger.movements:
        if m.reason == "produced":
            produced[m.item_code] += m.quantity
    # Written up on Saturdays: everything from the start through the last Saturday.
    last_saturday = max(m.day for m in ledger.movements if m.item_code == "BIOSLURRY")
    days_covered = (last_saturday - D0).days + 1
    assert produced["BIOSLURRY"] == pytest.approx(
        dung * DUNG_TO_DIGESTER * SLURRY_L_PER_KG_DUNG * days_covered, rel=1e-3
    )
    first_vermi = min(m.day for m in ledger.movements if m.item_code == "VERMICOMPOST")
    assert (first_vermi - D0).days >= VERMI_DAYS
    assert produced["VERMICOMPOST"] > 0
    assert produced["VERMICOMPOST"] <= dung * (1 - DUNG_TO_DIGESTER) * VERMI_YIELD * 14 + 1e-6
    assert start_slurry >= 0 and start_vermi >= 0


def test_feed_rations_follow_breed_and_stage():
    day = date(2026, 12, 1)
    assert cattle_feed_kg(cow(lactating=True), day) > cattle_feed_kg(cow(lactating=False), day)
    assert cattle_feed_kg(cow(breed="jersey_cross"), day) > cattle_feed_kg(
        cow(breed="malnad_gidda"), day
    )


def test_feeding_never_runs_the_stores_negative():
    ledger = InventoryLedger()
    ledger.on_hand["CATTLE-FEED"] = 5.0
    org = OrganicsModel(ledger, ledger_from=D0)
    for i in range(30):
        org.on_day(D0 + timedelta(days=i), [cow() for _ in range(6)], pulped_kg=0.0)
    balance = 5.0
    for m in ledger.movements:
        if m.item_code == "CATTLE-FEED":
            balance += m.quantity
            assert balance >= -1e-9
