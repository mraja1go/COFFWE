"""Stochastic weather generator for the estate-core weather mast.

Pipeline (each step only uses the ones before it):

1. Daily rain occurrence -- a two-state Markov chain (wet/dry): tomorrow's chance of
   rain depends on whether it rained today, which produces realistic spells.
2. Daily rain amount -- gamma-distributed on wet days (many light days, few deluges).
3. Daily temperature -- seasonal normal + AR(1) anomaly (warm spells persist), cooler
   with a smaller day/night swing on wet days.
4. Hourly disaggregation -- rain into showers (afternoon convection outside the
   monsoon, long spells in it), temperature along a sunrise-minimum/afternoon-maximum
   curve, then humidity, solar radiation, wind and pressure consistent with both.

Weather is *forcing*: nothing on the estate feeds back into it, so a whole run's
weather is generated up front, vectorized, before the hourly engine loop starts.
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd

from coffwe.sim import climate as C
from coffwe.sim.rng import stream

HOURLY_COLUMNS = [
    "air_temperature",  # degC
    "relative_humidity",  # %
    "rainfall",  # mm in the hour
    "wind_speed",  # m/s
    "solar_radiation",  # W/m2, mean over the hour
    "barometric_pressure",  # hPa
]


# --- 1-3: daily series -------------------------------------------------------------


def generate_daily(
    start: date, end: date, rng: np.random.Generator, carry: tuple[bool, float] | None = None
) -> pd.DataFrame:
    """One row per day in [start, end): wet flag, rain, tmin/tmax, daily RH and wind.

    `carry` = (was yesterday wet, yesterday's temperature anomaly), to continue a
    previous chunk seamlessly; the final state is left in ``df.attrs["carry"]``.
    """
    days = pd.date_range(start, end, freq="D", inclusive="left")
    n = len(days)
    doy = np.minimum(days.day_of_year.to_numpy(), 365)  # treat 31 Dec of leap years as 30 Dec
    month = days.month.to_numpy()
    N = C.NORMALS

    # Markov chain. With p = fraction of days that are wet and r = day-to-day
    # persistence, the transition probabilities that reproduce both are:
    #   P(wet | yesterday wet) = p + r(1 - p)      P(wet | yesterday dry) = p(1 - r)
    wet_fraction = C.interpolate_monthly(
        tuple(w / d for w, d in zip(N.wet_days, C.DAYS_IN_MONTH, strict=True)), doy
    )
    wet_fraction = np.clip(wet_fraction, 0.001, 0.97)  # probabilities must stay in (0, 1)
    r = np.clip(C.interpolate_monthly(N.wet_persistence, doy), 0, 0.95)
    p_wet_after_wet = wet_fraction + r * (1 - wet_fraction)
    p_wet_after_dry = wet_fraction * (1 - r)

    u = rng.random(n)
    wet = np.zeros(n, dtype=bool)
    prev = carry[0] if carry else False
    for i in range(n):  # sequential by nature: each day depends on the previous one
        wet[i] = u[i] < (p_wet_after_wet[i] if prev else p_wet_after_dry[i])
        prev = wet[i]

    # Gamma amounts *above* the wet-day threshold, so every wet day really is >= 1 mm.
    mean_wet_amount = C.interpolate_monthly(
        tuple(rain / max(wd, 0.1) for rain, wd in zip(N.rain_mm, N.wet_days, strict=True)), doy
    )
    shape = C.interpolate_monthly(N.gamma_shape, doy)
    excess_mean = np.maximum(mean_wet_amount - C.WET_DAY_THRESHOLD_MM, 0.1)
    excess = rng.gamma(shape, excess_mean / shape)
    # One monsoon-strength factor per calendar year (a good or a weak monsoon season).
    # It scales only the amount *above* the threshold, so a weak year can't shrink a
    # wet day below 1 mm (a bug Hypothesis found when it scaled the whole amount).
    years = days.year.to_numpy()
    unique_years = np.unique(years)
    strength = np.clip(rng.normal(1, C.MONSOON_STRENGTH_SD, len(unique_years)), 0.7, 1.3)
    year_strength = strength[np.searchsorted(unique_years, years)]
    monsoon_day = np.isin(month, list(C.MONSOON_MONTHS))
    excess = np.where(monsoon_day, excess * year_strength, excess)
    rain = np.where(wet, C.WET_DAY_THRESHOLD_MM + excess, 0.0)

    # AR(1) temperature anomaly: a_t = phi * a_(t-1) + e_t. The noise sd is chosen so
    # the anomaly itself has sd TEMP_ANOMALY_SD_C: var(a) = var(e) / (1 - phi^2).
    phi = C.TEMP_ANOMALY_PHI
    noise = rng.normal(0, C.TEMP_ANOMALY_SD_C * np.sqrt(1 - phi**2), n)
    anomaly = np.empty(n)
    a = rng.normal(0, C.TEMP_ANOMALY_SD_C)  # start from the long-run distribution...
    if carry:
        a = carry[1]  # ...unless continuing from the previous chunk
    for i in range(n):
        a = phi * a + noise[i]
        anomaly[i] = a

    tmean = C.interpolate_monthly(N.tmean_c, doy) + anomaly - np.where(wet, C.WET_DAY_COOLING_C, 0)
    dtr = C.interpolate_monthly(N.dtr_c, doy) * np.where(wet, C.WET_DAY_DTR_FACTOR, 1.0)
    dtr = np.maximum(dtr * rng.normal(1, 0.12, n), 2.0)

    # Wet days are more humid, dry days less -- centred on the wet fraction, so the
    # *average* humidity still equals the monthly normal: +6(1-p) on wet days, -6p on dry.
    rh = C.interpolate_monthly(N.rh_pct, doy) + 6.0 * (wet - wet_fraction) + rng.normal(0, 3, n)
    # Lognormal(0, s) has mean exp(s^2/2), not 1 -- divide that out to keep the normal.
    wind = C.interpolate_monthly(N.wind_ms, doy) * rng.lognormal(0, 0.25, n) / np.exp(0.25**2 / 2)
    clear_kt = C.interpolate_monthly(C.CLEAR_DAY_KT, doy)

    frame = pd.DataFrame(
        {
            "wet": wet,
            "rain_mm": rain,
            "tmin": tmean - dtr / 2,
            "tmax": tmean + dtr / 2,
            "tmean": tmean,
            "rh_mean": np.clip(rh, 30, 99),
            "wind_mean": wind,
            "clear_kt": clear_kt,
            "monsoon": np.isin(month, list(C.MONSOON_MONTHS)),
        },
        index=pd.Index(days.date, name="date"),
    )
    frame.attrs["carry"] = (bool(wet[-1]), float(anomaly[-1])) if n else carry
    return frame


# --- 4: hourly disaggregation ------------------------------------------------------


def _hourly_temperature(
    daily: pd.DataFrame,
    before: tuple[float, float] | None = None,
    after: tuple[float, float] | None = None,
) -> np.ndarray:
    """Cosine curve through tmin at 06:00 and tmax at 14:00 of every day.

    Between anchors the value follows half a cosine wave, so it rises fastest
    mid-morning and flattens out near the extremes, like a real thermograph trace.
    The night-time fall runs from today's tmax to *tomorrow's* tmin. `before`/`after`
    are the (tmin, tmax) of the days just outside this chunk, so chunks join smoothly.
    """
    n_days = len(daily)
    tmin, tmax = daily["tmin"].to_numpy(), daily["tmax"].to_numpy()
    # Anchor times in hours since the first midnight; pad one day either side.
    day_offsets = np.arange(-1, n_days + 1) * 24
    b_min, b_max = before or (tmin[0], tmax[0])
    a_min, a_max = after or (tmin[-1], tmax[-1])
    tmin_p = np.concatenate([[b_min], tmin, [a_min]])
    tmax_p = np.concatenate([[b_max], tmax, [a_max]])
    anchor_t = np.column_stack([day_offsets + C.TMIN_HOUR, day_offsets + C.TMAX_HOUR]).ravel()
    anchor_v = np.column_stack([tmin_p, tmax_p]).ravel()

    t = np.arange(n_days * 24) + 0.5  # mid-hour
    seg = np.searchsorted(anchor_t, t) - 1
    t0, t1 = anchor_t[seg], anchor_t[seg + 1]
    v0, v1 = anchor_v[seg], anchor_v[seg + 1]
    frac = (t - t0) / (t1 - t0)
    return v0 + (v1 - v0) * (1 - np.cos(np.pi * frac)) / 2


def _hourly_rain(daily: pd.DataFrame, rng: np.random.Generator) -> np.ndarray:
    """Split each wet day's total into a shower or spell of consecutive hours."""
    rain = np.zeros((len(daily), 24))
    for i, (total, monsoon) in enumerate(zip(daily["rain_mm"], daily["monsoon"], strict=True)):
        if total <= 0:
            continue
        if monsoon:  # long spells, any time of day
            duration = int(rng.integers(5, 16))
            start = int(rng.integers(0, 24))
        else:  # convective: short afternoon/evening showers
            duration = int(rng.integers(1, 5))
            start = int(rng.integers(13, 19))
        hours = np.arange(start, min(start + duration, 24))
        rain[i, hours] = total * rng.dirichlet(np.ones(len(hours)))  # weights sum to 1
    return rain.ravel()


def _solar_sin_elevation(timestamps: pd.DatetimeIndex) -> np.ndarray:
    """sin(sun elevation) at the middle of each hour, from standard solar geometry."""
    doy = timestamps.day_of_year.to_numpy()
    clock_h = timestamps.hour.to_numpy() + 0.5
    # Local solar time: IST is set for 82.5 E; each degree west is 4 minutes later.
    solar_h = clock_h - (C.IST_MERIDIAN_DEG - C.LONGITUDE_DEG) * 4 / 60
    declination = np.radians(23.44) * np.sin(2 * np.pi * (284 + doy) / 365)
    hour_angle = np.radians(15 * (solar_h - 12))
    lat = np.radians(C.LATITUDE_DEG)
    return np.sin(lat) * np.sin(declination) + np.cos(lat) * np.cos(declination) * np.cos(
        hour_angle
    )


def generate_hourly(
    daily: pd.DataFrame,
    rng: np.random.Generator,
    before: tuple[float, float] | None = None,
    after: tuple[float, float] | None = None,
) -> pd.DataFrame:
    n_days = len(daily)
    timestamps = pd.date_range(
        pd.Timestamp(daily.index[0]), periods=n_days * 24, freq="h", tz=C.TIMEZONE
    )

    def per_hour(col: str) -> np.ndarray:
        return np.repeat(daily[col].to_numpy(), 24)

    temp = _hourly_temperature(daily, before, after)
    rain = _hourly_rain(daily, rng)
    raining = rain > 0
    wet_day, monsoon = per_hour("wet"), per_hour("monsoon")

    # Humidity runs opposite to temperature through the day; near-saturated in rain.
    rh = per_hour("rh_mean") - 2.5 * (temp - per_hour("tmean"))
    rh = np.where(raining, np.maximum(rh, rng.uniform(95, 100, len(rh))), rh)
    rh = np.clip(rh, 15, 100)

    # Radiation = sun geometry x top-of-atmosphere intensity x clearness index.
    sin_elev = np.maximum(_solar_sin_elevation(timestamps), 0)
    doy = timestamps.day_of_year.to_numpy()
    top_of_atmosphere = 1361 * (1 + 0.033 * np.cos(2 * np.pi * doy / 365))
    clearness = np.where(
        wet_day, np.where(monsoon, C.MONSOON_WET_DAY_KT, C.WET_DAY_KT), per_hour("clear_kt")
    )
    clearness = np.where(raining, C.RAINING_HOUR_KT, clearness) + rng.normal(0, 0.05, len(rain))
    radiation = top_of_atmosphere * sin_elev * np.clip(clearness, 0.05, 0.78)

    # Wind: daily mean x afternoon peak x gusty Weibull noise (scaled to mean 1).
    hour = timestamps.hour.to_numpy()
    diurnal = 1 + 0.35 * np.sin(2 * np.pi * (hour - 8) / 24)
    weibull_k = 2.2
    gust = rng.weibull(weibull_k, len(hour)) / _weibull_mean(weibull_k)
    wind = np.clip(per_hour("wind_mean") * diurnal * gust, 0, 40)

    # Pressure: standard atmosphere at mast height, + seasonal swing (high in winter,
    # low in the monsoon), + the twice-daily atmospheric tide, + smooth weather noise.
    base = 1013.25 * (1 - 2.25577e-5 * C.REFERENCE_ELEVATION_M) ** 5.25588
    seasonal = 2.5 * np.cos(2 * np.pi * (doy - 15) / 365)
    tide = 1.2 * np.cos(2 * np.pi * (hour - 10) / 12)
    daily_noise = np.interp(
        np.arange(n_days * 24), np.arange(n_days) * 24 + 12, rng.normal(0, 1.0, n_days)
    )
    pressure = base + seasonal + tide + daily_noise

    return pd.DataFrame(
        {
            "air_temperature": temp,
            "relative_humidity": rh,
            "rainfall": rain,
            "wind_speed": wind,
            "solar_radiation": radiation,
            "barometric_pressure": pressure,
        },
        index=pd.Index(timestamps, name="ts"),
    ).round(3)


def _weibull_mean(k: float) -> float:
    from math import gamma

    return gamma(1 + 1 / k)


def generate_weather(start: date, end: date, rng: np.random.Generator) -> pd.DataFrame:
    """Hourly weather at the estate mast for [start, end), from one random stream."""
    return generate_hourly(generate_daily(start, end, rng), rng)


def generate_weather_for_seed(start: date, end: date, seed: int) -> pd.DataFrame:
    """Hourly weather for [start, end) that depends only on (seed, calendar day).

    Drawing one random array for the whole run would make every day's weather depend
    on how long the run is: extend a run by a month and the past changes. Instead each
    calendar year draws from its own stream, and the rain-spell state, temperature
    anomaly and night-time temperature curve carry across New Year. Extending,
    shortening or restarting a run never rewrites weather that has already happened.
    """
    years = range(start.year, (end - timedelta(days=1)).year + 1)
    chunks, carry = [], None
    for year in years:
        rng = stream(seed, f"weather-{year}")
        daily = generate_daily(date(year, 1, 1), date(year + 1, 1, 1), rng, carry)
        carry = daily.attrs["carry"]
        chunks.append((daily, rng))
    hourly = []
    for i, (daily, rng) in enumerate(chunks):
        prev = chunks[i - 1][0].iloc[-1] if i > 0 else None
        nxt = chunks[i + 1][0].iloc[0] if i + 1 < len(chunks) else None
        hourly.append(
            generate_hourly(
                daily,
                rng,
                before=(prev["tmin"], prev["tmax"]) if prev is not None else None,
                after=(nxt["tmin"], nxt["tmax"]) if nxt is not None else None,
            )
        )
    weather = pd.concat(hourly)
    lo = pd.Timestamp(start).tz_localize(C.TIMEZONE)
    hi = pd.Timestamp(end).tz_localize(C.TIMEZONE)
    return weather[(weather.index >= lo) & (weather.index < hi)]


def block_air_temperature(mast_temperature: np.ndarray, elevation_m: float) -> np.ndarray:
    """Adjust mast temperature to a block's elevation with the standard lapse rate."""
    return mast_temperature + C.LAPSE_RATE_C_PER_M * (elevation_m - C.REFERENCE_ELEVATION_M)
