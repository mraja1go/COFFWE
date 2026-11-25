"""Hourly soil water balance and microclimate for every block ("bucket" model).

Each block's root zone is a bucket holding S mm of water, between the wilting point
(plants can't extract more) and saturation (every pore full). Each hour, water moves
in this order:

    rain + irrigation
      -> canopy interception   (leaves catch the first mm; that water evaporates back)
      -> infiltration          (limited by how fast the soil can absorb, and by room left)
      -> runoff                (whatever couldn't get in -- worse on steep blocks)
      -> evapotranspiration    (wet leaves first, then plants draw from the soil)
      -> drainage              (water above field capacity seeps below the roots)

Water is conserved exactly: every mm of input ends up in the canopy store, the soil
store, evaporation, runoff or drainage. The tests check that to the micro-millimetre.

All 10 blocks are stepped together as numpy arrays, one hour at a time.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from coffwe.estate_data import BLOCKS, BlockSpec
from coffwe.sim import climate as C


@dataclass(frozen=True)
class SoilType:
    field_capacity: float  # m3/m3 -- what's left after free drainage (the bucket's "rim")
    wilting_point: float  # m3/m3 -- below this, roots can't pull water out
    saturation: float  # m3/m3 -- every pore full
    root_depth_m: float  # depth of the root-zone bucket
    ksat_mm_h: float  # max infiltration rate when wet (saturated hydraulic conductivity)
    drain_rate_per_h: float  # fraction of the water above field capacity draining per hour


# Typical values for these textures; thin rocky soils are shallow buckets that shed water.
SOIL_TYPES: dict[str, SoilType] = {
    "Alluvial-lateritic loam":       SoilType(0.36, 0.18, 0.48, 1.2, 12, 0.03),
    "Red lateritic loam":            SoilType(0.32, 0.17, 0.46, 1.0, 15, 0.04),
    "Lateritic loam, well-drained":  SoilType(0.30, 0.15, 0.45, 1.0, 25, 0.06),
    "Lateritic loam":                SoilType(0.32, 0.17, 0.46, 1.0, 15, 0.04),
    "Thin rocky lateritic":          SoilType(0.26, 0.13, 0.40, 0.5,  8, 0.08),
    "Loam":                          SoilType(0.34, 0.16, 0.47, 1.0, 13, 0.04),
    "Sandy loam":                    SoilType(0.24, 0.10, 0.42, 0.9, 30, 0.10),
    "Well-drained lateritic loam":   SoilType(0.30, 0.15, 0.45, 1.0, 25, 0.06),
    "Thin lateritic / forest humus": SoilType(0.30, 0.14, 0.50, 0.6, 20, 0.06),
}  # fmt: skip

STEEP_INFILTRATION_FACTOR = 0.5  # steep slopes: water runs off before it can soak in
COFFEE_CANOPY_STORAGE_MM = 0.6  # rain a coffee canopy alone can hold
SHADE_CANOPY_STORAGE_MM = 1.6  # extra a full shade-tree canopy holds
LEAF_WET_THRESHOLD_MM = 0.05  # canopy water above this counts as "leaves wet"
STRESS_DEPLETION_FRACTION = 0.45  # FAO-56 "p" for coffee: stress starts past 45% depletion
SHADE_MIDDAY_COOLING_C = 3.0  # full shade, full sun: canopy air this much cooler
SOIL_TEMP_LAG_HOURS = 24  # soil at ~15 cm follows air temperature with ~a day's lag
PRIESTLEY_TAYLOR_ALPHA = 1.26
NET_RADIATION_FRACTION = 0.6  # net radiation as a fraction of incoming shortwave (daytime)
INITIAL_AVAILABLE_FRACTION = 0.6  # 1 Jan: soils still 60% "full" after the Oct-Nov rains

HOURLY_COLUMNS = [
    "air_temperature",  # degC, block canopy air (lapse rate + shade cooling)
    "relative_humidity",  # %
    "soil_moisture",  # m3/m3, root-zone volumetric water content
    "soil_temperature",  # degC
    "leaf_wetness",  # %, 0-100 like a leaf-wetness sensor
    "rain_mm",  # incident rain
    "irrigation_mm",
    "interception_mm",  # caught by the canopy this hour
    "throughfall_mm",  # reached the ground
    "runoff_mm",
    "infiltration_mm",
    "et_soil_mm",  # transpiration + soil evaporation drawn from the bucket
    "canopy_evap_mm",  # intercepted water evaporated back
    "drainage_mm",
    "canopy_store_mm",  # water sitting on leaves at the end of the hour
    "water_stress",  # 1 = unstressed, 0 = at wilting point (FAO-56 Ks)
]


def saturation_vapour_pressure_kpa(t_c: np.ndarray) -> np.ndarray:
    """Tetens equation: how much water vapour air at t_c can hold."""
    return 0.6108 * np.exp(17.27 * t_c / (t_c + 237.3))


def priestley_taylor_mm_h(t_c, solar_wm2, pressure_hpa):
    """Reference evapotranspiration for one hour, mm.

    ET0 = alpha * delta/(delta + gamma) * Rn / lambda. Energy-limited evaporation:
    in a humid climate like the Ghats the air is rarely dry enough for wind and vapour
    deficit to dominate, which is exactly where Priestley-Taylor works well.
    """
    es = saturation_vapour_pressure_kpa(t_c)
    delta = 4098 * es / (t_c + 237.3) ** 2  # slope of the saturation curve, kPa/degC
    gamma = 0.000665 * pressure_hpa / 10  # psychrometric constant, kPa/degC
    rn_mj = NET_RADIATION_FRACTION * solar_wm2 * 0.0036  # W/m2 over an hour -> MJ/m2
    return np.maximum(PRIESTLEY_TAYLOR_ALPHA * delta / (delta + gamma) * rn_mj / 2.45, 0.0)


class SoilModel:
    """State and hourly physics for all blocks at once (index i = BLOCKS[i])."""

    def __init__(self, blocks: list[BlockSpec] = BLOCKS) -> None:
        self.blocks = blocks
        soils = [SOIL_TYPES[b.soil] for b in blocks]
        depth_mm = np.array([s.root_depth_m for s in soils]) * 1000
        # Bucket levels in mm of water: theta (m3/m3) x depth (mm).
        self.s_wp = np.array([s.wilting_point for s in soils]) * depth_mm
        self.s_fc = np.array([s.field_capacity for s in soils]) * depth_mm
        self.s_sat = np.array([s.saturation for s in soils]) * depth_mm
        self.depth_mm = depth_mm
        steep = np.array([b.slope == "steep" for b in blocks])
        self.infiltration_cap = np.array([s.ksat_mm_h for s in soils]) * np.where(
            steep, STEEP_INFILTRATION_FACTOR, 1.0
        )
        self.drain_rate = np.array([s.drain_rate_per_h for s in soils])
        self.shade = np.array([b.shade_pct for b in blocks]) / 100
        self.canopy_capacity = COFFEE_CANOPY_STORAGE_MM + SHADE_CANOPY_STORAGE_MM * self.shade
        self.crop_coefficient = 0.80 + 0.15 * self.shade  # shade trees transpire too
        self.elevation = np.array([(b.elevation_min_m + b.elevation_max_m) / 2 for b in blocks])

        # State
        self.soil = self.s_wp + INITIAL_AVAILABLE_FRACTION * (self.s_fc - self.s_wp)
        self.canopy = np.zeros(len(blocks))
        self.soil_temp: np.ndarray | None = None

    def step(
        self,
        rain: float,
        air_temp: float,
        rh: float,
        solar: float,
        pressure: float,
        irrigation: np.ndarray | None = None,
    ) -> dict[str, np.ndarray]:
        """Advance one hour from estate-mast weather. Returns this hour's block values."""
        n = len(self.blocks)
        irrigation = np.zeros(n) if irrigation is None else irrigation
        water_in = rain + irrigation

        # --- microclimate: elevation, then shade cooling in proportion to sunshine
        t_block = air_temp + C.LAPSE_RATE_C_PER_M * (self.elevation - C.REFERENCE_ELEVATION_M)
        t_block = t_block - SHADE_MIDDAY_COOLING_C * self.shade * (solar / 1000)
        # Same water vapour in the air, different temperature -> different RH.
        vapour = rh / 100 * saturation_vapour_pressure_kpa(np.array(air_temp))
        rh_block = np.clip(100 * vapour / saturation_vapour_pressure_kpa(t_block), 0, 100)
        lag = 1 / SOIL_TEMP_LAG_HOURS
        canopy_soil_t = t_block - 1.0 * self.shade  # shaded ground stays cooler
        if self.soil_temp is None:
            self.soil_temp = canopy_soil_t.copy()
        self.soil_temp = self.soil_temp + lag * (canopy_soil_t - self.soil_temp)

        # --- interception
        interception = np.minimum(water_in, self.canopy_capacity - self.canopy)
        self.canopy = self.canopy + interception
        throughfall = water_in - interception

        # --- infiltration and runoff
        room = self.s_sat - self.soil
        infiltration = np.minimum(throughfall, np.minimum(self.infiltration_cap, room))
        runoff = throughfall - infiltration
        self.soil = self.soil + infiltration

        # --- evapotranspiration: wet canopy evaporates first, the rest from the soil
        et0 = priestley_taylor_mm_h(t_block, solar, pressure)
        canopy_evap = np.minimum(self.canopy, et0)
        self.canopy = self.canopy - canopy_evap
        stress = self._water_stress()
        demand = np.maximum(self.crop_coefficient * et0 - canopy_evap, 0) * stress
        et_soil = np.minimum(demand, np.maximum(self.soil - self.s_wp, 0))
        self.soil = self.soil - et_soil

        # --- drainage of water above field capacity
        drainage = self.drain_rate * np.maximum(self.soil - self.s_fc, 0)
        self.soil = self.soil - drainage

        # --- leaf wetness: rain-wet leaves, or dew when the canopy air nears saturation
        dew = np.clip((rh_block - 90) / 5, 0, 1)
        leaf_wet = np.where(self.canopy > LEAF_WET_THRESHOLD_MM, 1.0, dew)

        return {
            "air_temperature": t_block,
            "relative_humidity": rh_block,
            "soil_moisture": self.soil / self.depth_mm,
            "soil_temperature": self.soil_temp.copy(),
            "leaf_wetness": 100 * leaf_wet,
            "rain_mm": np.full(n, rain),
            "irrigation_mm": irrigation,
            "interception_mm": interception,
            "throughfall_mm": throughfall,
            "runoff_mm": runoff,
            "infiltration_mm": infiltration,
            "et_soil_mm": et_soil,
            "canopy_evap_mm": canopy_evap,
            "drainage_mm": drainage,
            "canopy_store_mm": self.canopy.copy(),
            "water_stress": self._water_stress(),
        }

    def _water_stress(self) -> np.ndarray:
        """FAO-56 Ks: 1 until a fraction p of the available water is used, then linear to 0.

        Plants don't slow down the moment soil starts drying -- only once it gets hard
        enough to pull water out that they partly close their leaf pores.
        """
        available = self.s_fc - self.s_wp
        readily_available = STRESS_DEPLETION_FRACTION * available
        ks = (self.soil - self.s_wp) / (available - readily_available)
        return np.clip(ks, 0.0, 1.0)


def to_frame(timestamps: pd.DatetimeIndex, hours: list[dict[str, np.ndarray]]) -> pd.DataFrame:
    """Hourly block results -> long table: one row per (hour, block)."""
    n_blocks = len(BLOCKS)
    data = {col: np.concatenate([h[col] for h in hours]) for col in HOURLY_COLUMNS}
    frame = pd.DataFrame(data).round(4)
    frame.insert(0, "block_code", np.tile([b.code for b in BLOCKS], len(hours)))
    frame.index = pd.Index(np.repeat(timestamps, n_blocks), name="ts")
    return frame
