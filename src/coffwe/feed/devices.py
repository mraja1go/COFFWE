"""Virtual field hardware: how each sensor and station gets the truth *wrong*.

Per sensor (what the element itself does):
  * calibration bias, fixed at installation (additive, and/or multiplicative)
  * drift: a slow random walk, bounded (sensors are recalibrated now and then)
  * noise on every reading, then rounding to the sensor's resolution
  * occasional faults: stuck values, spikes, a clogged rain gauge, false/missed triggers

Per station (power and radio, shared by all its sensors):
  * solar battery: a dull spell drains it; below a threshold the station browns out
    for the day and measures nothing
  * radio link as a two-state (Gilbert-Elliott) Markov chain: good / bad. In the bad
    state messages don't get through. Stations with store-and-forward buffer them
    and deliver them in a late burst when the link recovers; others lose them
  * small network latency; rare duplicate deliveries (MQTT QoS 1 retransmits)

Everything is deterministic for a seed, and every injected fault is labelled, so
Phase 6 anomaly detection can be trained and scored against the truth.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from coffwe.feed.truth import REPORT_MINUTES, SensorRef, TruthSource
from coffwe.sim.rng import stream

READINGS_PER_DAY = 24 * 60 // REPORT_MINUTES


@dataclass(frozen=True)
class SensorModel:
    noise_sd: float = 0.0  # additive, in the measurement's unit
    noise_rel: float = 0.0  # multiplicative (fraction of the value)
    resolution: float = 0.0  # 0 = continuous
    bias_sd: float = 0.0  # additive calibration offset at install
    gain_sd: float = 0.0  # multiplicative calibration error at install
    drift_sd_per_day: float = 0.0  # random-walk step (additive) per sqrt(day)
    stuck_per_day: float = 0.0  # chance a stuck-value fault starts on a given day
    stuck_hours: float = 12.0  # mean duration
    spike_per_reading: float = 0.0
    spike_size: float = 0.0  # added (with random sign) on a spike


MODELS: dict[str, SensorModel] = {
    "air_temperature": SensorModel(0.15, 0, 0.1, 0.2, 0, 0.004, 0.001, 8, 0.0003, 15),
    "relative_humidity": SensorModel(1.5, 0, 1.0, 1.5, 0, 0.03, 0.001, 8, 0.0003, 30),
    "rainfall": SensorModel(resolution=0.2, gain_sd=0.04),  # tipping bucket; clogging below
    "wind_speed": SensorModel(0.15, 0.03, 0.1, 0, 0.03),
    "solar_radiation": SensorModel(
        2.0, 0.02, 1.0, 0, 0.03, spike_per_reading=0.0002, spike_size=600
    ),  # fmt: skip
    "barometric_pressure": SensorModel(0.1, 0, 0.1, 0.5, 0, 0.003),
    "soil_moisture": SensorModel(0.006, 0, 0.001, 0.02, 0, 0.0003, 0.0015, 24, 0.0003, 0.2),
    "soil_temperature": SensorModel(0.1, 0, 0.1, 0.2, 0, 0.002, 0.001, 12),
    "leaf_wetness": SensorModel(3.0, 0, 1.0, 2.0, 0, 0.02),
    "rain_detected": SensorModel(),  # binary: false alarms / misses below
    "water_level": SensorModel(0.01, 0, 0.01, 0.02, 0, 0.0005, 0.001, 24),
    "stream_flow": SensorModel(0.5, 0.05, 1.0, 0, 0.10),  # rating-curve error ~10%
    "motion_events": SensorModel(),  # counts: misses and false triggers below
}

RAIN_CLOG_PER_DAY = 0.003  # leaves/insects block the funnel...
RAIN_CLOG_CLEARED_WEEKDAY = 5  # ...until the Saturday maintenance round
DETECTOR_FALSE_ALARM, DETECTOR_MISS = 0.01, 0.05
MOTION_MISS, MOTION_FALSE_PER_READING = 0.10, 0.01

# Stations
BATTERY_DRAIN_PER_DAY = 0.17  # share of capacity a station uses per day
BATTERY_CHARGE_PER_WM2 = 0.17 / 170  # break-even at ~170 W/m2 daily mean: dull spells drain
BROWNOUT_BELOW, RECOVER_ABOVE = 0.10, 0.30
LINK_GOOD_TO_BAD, LINK_BAD_TO_GOOD = 0.002, 0.15  # per reading interval
LINK_RAIN_PENALTY = 0.01  # heavy rain (>5 mm/h) attenuates the radio
BUFFER_READINGS = 96  # store-and-forward capacity: one day at 15-min reporting
DUPLICATE_RATE = 0.003
STORE_AND_FORWARD = {"weather", "field", "process", "water"}  # corridor units don't buffer


def _bounded_walk(rng, n: int, sd_per_day: float) -> np.ndarray:
    if sd_per_day == 0:
        return np.zeros(n)
    steps = rng.normal(0, sd_per_day * np.sqrt(1 / READINGS_PER_DAY), n)
    walk = np.cumsum(steps)
    bound = 3 * sd_per_day * np.sqrt(365)
    return np.clip(walk, -bound, bound)


def _fault_windows(rng, times: pd.DatetimeIndex, per_day: float, mean_hours: float) -> np.ndarray:
    """Boolean mask of readings inside randomly starting fault episodes."""
    n = len(times)
    mask = np.zeros(n, dtype=bool)
    if per_day == 0:
        return mask
    starts = np.flatnonzero(rng.random(n) < per_day / READINGS_PER_DAY)
    for s in starts:
        length = max(1, int(rng.exponential(mean_hours) * 60 / REPORT_MINUTES))
        mask[s : s + length] = True
    return mask


def measure(sensor: SensorRef, truth: pd.Series, seed: int) -> pd.DataFrame:
    """One sensor's readings over the truth's timeline, before any radio effects."""
    rng = stream(seed, f"device-{sensor.code}")
    model = MODELS[sensor.measurement]
    x = truth.to_numpy(dtype=float)
    n = len(x)
    faults = np.full(n, "", dtype=object)
    m = sensor.measurement

    if m == "rainfall":
        gain = 1 + rng.normal(0, model.gain_sd)
        clogged = np.zeros(n, dtype=bool)
        starts = np.flatnonzero(rng.random(n) < RAIN_CLOG_PER_DAY / READINGS_PER_DAY)
        weekday = truth.index.weekday.to_numpy()
        for s in starts:
            e = s
            while e < n and not (weekday[e] == RAIN_CLOG_CLEARED_WEEKDAY and e > s + 4):
                e += 1
            clogged[s:e] = True
        caught = np.where(clogged, 0.0, x * gain)
        tips = np.floor(np.cumsum(caught) / model.resolution)  # tipping bucket keeps remainder
        value = np.diff(tips, prepend=0) * model.resolution
        faults[clogged & (x > 0)] = "clogged"
    elif m == "rain_detected":
        false_alarm = (x == 0) & (rng.random(n) < DETECTOR_FALSE_ALARM)
        miss = (x == 1) & (rng.random(n) < DETECTOR_MISS)
        value = np.where(false_alarm, 1.0, np.where(miss, 0.0, x))
        faults[false_alarm], faults[miss] = "false_alarm", "missed"
    elif m == "motion_events":
        seen = rng.binomial(x.astype(int), 1 - MOTION_MISS)
        false = rng.poisson(MOTION_FALSE_PER_READING, n)
        value = (seen + false).astype(float)
        faults[seen < x] = "missed"
        faults[false > 0] = "false_trigger"
    else:
        gain = 1 + rng.normal(0, model.gain_sd) if model.gain_sd else 1.0
        bias = rng.normal(0, model.bias_sd) if model.bias_sd else 0.0
        drift = _bounded_walk(rng, n, model.drift_sd_per_day)
        noise = rng.normal(0, 1, n) * (model.noise_sd + model.noise_rel * np.abs(x))
        value = x * gain + bias + drift + noise
        if model.spike_per_reading:
            spikes = rng.random(n) < model.spike_per_reading
            value = np.where(spikes, value + rng.choice([-1, 1], n) * model.spike_size, value)
            faults[spikes] = "spike"
        stuck = _fault_windows(rng, truth.index, model.stuck_per_day, model.stuck_hours)
        if stuck.any():
            # A stuck sensor repeats the last reading it took before sticking.
            run_start = np.flatnonzero(stuck & ~np.roll(stuck, 1))
            for s in run_start:
                e = s
                while e < n and stuck[e]:
                    e += 1
                value[s:e] = value[s - 1] if s > 0 else value[s]
            faults[stuck] = "stuck"
        # Physical floor: these sensors can't read below zero (or above 100 %).
        if sensor.valid_min is not None and sensor.valid_min >= 0 and m != "air_temperature":
            value = np.where(faults == "spike", value, np.maximum(value, 0.0))
        if sensor.unit == "%":
            value = np.where(faults == "spike", value, np.clip(value, 0, 100))
        if model.resolution:
            value = np.round(value / model.resolution) * model.resolution

    return pd.DataFrame(
        {
            "sensor_code": sensor.code,
            "station_code": sensor.station_code,
            "measurement": m,
            "unit": sensor.unit,
            "measured_at": truth.index,
            "true_value": x,
            "value": np.round(value, 4),
            "fault": faults,
        }
    )


def station_link(
    station_code: str,
    station_type: str,
    times: pd.DatetimeIndex,
    solar_daily: pd.Series,
    rain_rate: np.ndarray,
    seed: int,
) -> pd.DataFrame:
    """Power and radio for one station: when each reading is measured and delivered."""
    rng = stream(seed, f"link-{station_code}")
    n = len(times)
    days = times.normalize()
    # --- battery: a daily energy balance; brown out when it runs down
    soc, browned_out = 0.8, False
    day_state = {}
    for day in pd.unique(days):
        sun = float(solar_daily.get(pd.Timestamp(day).date(), 200.0))
        soc = float(np.clip(soc + BATTERY_CHARGE_PER_WM2 * sun - BATTERY_DRAIN_PER_DAY, 0, 1))
        if browned_out and soc > RECOVER_ABOVE:
            browned_out = False
        elif not browned_out and soc < BROWNOUT_BELOW:
            browned_out = True
        day_state[day] = (soc, browned_out)
    soc_arr = np.array([day_state[d][0] for d in days])
    measured = ~np.array([day_state[d][1] for d in days])
    battery_v = np.round(3.3 + 0.9 * soc_arr + rng.normal(0, 0.01, n), 2)

    # --- radio: good/bad Markov chain; buffered or lost while bad
    bad = np.zeros(n, dtype=bool)
    state = False
    u = rng.random(n)
    for i in range(n):
        if state:
            state = u[i] >= LINK_BAD_TO_GOOD
        else:
            state = u[i] < LINK_GOOD_TO_BAD + (LINK_RAIN_PENALTY if rain_rate[i] > 5 else 0)
        bad[i] = state
    latency = rng.lognormal(np.log(2.0), 0.5, n)  # seconds
    # Work in integer nanoseconds since the epoch (UTC): unambiguous and mutable --
    # late bursts overwrite entries below.
    measured_ns = times.as_unit("ns").asi8
    delivered_at = measured_ns + (latency * 1e9).astype(np.int64)
    delivered = measured & ~bad
    status = np.where(measured, "ok", "brownout").astype(object)
    if station_type in STORE_AND_FORWARD:
        # Each bad run is flushed when the link comes back (if the buffer held it).
        i = 0
        while i < n:
            if bad[i]:
                j = i
                while j < n and bad[j]:
                    j += 1
                if j < n:  # recovered at j
                    run = np.arange(i, j)[measured[i:j]]
                    kept = run[-BUFFER_READINGS:]
                    lost = run[: max(len(run) - BUFFER_READINGS, 0)]
                    flush = measured_ns[j]
                    for k, idx in enumerate(kept):
                        delivered[idx] = True
                        delivered_at[idx] = flush + 2 * (k + 1) * 1_000_000_000
                        status[idx] = "late"
                    status[lost] = "lost"
                else:
                    status[np.arange(i, n)[measured[i:n]]] = "lost"
                i = j
            else:
                i += 1
    else:
        status[measured & bad] = "lost"
    duplicate = delivered & (rng.random(n) < DUPLICATE_RATE)
    return pd.DataFrame(
        {
            "measured_at": times,
            "measured": measured,
            "delivered": delivered,
            "delivered_at": pd.to_datetime(delivered_at, unit="ns", utc=True).tz_convert(times.tz),
            "link_status": status,
            "duplicate": duplicate,
            "battery_v": battery_v,
        }
    )


def observe(source: TruthSource, seed: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The whole estate's sensor feed.

    Returns (messages, labels):
      messages -- what arrives at the broker, in delivery order (late bursts, duplicates)
      labels   -- every scheduled reading with its truth, observed value, faults, fate
    """
    from coffwe.estate_data import STATIONS

    sensors = source.sensors()
    station_type = {s.code: s.station_type for s in STATIONS}
    mast = source._weather if hasattr(source, "_weather") else None
    times = source.times
    solar_daily = (
        mast["solar_radiation"].groupby(mast.index.date).mean() if mast is not None else pd.Series()
    )
    rain_rate = (
        mast["rainfall"].reindex(times, method="ffill").to_numpy()
        if mast is not None
        else np.zeros(len(times))
    )

    links = {
        st: station_link(st, station_type[st], times, solar_daily, rain_rate, seed)
        for st in sorted({s.station_code for s in sensors})
    }
    labels = []
    for s in sensors:
        r = measure(s, source.series(s), seed)
        link = links[s.station_code]
        r["measured"] = link["measured"].to_numpy()
        r["delivered"] = link["delivered"].to_numpy()
        r["delivered_at"] = link["delivered_at"].to_numpy()
        r["link_status"] = link["link_status"].to_numpy()
        r["duplicate"] = link["duplicate"].to_numpy()
        r["battery_v"] = link["battery_v"].to_numpy()
        r["seq"] = np.cumsum(r["measured"].to_numpy())  # the device's own message counter
        labels.append(r)
    labels = pd.concat(labels, ignore_index=True)

    sent = labels[labels["delivered"]]
    dupes = sent[sent["duplicate"]].copy()
    dupes["delivered_at"] = dupes["delivered_at"] + pd.Timedelta(seconds=5)
    cols = ["sensor_code", "station_code", "measurement", "unit", "measured_at", "delivered_at",
            "value", "seq", "battery_v"]  # fmt: skip
    messages = (
        pd.concat([sent[cols], dupes[cols]])
        .sort_values(["delivered_at", "sensor_code"], kind="stable")
        .reset_index(drop=True)
    )
    return messages, labels
