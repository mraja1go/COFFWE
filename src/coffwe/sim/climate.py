"""Climate normals for COFFWE: the *parameters* the weather generator draws from.

The estate is fictional, but it's placed in the real Chikkamagaluru coffee belt of the
Western Ghats so its climate can be validated against real reanalysis data. Values are
monthly normals at the estate-core weather mast (REFERENCE_ELEVATION_M).

Everything the generator needs is here, separate from the generator's logic, so
calibration is a matter of editing numbers -- not code.
"""

from dataclasses import dataclass

import numpy as np

LATITUDE_DEG = 13.32
LONGITUDE_DEG = 75.77
REFERENCE_ELEVATION_M = 1265  # weather mast WX-CORE at the estate core (beside B6/B7)
LAPSE_RATE_C_PER_M = -6.5 / 1000  # air cools ~6.5 degC per km of climb
TIMEZONE = "Asia/Kolkata"
IST_MERIDIAN_DEG = 82.5  # IST is set for 82.5 E; the estate's sun runs ~27 min "late"

WET_DAY_THRESHOLD_MM = 1.0  # standard climatology definition of a "wet day"

DAYS_IN_MONTH = np.array([31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31])


@dataclass(frozen=True)
class MonthlyNormals:
    """Twelve values per field, January..December."""

    rain_mm: tuple[float, ...]  # mean monthly total
    wet_days: tuple[float, ...]  # mean number of days with >= 1 mm
    # Lag-1 autocorrelation of wet/dry days: 0 = independent coin flips each day,
    # higher = longer spells. Monsoon rain comes in long spells; dry-season showers don't.
    wet_persistence: tuple[float, ...]
    # Gamma shape for wet-day amounts: < 1 = mostly light days with rare deluges.
    gamma_shape: tuple[float, ...]
    tmean_c: tuple[float, ...]  # mean daily temperature
    dtr_c: tuple[float, ...]  # mean diurnal temperature range (tmax - tmin)
    rh_pct: tuple[float, ...]  # mean relative humidity
    wind_ms: tuple[float, ...]  # mean wind speed

    def __post_init__(self) -> None:
        for name, values in self.__dict__.items():
            if len(values) != 12:
                raise ValueError(f"{name} needs 12 monthly values, got {len(values)}")


# Western Ghats coffee climate at ~1,250 m: dry Dec-Feb, blossom showers Feb-Mar,
# pre-monsoon thunderstorms Apr-May, south-west monsoon Jun-Sep (most of the year's
# ~2,450 mm), retreating north-east monsoon Oct-Nov.
#
# Calibration v2 (2026-11-25, see docs/weather-validation.md): persistence raised in
# the transition months, more wet days in May/Sep-Dec, RH and clear-day sunshine
# lowered to match the reanalysis. Rain totals stay gauge-like on purpose (the
# reanalysis under-represents orographic rain on the Ghats).
NORMALS = MonthlyNormals(
    #            Jan    Feb    Mar    Apr    May    Jun    Jul    Aug    Sep    Oct    Nov    Dec
    rain_mm=(    5,     8,    25,    70,   120,   450,   700,   500,   250,   200,    80,    20),
    wet_days=( 0.6,   1.0,   2.5,   6.0,  11.0,  24.0,  28.0,  26.0,  21.0,  15.0,   7.0,   2.0),
    wet_persistence=(
              0.30,  0.30,  0.35,  0.40,  0.50,  0.65,  0.70,  0.65,  0.60,  0.60,  0.45,  0.35),
    gamma_shape=(0.70,  0.70,  0.65,  0.70,  0.70,  0.85,  0.90,  0.85,  0.80,  0.75,  0.70,  0.70),
    tmean_c=( 20.0,  21.5,  23.5,  24.5,  24.0,  21.5,  20.5,  20.5,  21.0,  21.0,  20.5,  19.5),
    dtr_c=(   12.0,  13.0,  13.0,  12.0,  10.0,   6.0,   5.0,   5.0,   6.0,   8.0,   9.0,  11.0),
    rh_pct=(  63.0,  55.0,  55.0,  65.0,  77.0,  89.0,  91.0,  90.0,  88.0,  83.0,  77.0,  72.0),
    wind_ms=(  2.2,   2.3,   2.3,   2.2,   2.7,   4.3,   4.8,   4.3,   3.4,   2.3,   2.5,   2.5),
)  # fmt: skip

# Clearness index (fraction of top-of-atmosphere sunlight reaching the ground) on a
# *dry* day, by month: haze and afternoon cloud make the transition months dimmer.
CLEAR_DAY_KT = (0.66, 0.68, 0.66, 0.61, 0.57, 0.48, 0.46, 0.50, 0.53, 0.57, 0.59, 0.61)
WET_DAY_KT = 0.47  # overcast but not raining
MONSOON_WET_DAY_KT = 0.46
RAINING_HOUR_KT = 0.25

# Day-to-day randomness alone under-represents year-to-year swings (a known weakness
# of weather generators). Real monsoons are good or bad *as a whole season*, so each
# year draws one strength factor that scales every monsoon-day rain amount.
MONSOON_STRENGTH_SD = 0.10

MONSOON_MONTHS = frozenset({6, 7, 8, 9})

# Day-to-day temperature anomaly: AR(1), a_t = PHI * a_(t-1) + noise.
TEMP_ANOMALY_PHI = 0.7
TEMP_ANOMALY_SD_C = 0.9  # standard deviation of the anomaly itself
WET_DAY_COOLING_C = 0.8  # rain days run cooler...
WET_DAY_DTR_FACTOR = 0.65  # ...and with a smaller day/night swing

TMIN_HOUR = 6  # coolest just before sunrise
TMAX_HOUR = 14  # warmest early afternoon


def month_mid_anchors() -> np.ndarray:
    """Day-of-year (1-based, 365-day year) at the middle of each month."""
    starts = np.concatenate([[0], np.cumsum(DAYS_IN_MONTH)[:-1]])
    return starts + DAYS_IN_MONTH / 2 + 0.5


def _interpolate_anchors(anchors: np.ndarray, day_of_year: np.ndarray) -> np.ndarray:
    """Linear interpolation between 12 mid-month anchor values, wrapping Dec -> Jan."""
    x = month_mid_anchors()
    x = np.concatenate([[x[-1] - 365], x, [x[0] + 365]])
    y = np.concatenate([[anchors[-1]], anchors, [anchors[0]]])
    return np.interp(np.clip(day_of_year, 1, 365), x, y)


def _month_mean_matrix() -> np.ndarray:
    """A[m, k] = how much anchor k contributes to the average of month m's days.

    Interpolation is linear in the anchors, so "monthly means of the curve" is just
    A @ anchors. Each column is found by interpolating a unit vector.
    """
    doy = np.arange(1, 366)
    month_of_day = np.repeat(np.arange(12), DAYS_IN_MONTH)
    a = np.zeros((12, 12))
    for k in range(12):
        curve = _interpolate_anchors(np.eye(12)[k], doy)
        a[:, k] = np.bincount(month_of_day, weights=curve) / DAYS_IN_MONTH
    return a


_MONTH_MEANS = _month_mean_matrix()


def interpolate_monthly(values: tuple[float, ...], day_of_year: np.ndarray) -> np.ndarray:
    """Smoothly interpolate 12 monthly normals to any day of year, preserving each mean.

    Holding the monthly value for the whole month would make the climate jump at
    every month boundary (31 Jan -> 1 Feb). Plain straight lines between mid-month
    values fix that but shave the peaks: June's days get pulled towards dry May, so
    June comes out drier than its normal. So instead we solve A @ anchors = normals
    for the anchor values whose *interpolated monthly averages* equal the normals --
    smooth like the straight lines, faithful to the monthly numbers like the steps.
    """
    targets = np.asarray(values, dtype=float)
    anchors = np.linalg.solve(_MONTH_MEANS, targets)
    # Next to a big peak, a near-zero month's anchor can overshoot below zero; values
    # here are all physically non-negative.
    anchors = np.where(targets >= 0, np.maximum(anchors, 0), anchors)
    return _interpolate_anchors(anchors, day_of_year)
