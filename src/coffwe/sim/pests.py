"""Coffee berry borer and coffee leaf rust, stepped daily per (block, variety).

Coffee berry borer (CBB, Hypothenemus hampei)
  The female bores into a berry and raises her brood inside it. One generation needs
  ~311 degree-days above 14.9 degC, and breeding is only possible while there are
  *host* berries: berries older than ~120 days on the branch, ripe/over-ripe cherry,
  and the fallen berries on the ground (the off-season reservoir that carries the
  pest from one crop to the next -- which is why collecting them, "gleaning", works).
  State is an infestation-pressure index P; the share of berries bored is 1 - e^-P.
  Picking removes the borers inside the picked berries; heavy rain, starvation
  between crops, pheromone trapping and Beauveria bassiana (a fungus that infects the
  beetles) kill them. Pheromone traps catch flying females -- flight peaks when the
  host berries are used up, after harvest -- giving the weekly trap counts that are
  the estate's *observable* of an otherwise hidden population.

Coffee leaf rust (Hemileia vastatrix)
  Spores infect a leaf only through a film of water: ~6+ wet hours at 15-28 degC
  (best ~22 degC). Infections incubate for a temperature-dependent latent period
  (~4 weeks) before the lesions sporulate and spread rust further; heavily rusted
  leaves drop. Lesioned + fallen leaf area cuts the bush's ability to fill its
  berries. Bordeaux mixture (copper) protects leaves for a few weeks, washed off by
  rain. Robusta is largely resistant.
"""

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

from coffwe.estate_data import BLOCKS, BlockSpec
from coffwe.sim.coffee import CoffeeModel

# --- berry borer -------------------------------------------------------------------
# Calibration v2 (2026-11-25): unmanaged infestation ~20-30% by harvest, survival
# through the dry season in leftover/fallen berries; rust transmission so that
# susceptible Arabica reaches ~30-50% incidence after an unmanaged monsoon.
CBB_BASE_TEMP_C = 14.9
CBB_DD_PER_GENERATION = 311.0
CBB_GROWTH_PER_GENERATION = 12.0  # population multiplier per generation, hosts plentiful
CBB_HOST_FULL_KG_PER_PLANT = 0.2  # this much host cherry per plant = unlimited breeding
CBB_RESERVOIR_HOST_WEIGHT = 0.5  # fallen berries are poorer hosts than berries on the bush
CBB_RESERVOIR_DECAY_PER_DAY = 1 / 90  # fallen berries dry out and persist ~3 months
CBB_MORTALITY_PER_DAY = 0.005
CBB_HEAVY_RAIN_MM, CBB_HEAVY_RAIN_MORTALITY = 20.0, 0.02
CBB_STARVATION_MORTALITY = 0.01  # when almost no host berries are left
CBB_TRAPPING_MORTALITY = 0.002  # pheromone traps remove some flying females
CBB_PRESSURE_FLOOR = 0.003  # immigration from neighbouring estates/forest
CBB_PICK_REMOVAL = 0.8  # share of borers in picked berries that leave with the crop
ROBUSTA_CBB_FACTOR = 0.8
TRAPS_PER_ACRE = 2
TRAP_CATCH_SCALE = 300.0  # expected catch per trap per day at P = 1, full flight
BEAUVERIA_MORTALITY, BEAUVERIA_DAYS = 0.06, 10
GLEANING_RESERVOIR_LEFT, GLEANING_PRESSURE_LEFT = 0.2, 0.85

# --- leaf rust ---------------------------------------------------------------------
RUST_OPTIMUM_C, RUST_RANGE_C = 22.0, 7.0  # infection weight falls to 0 at 15 and 29 degC
RUST_MIN_WET_HOURS = 4.0  # weighted wet hours needed before any infection takes
RUST_FULL_WET_HOURS = 18.0
RUST_TRANSMISSION = 0.16
RUST_BACKGROUND_SPORES = 0.003  # spores blowing in from outside
RUST_LEAF_FALL_PER_DAY = 1 / 60  # sporulating leaves drop
RUST_REGROWTH_PER_DAY = 1 / 120  # new flushes replace fallen leaves
RUST_SUSCEPTIBILITY = {"AR": 1.0, "RB": 0.15}
BORDEAUX_EFFICACY = 0.85
BORDEAUX_DECAY_PER_DAY, BORDEAUX_RAIN_WASH_PER_MM = 1 / 35, 0.0015


def latent_days(tmean_c: float) -> int:
    """Incubation shortens in warmth: ~28 days at 22 degC, ~2 days longer per degree cooler."""
    return int(np.clip(round(28 + 2 * (RUST_OPTIMUM_C - tmean_c)), 20, 50))


def rust_temperature_weight(t_c: np.ndarray) -> np.ndarray:
    return np.clip(1 - np.abs(t_c - RUST_OPTIMUM_C) / RUST_RANGE_C, 0, 1)


def rust_infection_potential(weighted_wet_hours: float) -> float:
    """0 for short wet spells, rising to 1 once leaves stay wet most of the day."""
    span = RUST_FULL_WET_HOURS - RUST_MIN_WET_HOURS
    return float(np.clip((weighted_wet_hours - RUST_MIN_WET_HOURS) / span, 0, 1))


@dataclass
class PestUnit:
    block: BlockSpec
    variety: str
    plants: int
    # borer
    pressure: float = 0.01
    reservoir_kg: float = 0.0
    beauveria_until: date | None = None
    gleaned_season: int | None = None
    # rust (shares of the leaf area)
    latent: list[tuple[date, float]] = field(default_factory=list)  # (sporulates_on, share)
    sporulating: float = 0.04
    defoliated: float = 0.02
    protection: float = 0.0
    infection_potential: list[float] = field(default_factory=list)  # recent days

    @property
    def code(self) -> str:
        return f"{self.block.code}-{self.variety}"

    @property
    def infestation(self) -> float:
        return 1 - math.exp(-self.pressure)

    @property
    def rust_incidence(self) -> float:
        """Share of leaves carrying lesions (incubating or sporulating)."""
        return min(sum(s for _, s in self.latent) + self.sporulating, 1.0)

    @property
    def leaf_health(self) -> float:
        return float(np.clip(1 - 0.8 * self.defoliated - 0.3 * self.sporulating, 0.3, 1.0))

    def rust_risk(self, days: int = 14) -> float:
        return float(sum(self.infection_potential[-days:]))


class PestModel:
    def __init__(self, rng: np.random.Generator, coffee: CoffeeModel) -> None:
        self.rng = rng
        self.units = [PestUnit(u.block, u.variety, u.plants) for u in coffee.units]
        for p in self.units:  # rust starts low on resistant Robusta
            if p.variety == "RB":
                p.sporulating, p.defoliated = 0.005, 0.0
            p.pressure = float(rng.uniform(0.005, 0.02))
        self.traps = {
            b.code: max(1, round(TRAPS_PER_ACRE * float(b.area_acres - b.buffer_acres)))
            for b in BLOCKS
        }
        self._week_catch: dict[str, float] = dict.fromkeys(self.traps, 0.0)
        self.daily: list[dict] = []
        self.trap_counts: list[dict] = []
        self.trapping = True  # traps in place (switched off in the no-IPM counterfactual)

    def unit(self, code: str) -> PestUnit:
        return next(p for p in self.units if p.code == code)

    def block_units(self, block_code: str) -> list[PestUnit]:
        return [p for p in self.units if p.block.code == block_code]

    # --- daily step ----------------------------------------------------------------

    def on_day(
        self,
        day: date,
        coffee: CoffeeModel,
        tmean_c: np.ndarray,  # per block
        rust_wet_hours: np.ndarray,  # per block: temperature-weighted leaf-wet hours
        rain_mm: float,
        fallen_kg: dict[str, float],
        picked_kg: dict[str, float],
    ) -> None:
        index = {b.code: i for i, b in enumerate(BLOCKS)}
        for p in self.units:
            i = index[p.block.code]
            self._borer(
                p,
                day,
                coffee,
                float(tmean_c[i]),
                rain_mm,
                fallen_kg.get(p.code, 0.0),
                picked_kg.get(p.code, 0.0),
            )
            self._rust(p, day, float(tmean_c[i]), float(rust_wet_hours[i]), rain_mm)
            coffee.borer_infestation[p.code] = p.infestation
            coffee.leaf_health[p.code] = p.leaf_health
            self.daily.append(
                {
                    "day": day,
                    "block_code": p.block.code,
                    "variety": p.variety,
                    "cbb_pressure": round(p.pressure, 5),
                    "cbb_infestation": round(p.infestation, 4),
                    "cbb_reservoir_kg": round(p.reservoir_kg, 1),
                    "rust_incidence": round(p.rust_incidence, 4),
                    "rust_sporulating": round(p.sporulating, 4),
                    "rust_defoliated": round(p.defoliated, 4),
                    "rust_risk_14d": round(p.rust_risk(), 2),
                    "bordeaux_protection": round(p.protection, 3),
                    "leaf_health": round(p.leaf_health, 3),
                }
            )
        self._traps(day, coffee, rain_mm)

    def _host_index(self, p: PestUnit, coffee: CoffeeModel) -> float:
        host_kg = coffee.susceptible_kg(p.code) + CBB_RESERVOIR_HOST_WEIGHT * p.reservoir_kg
        return min(host_kg / p.plants / CBB_HOST_FULL_KG_PER_PLANT, 1.0)

    def _borer(self, p, day, coffee, tmean, rain, fallen, picked) -> None:
        host = self._host_index(p, coffee)
        dd = max(tmean - CBB_BASE_TEMP_C, 0.0)
        factor = ROBUSTA_CBB_FACTOR if p.variety == "RB" else 1.0
        growth = math.log(CBB_GROWTH_PER_GENERATION) / CBB_DD_PER_GENERATION * dd * host * factor
        mortality = CBB_MORTALITY_PER_DAY
        mortality += CBB_HEAVY_RAIN_MORTALITY if rain > CBB_HEAVY_RAIN_MM else 0.0
        mortality += CBB_STARVATION_MORTALITY if host < 0.02 else 0.0
        mortality += CBB_TRAPPING_MORTALITY if self.trapping else 0.0
        if p.beauveria_until and day <= p.beauveria_until:
            mortality += BEAUVERIA_MORTALITY
        p.pressure *= math.exp(growth - mortality)
        # Borers leave with the picked crop (they're inside the berries).
        on_branch = coffee.susceptible_kg(p.code) + picked
        if picked > 0 and on_branch > 0:
            p.pressure *= 1 - CBB_PICK_REMOVAL * picked / on_branch
        p.pressure = max(p.pressure, CBB_PRESSURE_FLOOR)
        p.reservoir_kg = p.reservoir_kg * (1 - CBB_RESERVOIR_DECAY_PER_DAY) + fallen

    def _rust(self, p, day, tmean, wet_hours, rain) -> None:
        e = rust_infection_potential(wet_hours)
        p.infection_potential = [*p.infection_potential[-29:], e]
        healthy = max(1 - p.rust_incidence - p.defoliated, 0.0)
        new = (
            RUST_TRANSMISSION * e * RUST_SUSCEPTIBILITY[p.variety]
            * (p.sporulating + RUST_BACKGROUND_SPORES) * healthy
            * (1 - BORDEAUX_EFFICACY * p.protection)
        )  # fmt: skip
        if new > 0:
            p.latent.append((day + timedelta(days=latent_days(tmean)), new))
        matured = sum(s for d, s in p.latent if d <= day)
        p.latent = [(d, s) for d, s in p.latent if d > day]
        p.sporulating += matured
        fall = p.sporulating * RUST_LEAF_FALL_PER_DAY
        p.sporulating -= fall
        p.defoliated = p.defoliated + fall - p.defoliated * RUST_REGROWTH_PER_DAY
        p.protection = max(
            p.protection - BORDEAUX_DECAY_PER_DAY - BORDEAUX_RAIN_WASH_PER_MM * rain, 0.0
        )

    def _traps(self, day: date, coffee: CoffeeModel, rain: float) -> None:
        if not self.trapping:
            return
        for block_code in self.traps:
            units = self.block_units(block_code)
            plants = sum(p.plants for p in units)
            pressure = sum(p.pressure * p.plants for p in units) / plants
            host = sum(self._host_index(p, coffee) * p.plants for p in units) / plants
            # Females fly to find new berries: most when the old ones are used up,
            # hardly at all in rain.
            flight = (0.3 + 0.7 * (1 - host)) * (0.2 if rain > 5 else 1.0)
            self._week_catch[block_code] += TRAP_CATCH_SCALE * pressure * flight
        if day.weekday() == 5:  # traps are emptied and counted on Saturdays
            for block_code, n_traps in self.traps.items():
                expected = self._week_catch[block_code] * n_traps
                self.trap_counts.append(
                    {
                        "day": day,
                        "block_code": block_code,
                        "traps": n_traps,
                        "catch": int(self.rng.poisson(expected)),
                    }
                )
                self._week_catch[block_code] = 0.0

    # --- interventions (decided by management) -------------------------------------

    def spray_beauveria(self, block_code: str, day: date) -> None:
        for p in self.block_units(block_code):
            p.beauveria_until = day + timedelta(days=BEAUVERIA_DAYS)

    def spray_bordeaux(self, block_code: str) -> None:
        for p in self.block_units(block_code):
            p.protection = 1.0

    def glean(self, block_code: str, season: int) -> None:
        for p in self.block_units(block_code):
            p.reservoir_kg *= GLEANING_RESERVOIR_LEFT
            p.pressure = max(p.pressure * GLEANING_PRESSURE_LEFT, CBB_PRESSURE_FLOOR)
            p.gleaned_season = season
