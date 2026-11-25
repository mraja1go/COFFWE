"""Phase 3 M1: sim clock, truth source, virtual devices, station links, wire schema,
and the new hydrology/wildlife truth."""

from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from coffwe.feed.clock import ManualClock, SimClock
from coffwe.feed.devices import (
    BUFFER_READINGS,
    DETECTOR_FALSE_ALARM,
    LINK_BAD_TO_GOOD,
    LINK_GOOD_TO_BAD,
    MODELS,
    measure,
    observe,
    station_link,
)
from coffwe.feed.schema import Reading, parse_reading, reading_topic
from coffwe.feed.truth import PER_HOUR, REPORT_MINUTES, ParquetTruthSource, SensorRef, all_sensors
from coffwe.sim import engine
from coffwe.sim.hydrology import RESERVOIR_CAPACITY_M3, RESERVOIR_SEEPAGE_PER_H
from coffwe.sim.wildlife import ELEPHANT_VISIT_PER_NIGHT, simulate_wildlife

TZ = "Asia/Kolkata"


# --- clock -------------------------------------------------------------------------


class FakeWall:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_clock_runs_at_speed_pauses_and_changes_speed_without_jumping():
    wall = FakeWall()
    start = datetime(2026, 11, 25, tzinfo=UTC)
    c = SimClock(start, speed=3600, wall=wall)
    wall.t = 2.0
    assert c.now() == start + timedelta(hours=2)
    c.pause()
    wall.t = 100.0
    assert c.now() == start + timedelta(hours=2)  # frozen
    c.resume()
    wall.t = 101.0
    assert c.now() == start + timedelta(hours=3)
    c.set_speed(60)  # re-anchors: no jump
    assert c.now() == start + timedelta(hours=3)
    wall.t = 102.0
    assert c.now() == start + timedelta(hours=3, minutes=1)
    assert c.wall_seconds_until(start + timedelta(hours=3, minutes=2)) == pytest.approx(1.0)


def test_clock_needs_an_aware_start_and_a_positive_speed():
    with pytest.raises(ValueError):
        SimClock(datetime(2026, 1, 1))
    with pytest.raises(ValueError):
        SimClock(datetime(2026, 1, 1, tzinfo=UTC)).set_speed(0)
    m = ManualClock(datetime(2026, 1, 1, tzinfo=UTC))
    assert m.advance(timedelta(minutes=15)) == m.now()


# --- truth source ------------------------------------------------------------------


@pytest.fixture(scope="module")
def run_dir(tmp_path_factory):
    return engine.run(
        seed=3, start=date(2026, 6, 15), days=40, root=tmp_path_factory.mktemp("feed")
    ).run_dir


@pytest.fixture(scope="module")
def source(run_dir):
    return ParquetTruthSource(run_dir, seed=3)


def test_every_station_sensor_is_served(source):
    sensors = source.sensors()
    assert len(sensors) == 65 and len({s.code for s in sensors}) == 65
    for s in sensors:
        series = source.series(s)
        assert len(series) == len(source.times) and not series.isna().any(), s.code
    assert (source.times[1] - source.times[0]) == pd.Timedelta(minutes=REPORT_MINUTES)


def test_amounts_are_split_not_invented(source):
    rain = next(s for s in source.sensors() if s.code == "WX-CORE-rainfall")
    quarter = source.series(rain)
    hourly = source._weather["rainfall"]
    np.testing.assert_allclose(quarter.resample("h").sum().to_numpy(), hourly.to_numpy(), atol=1e-9)
    motion = next(s for s in source.sensors() if s.code == "CORR-01-motion_events")
    q = source.series(motion)
    passes = source._wildlife[source._wildlife.station_code == "CORR-01"]["passes"]
    np.testing.assert_array_equal(q.resample("h").sum().to_numpy(), passes.to_numpy())


def test_state_variables_match_the_truth_on_the_hour(source):
    soil = next(s for s in source.sensors() if s.code == "FS-B5-soil_moisture")
    q = source.series(soil)
    truth = source._blocks[source._blocks.block_code == "B5"]["soil_moisture"]
    np.testing.assert_allclose(q.iloc[::PER_HOUR].to_numpy(), truth.to_numpy())


# --- sensors -----------------------------------------------------------------------


def constant_truth(value: float, days: int = 60) -> pd.Series:
    t = pd.date_range("2026-01-01", periods=days * 96, freq="15min", tz=TZ)
    return pd.Series(value, index=t)


def ref(measurement: str, unit: str = "x", lo=None, hi=None) -> SensorRef:
    return SensorRef(f"T-{measurement}", "T", "B1", measurement, unit, lo, hi)


def test_noise_and_calibration_look_like_the_spec_sheet():
    errors, biases = [], []
    for seed in range(30):
        r = measure(ref("air_temperature", "degC", -5, 50), constant_truth(20.0, 10), seed)
        ok = r[r.fault == ""]
        err = ok["value"] - ok["true_value"]
        biases.append(err.mean())
        errors.append((err - err.rolling(96, min_periods=1).mean()).std())
    m = MODELS["air_temperature"]
    assert np.std(biases) == pytest.approx(m.bias_sd, rel=0.4)  # calibration differs by unit
    assert np.mean(errors) == pytest.approx(m.noise_sd, rel=0.35)  # short-term scatter


def test_readings_land_on_the_resolution_grid():
    r = measure(ref("relative_humidity", "%", 0, 100), constant_truth(80.0), 1)
    assert np.allclose(r["value"] % 1.0, 0) or np.allclose((r["value"] * 10) % 10, 0)
    assert r["value"].between(0, 100).all() or (r["fault"] == "spike").any()


def test_drift_stays_bounded_over_years():
    m = MODELS["relative_humidity"]
    r = measure(ref("relative_humidity", "%", 0, 100), constant_truth(50.0, 3 * 365), 4)
    ok = r[r.fault == ""]
    excursion = (ok["value"] - 50).abs().max()
    assert excursion < 3 * m.drift_sd_per_day * np.sqrt(365) + 6 * m.bias_sd + 6 * m.noise_sd


def test_stuck_sensor_repeats_one_value_and_is_labelled():
    for seed in range(50):
        r = measure(ref("soil_moisture", "m3/m3", 0, 0.6), constant_truth(0.3, 90), seed)
        stuck = r[r.fault == "stuck"]
        if len(stuck):
            runs = (r.fault != r.fault.shift()).cumsum()[r.fault == "stuck"]
            assert stuck.groupby(runs)["value"].nunique().max() == 1
            return
    pytest.fail("no stuck episode in 50 seeds")


def test_tipping_bucket_conserves_rain_within_one_tip():
    t = constant_truth(0.0, 5)
    t.iloc[100:140] = 0.37  # a 40-reading shower
    r = measure(ref("rainfall", "mm", 0, 300), t, 2)
    gain_free = r["value"].sum() / max(t.sum(), 1e-9)
    assert 0.85 < gain_free < 1.15
    assert np.allclose(r["value"] % 0.2, 0, atol=1e-6) | np.allclose(
        r["value"] % 0.2, 0.2, atol=1e-6
    )


def test_rain_detector_false_alarm_rate():
    r = measure(ref("rain_detected", "bool", 0, 1), constant_truth(0.0, 200), 5)
    assert (r["value"] == 1).mean() == pytest.approx(DETECTOR_FALSE_ALARM, rel=0.3)
    assert (r.loc[r["value"] == 1, "fault"] == "false_alarm").all()


# --- station links -----------------------------------------------------------------


def link_for(kind: str, days=120, sun=220.0, seed=6):
    t = pd.date_range("2026-01-01", periods=days * 96, freq="15min", tz=TZ)
    solar = pd.Series(sun, index=sorted(set(t.date)))
    return station_link("FS-B1", kind, t, solar, np.zeros(len(t)), seed)


def test_link_outage_fraction_matches_the_markov_chain():
    lost_or_late = []
    for seed in range(8):
        lk = link_for("field", seed=seed)
        lost_or_late.append((lk["link_status"] != "ok").mean())
    stationary = LINK_GOOD_TO_BAD / (LINK_GOOD_TO_BAD + LINK_BAD_TO_GOOD)
    assert np.mean(lost_or_late) == pytest.approx(stationary, rel=0.35)


def test_store_and_forward_delivers_late_but_in_order_while_corridor_units_lose():
    field, corridor = link_for("field"), link_for("corridor")
    late = field[field["link_status"] == "late"]
    assert len(late) > 0 and (late["delivered_at"] > late["measured_at"]).all()
    assert (field.loc[field["link_status"] == "lost"].shape[0]) <= len(field) // BUFFER_READINGS
    assert (corridor["link_status"] == "late").sum() == 0
    assert (corridor["link_status"] == "lost").sum() > 0
    burst = late.sort_values("measured_at")
    assert burst["delivered_at"].is_monotonic_increasing  # a burst replays oldest first


def test_dark_spell_browns_the_station_out_and_it_recovers():
    t = pd.date_range("2026-07-01", periods=60 * 96, freq="15min", tz=TZ)
    days = sorted(set(t.date))
    sun = pd.Series([40.0] * 20 + [250.0] * 40, index=days)  # 20 dull days, then sun
    lk = station_link("FS-B2", "field", t, sun, np.zeros(len(t)), 7)
    off_days = sorted(set(lk.loc[~lk["measured"], "measured_at"].dt.date))
    assert off_days and off_days[0] < days[20] < off_days[-1] + timedelta(days=10)
    assert lk.loc[lk["measured_at"].dt.date == days[-1], "measured"].all()
    assert (lk.loc[~lk["measured"], "link_status"] == "brownout").all()


# --- the whole feed ----------------------------------------------------------------


@pytest.fixture(scope="module")
def feed(source):
    return observe(source, 3)


def test_messages_arrive_in_delivery_order_with_per_sensor_sequence(feed):
    messages, labels = feed
    assert messages["delivered_at"].is_monotonic_increasing
    first = messages.drop_duplicates(["sensor_code", "measured_at"])
    for _, g in first.groupby("sensor_code"):
        assert g.sort_values("measured_at")["seq"].is_monotonic_increasing
    assert len(labels) == 65 * len(labels["measured_at"].unique())


def test_every_message_is_a_labelled_reading(feed):
    messages, labels = feed
    keys = labels.set_index(["sensor_code", "measured_at"])
    m = messages.set_index(["sensor_code", "measured_at"])
    assert m.index.isin(keys.index).all()
    joined = m.join(keys[["value"]], rsuffix="_label")
    assert (joined["value"] == joined["value_label"]).all()
    dupes = messages.duplicated(["sensor_code", "measured_at"]).mean()
    assert 0 < dupes < 0.01


def test_same_seed_same_feed(source, feed):
    again, _ = observe(source, 3)
    pd.testing.assert_frame_equal(again, feed[0])


def test_messages_validate_against_the_wire_schema(feed):
    messages, _ = feed
    row = messages.iloc[123]
    r = Reading(
        sensor=row.sensor_code,
        measured_at=row.measured_at.to_pydatetime(),
        value=row.value,
        unit=row.unit,
        seq=int(row.seq),
        battery_v=row.battery_v,
    )
    assert parse_reading(r.model_dump_json()) == r
    with pytest.raises(ValidationError):
        parse_reading(
            '{"sensor": "X-1", "measured_at": "2026-01-01T00:00:00", "value": 1,'
            ' "unit": "u", "seq": 1}'
        )  # naive timestamp: rejected
    with pytest.raises(ValidationError):
        Reading(sensor="FS-B1-x", measured_at=datetime.now(UTC), value=1, unit="u", seq=-1)
    assert reading_topic("B3", "FS-B3", "soil_moisture") == "coffwe/v1/B3/FS-B3/soil_moisture"


# --- new truth: hydrology and wildlife ---------------------------------------------


def test_reservoir_water_balance_closes(run_dir):
    from coffwe.sim.truth import TruthStore

    w = TruthStore(run_dir).read("water_hourly")
    v = w["reservoir_volume_m3"].to_numpy()
    inflow = w["stream1_flow_ls"].to_numpy()[1:] * 3.6
    expected = (
        v[:-1]
        + inflow
        - w["irrigation_withdrawal_m3"].to_numpy()[1:]
        - RESERVOIR_SEEPAGE_PER_H * v[:-1]
        - w["spill_m3"].to_numpy()[1:]
        + w["shortfall_m3"].to_numpy()[1:]
    )
    np.testing.assert_allclose(v[1:], expected, atol=0.05)
    assert (v >= 0).all() and (v <= RESERVOIR_CAPACITY_M3 + 1e-6).all()
    assert (w[["stream1_flow_ls", "stream2_flow_ls"]] >= 0).all().all()


def test_elephants_visit_in_the_dry_season_and_at_night():
    from coffwe.sim.rng import stream

    # One seed's decade is a small sample (a few monsoon visits either way), so pool five.
    months, frames = [], []
    for seed in range(5):
        frame, visits = simulate_wildlife(
            date(2020, 1, 1), date(2030, 1, 1), stream(seed, "wildlife")
        )
        months += [v["start"].month for v in visits]
        assert all(v["start"].hour >= 19 for v in visits)
        frames.append(frame)
    months = pd.Series(months)
    dry = months.isin([1, 2, 3, 4]).sum() / 4
    monsoon = months.isin([6, 7, 8, 9]).sum() / 4
    assert dry > 3 * monsoon  # expected ratio ~5.7
    frame = pd.concat(frames)
    ele = frame[frame["elephants"]]
    assert ele["passes"].mean() > 5 * frame[~frame["elephants"]]["passes"].mean()
    assert set(ELEPHANT_VISIT_PER_NIGHT) == set(range(1, 13))


def test_all_sensors_cover_every_station_measurement():
    from coffwe.estate_data import STATIONS

    assert {s.code for s in all_sensors()} == {
        f"{st.code}-{m}" for st in STATIONS for m in st.measurements
    }
