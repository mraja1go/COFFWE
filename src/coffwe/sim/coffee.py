"""Coffee phenology and yield, stepped once a day per (block, variety).

The annual cycle, as modelled:

1. Dormancy -- flower buds sit dormant through the dry season. They become ready to
   open once the block has had DORMANCY_DRY_DAYS rainless days since 1 December.
2. Blossom -- within the blossom window, a "blossom shower" (rain + irrigation over
   two days >= the variety's threshold) opens a share of the remaining buds
   BLOSSOM_DELAY_DAYS later. Each shower creates a *cohort* of flowers; several
   cohorts are what spread ripening over weeks. Buds still closed when the window
   ends are lost.
3. Fruit set -- water stress in the weeks after blossom reduces how many flowers set
   fruit (hence the importance of a "backing shower"). Robusta needs insects to carry
   pollen between plants, so heavy rain on its blossom day costs some set too.
4. Fill -- water stress during berry expansion reduces final cherry weight.
5. Ripening -- driven by thermal time (degree-days above 10 degC). Each cohort ripens
   along a normal distribution around its degree-day target, so cherries turn red
   over a few weeks rather than all at once.
6. Ripe pool -- ripe cherries hang RIPE_DAYS, then turn over-ripe for OVERRIPE_DAYS,
   then fall. Picking (M4) takes from this pool; whatever isn't picked is lost.

All masses are fresh cherry, kg.
"""

from dataclasses import dataclass, field
from datetime import date

import numpy as np
from scipy.stats import norm

from coffwe.estate_data import BLOCKS, BlockSpec


@dataclass(frozen=True)
class VarietyParams:
    blossom_threshold_mm: float  # 2-day rain + irrigation that opens the buds
    ripening_dd: float  # degree-days (base 10 degC) from blossom to peak ripeness
    potential_kg_per_plant: float  # fresh cherry per plant per year, unstressed, ideal shade
    pollination_rain_mm: float | None  # rain on blossom day above this hurts fruit set


VARIETIES: dict[str, VarietyParams] = {
    # Arabica self-pollinates; ripens Nov-Jan on these slopes (peak ~mid-Dec).
    "AR": VarietyParams(20.0, 3130.0, 3.8, None),
    # Robusta needs cross-pollination; ripens Dec-Feb (peak ~mid-Jan).
    "RB": VarietyParams(25.0, 3460.0, 4.5, 10.0),
}

BASE_TEMP_C = 10.0
DORMANCY_DRY_DAYS = 30  # rainless days since 1 Dec before buds can respond
ASSUMED_DRY_DAYS_ON_1_JAN = 31  # spin-up starts 1 Jan: assume a dry December
BLOSSOM_WINDOW = ((1, 20), (5, 15))  # (month, day) earliest trigger .. last trigger
BLOSSOM_DELAY_DAYS = 9  # flowers open ~9 days after the triggering shower
BUDS_OPENED_PER_SHOWER = 0.75  # share of the remaining buds each blossom shower opens
FRUIT_SET_DAYS = 21  # stress window after opening that decides fruit set
SET_FLOOR = 0.65  # fruit set even at wilting point (flowers already committed)
FILL_WINDOW_DAYS = (60, 150)  # berry expansion: stress here reduces cherry weight
RIPENING_SPREAD = 0.05  # sd of ripening, as a fraction of the degree-day target
RIPE_DAYS = 12  # ripe on the branch...
OVERRIPE_DAYS = 10  # ...then over-ripe, then it falls
POLLINATION_RAIN_PENALTY = 0.8
YEAR_VARIATION_SD = 0.08  # unexplained block-to-block, year-to-year yield variation
BORER_SUSCEPTIBLE_AFTER_DAYS = 120  # berries harden enough for the borer to enter
CURRENT_SEASON_MIN_AGE_DAYS = 200  # older cohorts belong to the harvest under way
BORER_WEIGHT_LOSS = 0.35  # share of a bored berry's weight lost (bean eaten, early drop)
RUST_CARRY_OVER = 0.5  # next-year bud loss per unit of leaf area lost to rust


def shade_yield_factor(shade_pct: float) -> float:
    """Open sun gives higher potential yield; dense shade a little less (but less stress)."""
    return 1.15 - 0.25 * shade_pct / 100


@dataclass
class Cohort:
    """The flowers that opened on one day in one (block, variety)."""

    opened_on: date
    flowers_share: float  # share of the year's full bud load
    potential_kg: float  # crop if set and fill are perfect
    days_since_open: int = 0
    degree_days: float = 0.0
    stress_set: list[float] = field(default_factory=list)
    stress_fill: list[float] = field(default_factory=list)
    set_factor: float | None = None  # decided after FRUIT_SET_DAYS
    ripened_share: float = 0.0  # cumulative normal CDF reached so far
    pollination_penalty: float = 1.0

    def fill_factor(self) -> float:
        return 0.7 + 0.3 * float(np.mean(self.stress_fill)) if self.stress_fill else 1.0

    def final_kg(self) -> float:
        return self.potential_kg * (self.set_factor or 1.0) * self.fill_factor()


@dataclass
class Unit:
    """One variety within one block, e.g. ("B4", "AR")."""

    block: BlockSpec
    variety: str
    plants: int
    buds_remaining: float = 1.0
    dry_days: int = ASSUMED_DRY_DAYS_ON_1_JAN
    recent_water: list[float] = field(default_factory=lambda: [0.0])
    pending_openings: list[tuple[date, float, float]] = field(default_factory=list)
    cohorts: list[Cohort] = field(default_factory=list)
    # ripe pool by age in days since ripening: [0, RIPE_DAYS) ripe, then over-ripe
    pool: np.ndarray = field(default_factory=lambda: np.zeros(RIPE_DAYS + OVERRIPE_DAYS))
    year_factor: float = 1.0

    @property
    def code(self) -> str:
        return f"{self.block.code}-{self.variety}"

    @property
    def ripe_kg(self) -> float:
        return float(self.pool[:RIPE_DAYS].sum())

    @property
    def overripe_kg(self) -> float:
        return float(self.pool[RIPE_DAYS:].sum())

    def green_kg(self) -> float:
        return sum(c.final_kg() * (1 - c.ripened_share) for c in self.cohorts)


def _in_window(day: date) -> bool:
    (m0, d0), (m1, d1) = BLOSSOM_WINDOW
    return date(day.year, m0, d0) <= day <= date(day.year, m1, d1)


class CoffeeModel:
    def __init__(self, rng: np.random.Generator, blocks: list[BlockSpec] = BLOCKS) -> None:
        self.rng = rng
        self.blocks = blocks
        self.units: list[Unit] = [
            Unit(block=b, variety=v, plants=b.plantings[v])
            for b in blocks
            for v in ("AR", "RB")
            if b.plantings.get(v)
        ]
        self.blossom_log: list[dict] = []
        # Hooks set by the pest model (M6); neutral defaults leave the crop untouched.
        self.borer_infestation: dict[str, float] = {}  # unit -> share of berries bored
        self.leaf_health: dict[str, float] = {}  # unit -> 1 healthy .. lower = rust-damaged
        self._new_bud_year(self.units)

    def _new_bud_year(self, units: list[Unit]) -> None:
        for u in units:
            u.buds_remaining = 1.0
            # Next season's flower buds form on this season's leaves: a bush stripped by
            # leaf rust sets fewer of them (rust's main yield damage comes a year later).
            leaves = self.leaf_health.get(u.code, 1.0)
            carry_over = 1 - RUST_CARRY_OVER * (1 - leaves)
            u.year_factor = float(self.rng.lognormal(0, YEAR_VARIATION_SD)) * carry_over

    def unit(self, code: str) -> Unit:
        """Look up a unit by its code, e.g. "B4-AR"."""
        return next(u for u in self.units if u.code == code)

    def pick(self, code: str, kg: float) -> tuple[float, float]:
        """Selective picking: take up to `kg` of ripe + over-ripe cherry off the branches.

        Pickers strip everything red or darker from the branches they work, so every age
        in the pool is reduced by the same fraction. Returns (ripe_kg, overripe_kg) picked.
        """
        u = self.unit(code)
        available = float(u.pool.sum())
        if available <= 0 or kg <= 0:
            return 0.0, 0.0
        fraction = min(kg / available, 1.0)
        taken = u.pool * fraction
        u.pool = u.pool - taken
        return float(taken[:RIPE_DAYS].sum()), float(taken[RIPE_DAYS:].sum())

    def susceptible_kg(self, code: str) -> float:
        """Cherry a berry borer can breed in: berries past ~120 days on the branch
        (hard enough to bore) plus everything ripe or over-ripe."""
        u = self.unit(code)
        green = sum(
            c.final_kg() * (1 - c.ripened_share)
            for c in u.cohorts
            if c.days_since_open >= BORER_SUSCEPTIBLE_AFTER_DAYS
        )
        return green + float(u.pool.sum())

    def season_crop_left_kg(self, code: str) -> float:
        """Cherry still to come from the *current* harvest: the ripe pool plus green berries
        old enough to belong to it. The next season's young berries (blossomed this
        spring) don't count -- otherwise a harvest would never look finished."""
        u = self.unit(code)
        mature_green = sum(
            c.final_kg() * (1 - c.ripened_share)
            for c in u.cohorts
            if c.days_since_open >= CURRENT_SEASON_MIN_AGE_DAYS
        )
        return mature_green + float(u.pool.sum())

    def unblossomed(self, block_code: str, variety: str | None = None) -> bool:
        """True if this block (variety) hasn't had its main blossom yet this year."""
        return any(
            u.block.code == block_code
            and (variety is None or u.variety == variety)
            and u.buds_remaining > 1 - BUDS_OPENED_PER_SHOWER + 1e-9
            for u in self.units
        )

    def blossomed_on(self, block_code: str) -> date | None:
        """Latest blossom-trigger date this year in the block (any variety)."""
        dates = [b["triggered_on"] for b in self.blossom_log if b["block_code"] == block_code]
        return max(dates) if dates else None

    def on_day(
        self,
        day: date,
        water_mm: np.ndarray,  # rain + irrigation per block (BLOCKS order)
        rain_mm: np.ndarray,
        tmean_c: np.ndarray,
        water_stress: np.ndarray,  # daily mean FAO-56 Ks per block
    ) -> list[dict]:
        if day.month == 12 and day.day == 1:  # a new bud year begins
            for u in self.units:
                u.dry_days = 0
            self._new_bud_year(self.units)

        index = {b.code: i for i, b in enumerate(self.blocks)}
        rows = []
        for u in self.units:
            i = index[u.block.code]
            p = VARIETIES[u.variety]
            u.dry_days += int(water_mm[i] < 1.0)
            u.recent_water = [*u.recent_water[-1:], float(water_mm[i])]

            # --- blossom trigger -> flowers open BLOSSOM_DELAY_DAYS later
            if (
                _in_window(day)
                and u.dry_days >= DORMANCY_DRY_DAYS
                and u.buds_remaining > 0.01
                and sum(u.recent_water) >= p.blossom_threshold_mm
                and not any(t == day for t, _, _ in u.pending_openings)
            ):
                share = u.buds_remaining * BUDS_OPENED_PER_SHOWER
                u.buds_remaining -= share
                u.pending_openings.append((day, share, sum(u.recent_water)))
                u.recent_water = [0.0]  # the same water can't trigger twice
            if day.month == BLOSSOM_WINDOW[1][0] and day.day == BLOSSOM_WINDOW[1][1] + 1:
                u.buds_remaining = 0.0  # window closed: unopened buds are lost

            for triggered, share, mm in list(u.pending_openings):
                if (day - triggered).days >= BLOSSOM_DELAY_DAYS:
                    u.pending_openings.remove((triggered, share, mm))
                    potential = (
                        u.plants
                        * p.potential_kg_per_plant
                        * shade_yield_factor(u.block.shade_pct)
                        * u.year_factor
                        * share
                    )
                    cohort = Cohort(opened_on=day, flowers_share=share, potential_kg=potential)
                    if p.pollination_rain_mm is not None and rain_mm[i] > p.pollination_rain_mm:
                        cohort.pollination_penalty = POLLINATION_RAIN_PENALTY
                    u.cohorts.append(cohort)
                    self.blossom_log.append(
                        {
                            "block_code": u.block.code,
                            "variety": u.variety,
                            "triggered_on": triggered,
                            "opened_on": day,
                            "trigger_mm": round(mm, 1),
                            "flowers_share": round(share, 4),
                            "potential_kg": round(potential, 1),
                        }
                    )

            # --- development of every cohort on the branches
            ripened_today = 0.0
            dd_today = max(float(tmean_c[i]) - BASE_TEMP_C, 0.0)
            for c in u.cohorts:
                c.days_since_open += 1
                c.degree_days += dd_today
                if c.days_since_open <= FRUIT_SET_DAYS:
                    c.stress_set.append(float(water_stress[i]))
                    if c.days_since_open == FRUIT_SET_DAYS:
                        c.set_factor = (
                            SET_FLOOR + (1 - SET_FLOOR) * float(np.mean(c.stress_set))
                        ) * (c.pollination_penalty)
                lo, hi = FILL_WINDOW_DAYS
                if lo <= c.days_since_open <= hi:
                    # Rust-damaged, defoliated bushes fill their berries less, like drought.
                    leaves = self.leaf_health.get(u.code, 1.0)
                    c.stress_fill.append(float(water_stress[i]) * leaves)
                share = float(
                    norm.cdf(c.degree_days, p.ripening_dd, RIPENING_SPREAD * p.ripening_dd)
                )
                ripened_today += c.final_kg() * max(share - c.ripened_share, 0.0)
                c.ripened_share = max(share, c.ripened_share)
            # Fully ripened cohorts have handed all their crop to the ripe pool.
            u.cohorts = [c for c in u.cohorts if c.ripened_share < 0.9999]
            # Bored berries lose bean weight (and many drop early): lost before picking.
            borer_loss = ripened_today * BORER_WEIGHT_LOSS * self.borer_infestation.get(u.code, 0)
            ripened_today -= borer_loss

            # --- ripe pool ages one day; the oldest over-ripe cherries fall
            fallen = float(u.pool[-1])
            u.pool = np.concatenate([[ripened_today], u.pool[:-1]])

            rows.append(
                {
                    "block_code": u.block.code,
                    "variety": u.variety,
                    "buds_remaining": round(u.buds_remaining, 4),
                    "green_kg": u.green_kg(),
                    "ripened_kg": ripened_today,
                    "ripe_kg": u.ripe_kg,
                    "overripe_kg": u.overripe_kg,
                    "fallen_kg": fallen,
                    "borer_loss_kg": borer_loss,
                }
            )
        return rows
