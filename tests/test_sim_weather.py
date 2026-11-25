"""Weather generator tests: physical laws (property-based) plus long-run statistics."""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from coffwe.sim import climate as C
from coffwe.sim import engine
from coffwe.sim.rng import stream
from coffwe.sim.truth import TruthStore
from coffwe.sim.weather import (
    HOURLY_COLUMNS,
    block_air_temperature,
    generate_daily,
    generate_hourly,
    generate_weather,
    generate_weather_for_seed,
)

seeds = st.integers(min_value=0, max_value=2**32 - 1)
starts = st.dates(min_value=date(2000, 1, 1), max_value=date(2040, 12, 31))
lengths = st.integers(min_value=1, max_value=60)


# --- Physical laws, for any seed, start date and length ----------------------------


@settings(max_examples=40, deadline=None)
@given(seed=seeds, start=starts, n_days=lengths)
def test_weather_obeys_physical_limits(seed, start, n_days):
    w = generate_weather(start, start + timedelta(days=n_days), stream(seed, "weather"))
    assert len(w) == n_days * 24
    assert list(w.columns) == HOURLY_COLUMNS
    assert not w.isna().any().any()
    assert (w["rainfall"] >= 0).all()
    assert w["relative_humidity"].between(15, 100).all()
    assert (w["solar_radiation"] >= 0).all()
    assert (w["wind_speed"] >= 0).all()
    assert w["air_temperature"].between(0, 42).all()  # highland tropics, ~1,265 m
    assert w["barometric_pressure"].between(850, 895).all()


@settings(max_examples=40, deadline=None)
@given(seed=seeds, start=starts, n_days=lengths)
def test_no_sunshine_at_night(seed, start, n_days):
    w = generate_weather(start, start + timedelta(days=n_days), stream(seed, "weather"))
    night = w.index.hour.isin([0, 1, 2, 3, 4, 20, 21, 22, 23])
    assert (w.loc[night, "solar_radiation"] == 0).all()


@settings(max_examples=40, deadline=None)
@given(seed=seeds, start=starts, n_days=lengths)
def test_hourly_rain_adds_up_to_daily_rain(seed, start, n_days):
    rng = stream(seed, "weather")
    daily = generate_daily(start, start + timedelta(days=n_days), rng)
    hourly = generate_hourly(daily, rng)
    per_day = hourly["rainfall"].groupby(hourly.index.date).sum().to_numpy()
    np.testing.assert_allclose(per_day, daily["rain_mm"].to_numpy(), atol=0.02)  # rounding
    # and every wet day really clears the wet-day threshold
    assert (daily.loc[daily["wet"], "rain_mm"] >= C.WET_DAY_THRESHOLD_MM).all()


@settings(max_examples=40, deadline=None)
@given(seed=seeds, start=starts, n_days=lengths)
def test_daily_extremes_bracket_the_hourly_curve(seed, start, n_days):
    rng = stream(seed, "weather")
    daily = generate_daily(start, start + timedelta(days=n_days), rng)
    assert (daily["tmax"] > daily["tmin"]).all()
    hourly = generate_hourly(daily, rng)
    t = hourly["air_temperature"]
    # the cosine curve only ever moves between neighbouring days' extremes
    lo = min(daily["tmin"].min(), daily["tmax"].min())
    hi = max(daily["tmin"].max(), daily["tmax"].max())
    assert t.min() >= lo - 0.01 and t.max() <= hi + 0.01


@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.filter_too_much])
@given(values=st.lists(st.floats(min_value=1, max_value=50), min_size=12, max_size=12))
def test_interpolation_preserves_monthly_means(values):
    anchors = np.linalg.solve(C._MONTH_MEANS, values)
    assume((anchors >= 0).all())  # no clipping happened
    doy = np.arange(1, 366)
    curve = C.interpolate_monthly(tuple(values), doy)
    month = np.repeat(np.arange(12), C.DAYS_IN_MONTH)
    np.testing.assert_allclose(np.bincount(month, weights=curve) / C.DAYS_IN_MONTH, values)


def test_interpolation_is_continuous_across_new_year():
    curve = C.interpolate_monthly(C.NORMALS.tmean_c, np.array([365, 1]))
    assert abs(curve[0] - curve[1]) < 0.1


# --- Reproducibility ---------------------------------------------------------------


def test_same_seed_same_weather_different_seed_different_weather():
    span = (date(2026, 1, 1), date(2026, 3, 1))
    a = generate_weather(*span, stream(42, "weather"))
    b = generate_weather(*span, stream(42, "weather"))
    c = generate_weather(*span, stream(43, "weather"))
    pd.testing.assert_frame_equal(a, b)
    assert not a.equals(c)


def test_subsystem_streams_are_independent():
    assert stream(42, "weather").random() != stream(42, "soil").random()
    assert stream(42, "weather").random() == stream(42, "weather").random()


def test_lapse_rate():
    t = np.array([20.0])
    assert block_air_temperature(t, C.REFERENCE_ELEVATION_M + 1000)[0] == pytest.approx(13.5)


# --- Long-run climate statistics (30 synthetic years) ------------------------------


@pytest.fixture(scope="module")
def thirty_years():
    rng = stream(7, "weather")
    daily = generate_daily(date(2001, 1, 1), date(2031, 1, 1), rng)
    return daily, generate_hourly(daily, rng)


def test_annual_rain_close_to_normal(thirty_years):
    daily, _ = thirty_years
    annual = daily["rain_mm"].groupby(pd.DatetimeIndex(daily.index).year).sum()
    assert annual.mean() == pytest.approx(sum(C.NORMALS.rain_mm), rel=0.08)
    assert 200 < annual.std() < 500  # good and bad monsoon years both happen


def test_monsoon_dominates_and_dry_season_is_dry(thirty_years):
    daily, _ = thirty_years
    by_month = daily["rain_mm"].groupby(pd.DatetimeIndex(daily.index).month).sum()
    assert by_month.loc[[6, 7, 8, 9]].sum() / by_month.sum() > 0.65
    assert by_month.loc[[12, 1, 2]].sum() / by_month.sum() < 0.03


def test_monthly_temperature_matches_normals(thirty_years):
    daily, _ = thirty_years
    tmean = daily["tmean"].groupby(pd.DatetimeIndex(daily.index).month).mean()
    # wet days run 0.8 degC cool, so monsoon months sit a little below the normal
    np.testing.assert_allclose(tmean.to_numpy(), C.NORMALS.tmean_c, atol=1.0)


# --- Engine + truth store ----------------------------------------------------------


def test_engine_writes_readable_ground_truth(tmp_path):
    store = engine.run(seed=1, start=date(2026, 1, 1), days=40, root=tmp_path)
    info = store.read_info()
    assert info.seed == 1 and info.end == "2026-02-10"
    w = TruthStore(store.run_dir).read("weather_hourly")
    assert len(w) == 40 * 24
    assert str(w.index.tz) == C.TIMEZONE
    assert w.index.is_monotonic_increasing
    expected = generate_weather_for_seed(date(2026, 1, 1), date(2026, 2, 10), 1)
    np.testing.assert_allclose(w.to_numpy(), expected.to_numpy())


def test_weather_depends_only_on_seed_and_day_not_run_length():
    long = generate_weather_for_seed(date(2026, 1, 1), date(2028, 6, 1), 9)
    short = generate_weather_for_seed(date(2026, 1, 1), date(2027, 4, 1), 9)
    middle = generate_weather_for_seed(date(2027, 2, 10), date(2027, 3, 1), 9)
    pd.testing.assert_frame_equal(short, long.loc[short.index])
    pd.testing.assert_frame_equal(middle, long.loc[middle.index])


def test_weather_is_continuous_across_new_year():
    w = generate_weather_for_seed(date(2026, 12, 20), date(2027, 1, 10), 9)
    jumps = w["air_temperature"].diff().abs()
    midnight = w.index.get_loc(pd.Timestamp("2027-01-01", tz=C.TIMEZONE))
    assert jumps.iloc[midnight] <= jumps.drop(jumps.index[midnight]).max()
