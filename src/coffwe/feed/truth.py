"""Where the virtual sensors get the truth they measure.

`TruthSource` is the seam between "the real estate" and its sensors. Phase 3 replays a
finished Phase 2 run from Parquet; Phase 8 will plug in a live-stepping simulator
behind the same interface (needed once actuators change the estate as it runs).

Truth is hourly; sensors report every REPORT_MINUTES. State variables (temperature,
soil moisture, levels) are interpolated between hours; amounts (rain, animal passes)
are split across the hour's reporting intervals so totals are preserved.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
import pandas as pd

from coffwe.estate_data import MEASUREMENTS, STATIONS
from coffwe.sim.rng import stream
from coffwe.sim.truth import TruthStore

REPORT_MINUTES = 15
PER_HOUR = 60 // REPORT_MINUTES
AMOUNTS = {"rainfall", "motion_events"}  # split across intervals, not interpolated


@dataclass(frozen=True)
class SensorRef:
    code: str  # "FS-B3-soil_moisture"
    station_code: str
    site_code: str
    measurement: str
    unit: str
    valid_min: float | None
    valid_max: float | None


def all_sensors() -> list[SensorRef]:
    refs = []
    for st in STATIONS:
        for m in st.measurements:
            unit, lo, hi = MEASUREMENTS[m]
            refs.append(SensorRef(f"{st.code}-{m}", st.code, st.site_code, m, unit,
                                  None if lo is None else float(lo),
                                  None if hi is None else float(hi)))  # fmt: skip
    return refs


class TruthSource(Protocol):
    def sensors(self) -> list[SensorRef]: ...

    def series(self, sensor: SensorRef) -> pd.Series:
        """True value of what this sensor measures, at every reporting time."""
        ...


class ParquetTruthSource:
    """Truth from a finished simulator run (data/sim/<run_id>/)."""

    def __init__(self, run_dir: Path, seed: int = 0) -> None:
        self.store = TruthStore(run_dir)
        self.seed = seed
        self._weather = self.store.read("weather_hourly")
        self._blocks = self.store.read("block_hourly")
        self._water = self.store.read("water_hourly")
        self._wildlife = self.store.read("wildlife_hourly")
        start, end = self._weather.index[0], self._weather.index[-1] + pd.Timedelta(hours=1)
        self.times = pd.date_range(start, end, freq=f"{REPORT_MINUTES}min", inclusive="left")

    def sensors(self) -> list[SensorRef]:
        return all_sensors()

    def _hourly(self, sensor: SensorRef) -> pd.Series:
        st, m = sensor.station_code, sensor.measurement
        if st.startswith("FS-"):
            b = self._blocks[self._blocks["block_code"] == st[3:]]
            return b[m]
        if st in ("WX-CORE", "DRY-01"):
            if m == "rain_detected":
                return (self._weather["rainfall"] > 0).astype(float)
            return self._weather[m]
        if st == "RES-01":
            return self._water["reservoir_level_m"]
        if st == "STR2-01":
            return self._water["stream2_flow_ls"]
        if st.startswith("CORR-"):
            w = self._wildlife[self._wildlife["station_code"] == st]
            return w["passes"].astype(float)
        raise KeyError(sensor.code)

    def series(self, sensor: SensorRef) -> pd.Series:
        hourly = self._hourly(sensor)
        if sensor.measurement in AMOUNTS:
            values = hourly.to_numpy()
            if sensor.measurement == "rainfall":  # spread evenly over the hour
                split = np.repeat(values / PER_HOUR, PER_HOUR)
            else:  # whole animal passes, scattered across the hour's intervals
                rng = stream(self.seed, f"truth-split-{sensor.code}")
                split = np.concatenate(
                    [rng.multinomial(int(v), [1 / PER_HOUR] * PER_HOUR) for v in values]
                ).astype(float)
            return pd.Series(split, index=self.times, name=sensor.code)
        # State variables: linear interpolation between the hourly values.
        upsampled = hourly.reindex(self.times).interpolate(method="time").ffill()
        if sensor.measurement == "rain_detected":
            upsampled = hourly.reindex(self.times).ffill()  # a yes/no state, not a blend
        return upsampled.rename(sensor.code)
