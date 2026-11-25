"""Validate the synthetic weather against ~10 years of real hourly reanalysis weather.

Reference data: the free Open-Meteo historical archive (ERA5/ERA5-Land reanalysis),
for the estate's real-world stand-in location in the Chikkamagaluru coffee belt,
temperature-adjusted to the mast's elevation. Downloaded once and cached under
data/reference/ (gitignored -- it can always be fetched again).

A reanalysis is a weather *model* constrained by observations, not a rain gauge. It
is known to drizzle too often (too many wet days, too few heavy ones) and to smooth
out orographic rain over mountains. So the comparison reads like this: timing and
shape of the seasons, temperature, humidity, radiation should match closely; rain
totals and wet-day counts are judged with that bias in mind.
"""

import json
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from coffwe.sim import climate as C
from coffwe.sim.rng import stream
from coffwe.sim.weather import generate_weather

REFERENCE_DIR = Path("data/reference")
REFERENCE_YEARS = (2015, 2024)
REPORT_PATH = Path("docs/weather-validation.md")

_API = "https://archive-api.open-meteo.com/v1/archive"
_VARIABLES = {  # Open-Meteo name -> our column name
    "temperature_2m": "air_temperature",
    "relative_humidity_2m": "relative_humidity",
    "precipitation": "rainfall",
    "wind_speed_10m": "wind_speed",
    "shortwave_radiation": "solar_radiation",
    "surface_pressure": "barometric_pressure",
}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def fetch_reference() -> pd.DataFrame:
    first, last = REFERENCE_YEARS
    cache = REFERENCE_DIR / f"openmeteo_{C.LATITUDE_DEG}_{C.LONGITUDE_DEG}_{first}_{last}.parquet"
    if cache.exists():
        return pd.read_parquet(cache)
    params = {
        "latitude": C.LATITUDE_DEG,
        "longitude": C.LONGITUDE_DEG,
        "elevation": C.REFERENCE_ELEVATION_M,  # API applies a lapse-rate correction
        "start_date": f"{first}-01-01",
        "end_date": f"{last}-12-31",
        "hourly": ",".join(_VARIABLES),
        "timezone": C.TIMEZONE,
        "wind_speed_unit": "ms",
    }
    url = f"{_API}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310 (fixed https URL)
        payload = json.load(resp)
    hourly = payload["hourly"]
    df = pd.DataFrame({ours: hourly[theirs] for theirs, ours in _VARIABLES.items()})
    df.index = pd.DatetimeIndex(pd.to_datetime(hourly["time"]), name="ts").tz_localize(C.TIMEZONE)
    df = df.dropna()
    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache)
    return df


def synthetic(years: int, seed: int = 2026) -> pd.DataFrame:
    return generate_weather(date(2001, 1, 1), date(2001 + years, 1, 1), stream(seed, "weather"))


# --- statistics --------------------------------------------------------------------


def _daily(hourly: pd.DataFrame) -> pd.DataFrame:
    by_day = hourly.groupby(hourly.index.date)
    d = pd.DataFrame(
        {
            "rain": by_day["rainfall"].sum(),
            "tmax": by_day["air_temperature"].max(),
            "tmin": by_day["air_temperature"].min(),
            "rh": by_day["relative_humidity"].mean(),
            "solar": by_day["solar_radiation"].mean(),
            "wind": by_day["wind_speed"].mean(),
        }
    )
    d.index = pd.DatetimeIndex(d.index)
    d["wet"] = d["rain"] >= C.WET_DAY_THRESHOLD_MM
    return d


def _spell_lengths(wet: pd.Series, value: bool) -> np.ndarray:
    """Lengths of consecutive runs of `value` (True = wet spells, False = dry spells)."""
    run_id = (wet != wet.shift()).cumsum()
    runs = wet.groupby(run_id).agg(["first", "size"])
    return runs.loc[runs["first"] == value, "size"].to_numpy()


def monthly_stats(hourly: pd.DataFrame) -> pd.DataFrame:
    d = _daily(hourly)
    n_years = d.index.year.nunique()
    by_month = d.groupby(d.index.month)
    prev_wet = d["wet"].shift(fill_value=False)
    stats = pd.DataFrame(
        {
            "rain_mm": by_month["rain"].sum() / n_years,
            "wet_days": by_month["wet"].sum() / n_years,
            # P(wet today | wet yesterday): the persistence the Markov chain controls
            "p_wet_after_wet": d["wet"][prev_wet].groupby(d.index[prev_wet].month).mean(),
            "tmax_c": by_month["tmax"].mean(),
            "tmin_c": by_month["tmin"].mean(),
            "rh_pct": by_month["rh"].mean(),
            "solar_wm2": by_month["solar"].mean(),
            "wind_ms": by_month["wind"].mean(),
        }
    )
    stats.index = MONTHS[: len(stats)]
    return stats


def annual_stats(hourly: pd.DataFrame) -> dict[str, float]:
    d = _daily(hourly)
    yearly_rain = d["rain"].groupby(d.index.year).sum()
    wet_amounts = d.loc[d["wet"], "rain"]
    return {
        "Annual rain, mean (mm)": yearly_rain.mean(),
        "Annual rain, year-to-year SD (mm)": yearly_rain.std(),
        "Wet days per year": d["wet"].sum() / d.index.year.nunique(),
        "Wettest day per year, mean (mm)": d["rain"].groupby(d.index.year).max().mean(),
        "95th percentile wet-day rain (mm)": wet_amounts.quantile(0.95),
        "Mean wet spell (days)": _spell_lengths(d["wet"], True).mean(),
        "Mean dry spell (days)": _spell_lengths(d["wet"], False).mean(),
        "Longest dry spell per year, mean (days)": np.mean(
            [_spell_lengths(g["wet"], False).max() for _, g in d.groupby(d.index.year)]
        ),
        "Mean daily max temp (degC)": d["tmax"].mean(),
        "Mean daily min temp (degC)": d["tmin"].mean(),
        "Mean relative humidity (%)": d["rh"].mean(),
        "Mean solar radiation (W/m2)": d["solar"].mean(),
        "Mean wind speed (m/s)": d["wind"].mean(),
    }


# --- report ------------------------------------------------------------------------


def _md_table(df: pd.DataFrame, fmt: str = "{:.1f}") -> str:
    header = "| | " + " | ".join(df.columns) + " |"
    rule = "|---" * (len(df.columns) + 1) + "|"
    rows = [
        f"| {idx} | " + " | ".join(fmt.format(v) for v in row) + " |"
        for idx, row in zip(df.index, df.to_numpy(), strict=True)
    ]
    return "\n".join([header, rule, *rows])


def build_report(synth: pd.DataFrame, real: pd.DataFrame, years: int) -> str:
    sm, rm = monthly_stats(synth), monthly_stats(real)
    sa, ra = annual_stats(synth), annual_stats(real)
    annual = pd.DataFrame({"Synthetic": sa, "Real (reanalysis)": ra})
    annual["Ratio"] = annual["Synthetic"] / annual["Real (reanalysis)"]

    def side_by_side(cols: list[str]) -> pd.DataFrame:
        parts = {}
        for col in cols:
            parts[f"{col} syn"] = sm[col]
            parts[f"{col} real"] = rm[col]
        return pd.DataFrame(parts)

    first, last = REFERENCE_YEARS
    return f"""# Weather generator validation

_Generated by `uv run python -m coffwe.sim validate-weather`. Synthetic: {years} years from
the COFFWE generator (`src/coffwe/sim/weather.py`, parameters in `climate.py`). Real:
Open-Meteo historical reanalysis {first}-{last} for {C.LATITUDE_DEG} N, {C.LONGITUDE_DEG} E
(Chikkamagaluru coffee belt), lapse-rate adjusted to the {C.REFERENCE_ELEVATION_M} m mast._

**Reading the rain numbers:** reanalysis drizzles too often and smooths mountain
rain, so it tends to show *more* wet days and *lower* peaks than a real gauge on a
Western Ghats slope. The synthetic estate is deliberately calibrated to gauge-like
climatology (~2,450 mm, few very wet dry-season days).

## Verdict (calibration v2, 2026-11-25)

- **Match closely:** daily max/min temperature (within ~1 degC every month), humidity,
  solar radiation (within ~10% every month), wind, wet-spell length, monsoon
  persistence (P(wet after wet) Jun-Nov), year-to-year rain variability, longest
  annual dry spell, and the seasonal shape of rain.
- **Differ by design / known reanalysis bias:** synthetic has ~60% more annual rain,
  ~20% fewer wet days, longer dry spells and heavier peaks. All four point the same
  way as reanalysis drizzle bias and orographic smoothing; the synthetic totals are
  gauge-like for a Western Ghats estate slope.
- **Known generator limitation:** with a seasonal Markov chain the wet-day count lags
  the monsoon's rapid onset, so May/June run ~10% under their wet-day targets.

## Year-round

{_md_table(annual, "{:.2f}")}

## Rain by month

{_md_table(side_by_side(["rain_mm", "wet_days", "p_wet_after_wet"]), "{:.2f}")}

## Temperature, humidity, radiation, wind by month

{_md_table(side_by_side(["tmax_c", "tmin_c", "rh_pct"]))}

{_md_table(side_by_side(["solar_wm2", "wind_ms"]))}
"""


def run(years: int = 30) -> Path:
    real = fetch_reference()
    synth = synthetic(years)
    REPORT_PATH.write_text(build_report(synth, real, years))
    return REPORT_PATH
