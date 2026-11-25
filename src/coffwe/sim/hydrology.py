"""Streams and the reservoir: where the estate's runoff and drainage go.

Two linear reservoirs turn each hour's water surplus into stream flow:

    quick flow:  runoff   -> store Sq, releases kq * Sq per hour  (storm peaks, hours)
    base flow:   drainage -> store Sb, releases kb * Sb per hour  (dry-season trickle, weeks)

Flow (L/s) = specific discharge (mm/h) x catchment area (m2) / 3600. Each stream's
catchment includes the reserve forest above the estate, so it's larger than the blocks.

The Stream 1 check-dam reservoir fills from Stream 1, loses water to irrigation
withdrawals and seepage, and spills when full. Its water level (what the RES-01 gauge
reads) rises with volume less than linearly, like a bowl-shaped pond.
"""

import numpy as np
import pandas as pd

from coffwe.estate_data import BLOCKS

ACRE_M2 = 4046.86
STREAM1_CATCHMENT_HA = 100.0
STREAM2_CATCHMENT_HA = 150.0
QUICK_K_PER_H = 0.15
BASE_K_PER_H = 1 / (30 * 24)  # baseflow recedes over ~a month
DRY_SEASON_BASEFLOW_MM_H = 0.01  # ~4 L/s from Stream 2's catchment in January
RESERVOIR_CAPACITY_M3 = 12_000.0
RESERVOIR_MAX_DEPTH_M = 5.0
RESERVOIR_SEEPAGE_PER_H = 0.0002
RESERVOIR_INITIAL_FRACTION = 0.7  # 1 Jan: still fairly full after the monsoon


def _linear_reservoir(inflow_mm: np.ndarray, k: float, start_store: float) -> np.ndarray:
    out = np.empty_like(inflow_mm)
    store = start_store
    for i, x in enumerate(inflow_mm):
        store += x
        out[i] = k * store
        store -= out[i]
    return out


def simulate_water(block_hourly: pd.DataFrame) -> pd.DataFrame:
    """Hourly stream flows and reservoir state from the blocks' runoff/drainage/irrigation."""
    by_hour = block_hourly.groupby(level=0)
    runoff = by_hour["runoff_mm"].mean().to_numpy()
    drainage = by_hour["drainage_mm"].mean().to_numpy()
    # The run starts in the dry season, but the streams still carry last monsoon's
    # groundwater: start the baseflow store at a typical January trickle.
    base_start = DRY_SEASON_BASEFLOW_MM_H / BASE_K_PER_H
    specific = _linear_reservoir(runoff, QUICK_K_PER_H, 0.0) + _linear_reservoir(
        drainage, BASE_K_PER_H, base_start
    )  # mm/h over the catchment

    def litres_per_s(area_ha: float) -> np.ndarray:
        return specific * area_ha * 10_000 / 3600

    stream1, stream2 = litres_per_s(STREAM1_CATCHMENT_HA), litres_per_s(STREAM2_CATCHMENT_HA)

    area = {b.code: float(b.area_acres - b.buffer_acres) * ACRE_M2 for b in BLOCKS}
    irr = block_hourly.assign(m3=block_hourly["irrigation_mm"] / 1000
                              * block_hourly["block_code"].map(area))  # fmt: skip
    withdrawal = irr.groupby(level=0)["m3"].sum().to_numpy()

    volume = np.empty(len(stream1))
    spill = np.zeros(len(stream1))
    shortfall = np.zeros(len(stream1))
    v = RESERVOIR_INITIAL_FRACTION * RESERVOIR_CAPACITY_M3
    for i in range(len(stream1)):
        v += stream1[i] * 3.6 - withdrawal[i] - RESERVOIR_SEEPAGE_PER_H * v  # L/s -> m3/h
        if v < 0:  # irrigation asked for more than was stored (no feedback yet: Phase 8)
            shortfall[i], v = -v, 0.0
        if v > RESERVOIR_CAPACITY_M3:
            spill[i], v = v - RESERVOIR_CAPACITY_M3, RESERVOIR_CAPACITY_M3
        volume[i] = v
    level = RESERVOIR_MAX_DEPTH_M * (volume / RESERVOIR_CAPACITY_M3) ** 0.6

    return pd.DataFrame(
        {
            "stream1_flow_ls": stream1,
            "stream2_flow_ls": stream2,  # what the STR2-01 gauge measures
            "reservoir_volume_m3": volume,
            "reservoir_level_m": level,  # what the RES-01 gauge measures
            "irrigation_withdrawal_m3": withdrawal,
            "spill_m3": spill,
            "shortfall_m3": shortfall,
        },
        index=pd.Index(by_hour.size().index, name="ts"),
    ).round(4)
