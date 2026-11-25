"""Wildlife along the forest corridor -- the ground truth behind the motion/IR sensors.

* Background: deer, wild boar, porcupine and civets move mostly at night, so each
  corridor sensor sees a Poisson stream of passes, much higher after dark.
* Elephants: herds visit occasionally, at night, for a few hours, near one or two
  adjacent sensors. Visits are commonest in the dry season (Jan-May, when forest water
  and fodder run short) and when the coffee and jackfruit are ripe (Nov-Dec), rare in
  the monsoon. These are the events the corridor early-warning system exists for.
"""

from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from coffwe.estate_data import STATIONS
from coffwe.sim.climate import TIMEZONE

CORRIDOR = [s.code for s in STATIONS if s.station_type == "corridor"]
NIGHT_HOURS = set(range(19, 24)) | set(range(0, 6))
BACKGROUND_PER_H = {"night": 0.4, "day": 0.05}
ELEPHANT_VISIT_PER_NIGHT = {1: 0.06, 2: 0.06, 3: 0.06, 4: 0.05, 5: 0.04, 6: 0.01, 7: 0.01,
                            8: 0.01, 9: 0.01, 10: 0.02, 11: 0.04, 12: 0.04}  # fmt: skip
ELEPHANT_PASSES_PER_H = 6.0


def simulate_wildlife(start: date, end: date, rng: np.random.Generator):
    """Returns (hourly passes per corridor sensor, list of elephant visits)."""
    hours = pd.date_range(start, end, freq="h", inclusive="left", tz=TIMEZONE)
    n, k = len(hours), len(CORRIDOR)
    night = np.isin(hours.hour, list(NIGHT_HOURS))
    rate = np.where(night, BACKGROUND_PER_H["night"], BACKGROUND_PER_H["day"])[:, None]
    rate = np.repeat(rate, k, axis=1)
    elephants = np.zeros((n, k), dtype=bool)
    visits = []
    day = start
    while day < end:
        if rng.random() < ELEPHANT_VISIT_PER_NIGHT[day.month]:
            begin = datetime.combine(day, time(int(rng.integers(19, 24))))
            duration = int(rng.integers(2, 6))
            first = int(rng.integers(0, k))
            sensors = [first] + ([first + 1] if first + 1 < k and rng.random() < 0.5 else [])
            herd = int(rng.integers(2, 9))
            start_ts = pd.Timestamp(begin).tz_localize(TIMEZONE)
            idx = hours.get_indexer(pd.date_range(start_ts, periods=duration, freq="h"))
            idx = idx[idx >= 0]
            for s in sensors:
                elephants[idx, s] = True
                rate[idx, s] += ELEPHANT_PASSES_PER_H
            visits.append({"start": start_ts, "hours": duration, "herd_size": herd,
                           "stations": ",".join(CORRIDOR[s] for s in sensors)})  # fmt: skip
        day += timedelta(days=1)
    passes = rng.poisson(rate)
    frame = pd.DataFrame(
        {
            "station_code": np.tile(CORRIDOR, n),
            "passes": passes.ravel(),
            "elephants": elephants.ravel(),
        },
        index=pd.Index(np.repeat(hours, k), name="ts"),
    )
    return frame, visits
