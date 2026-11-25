"""Baseline estate management: the decisions a typical Western Ghats manager makes.

The simulator is the "real estate", but a real estate is also *managed*. Until the AI
brains take over decisions (Phase 5+, acting through the Phase 8 actuators), this
policy plays the estate manager, using standard Karnataka practice:

* Blossom irrigation -- if a block's coffee hasn't blossomed by a set date (rain
  failed), run the sprinklers BLOSSOM_IRRIGATION_MM to force the blossom.
* Backing irrigation -- if a blossom was forced and no meaningful rain follows within
  BACKING_AFTER_DAYS, irrigate once more so the flowers set fruit.
* Picking plan -- each picking day, send the available pickers to the blocks with the
  most ripe (and especially over-ripe) cherry.
* Reordering -- restock consumables that fall below their reorder level.
* Integrated pest management -- monthly pheromone lures; weekly scouting; Beauveria
  against berry borer and Bordeaux against leaf rust only above thresholds; gleaning
  fallen berries once a block's harvest is over.

Decisions are taken at the start of each day and applied by the soil model as
irrigation over the morning hours (when the sprinkler crew works).
"""

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

from coffwe.estate_data import BLOCKS, INVENTORY_ITEMS, apportion
from coffwe.sim.coffee import CoffeeModel
from coffwe.sim.harvest import NO_PICKING_RAIN_MM, HarvestModel, InventoryLedger
from coffwe.sim.pests import PestModel

MIN_PICKABLE_KG_PER_PLANT = 0.06  # less than this and a picking round isn't worth it

BLOSSOM_IRRIGATION_MM = 30.0
BACKING_IRRIGATION_MM = 25.0
BACKING_AFTER_DAYS = 18
BACKING_RAIN_MM = 10.0  # a natural shower this size makes backing irrigation unnecessary
IRRIGATION_HOURS = range(6, 12)  # 06:00-11:59

# IPM thresholds and doses
SCOUT_SAMPLE = 100  # berries / leaves inspected per block on a scouting round
CBB_THRESHOLD = 0.05  # sampled share of bored berries that triggers Beauveria
BEAUVERIA_KG_PER_ACRE = 0.4
BEAUVERIA_GAP_DAYS = 21
RUST_THRESHOLD = 0.08  # sampled share of rusted leaves...
RUST_RISK_THRESHOLD = 3.0  # ...AND this much infection weather over the last 14 days
BORDEAUX_KG_PER_ACRE = 2.0  # 1% Bordeaux: equal copper sulphate and lime
BORDEAUX_GAP_DAYS = 30
SPRAY_MAX_RAIN_MM = 5.0  # sprays wash off: don't spray on a wet day


@dataclass
class BaselineManagement:
    # Robusta is usually irrigated first (earlier crop, warmer blocks); Arabica later.
    robusta_deadline: tuple[int, int] = (2, 20)
    arabica_deadline: tuple[int, int] = (3, 10)
    log: list[dict] = field(default_factory=list)
    ipm_enabled: bool = True  # False = the "no pest management" counterfactual
    ipm_log: list[dict] = field(default_factory=list)
    _last_spray: dict[str, dict[str, date]] = field(default_factory=dict)
    _forced: dict[str, date] = field(default_factory=dict)  # block -> forced blossom date
    _rain_since: dict[str, float] = field(default_factory=dict)

    def irrigation_for(self, day: date, coffee: CoffeeModel) -> np.ndarray:
        """mm of irrigation per block (BLOCKS order) to apply today."""
        plan = np.zeros(len(BLOCKS))
        for i, b in enumerate(BLOCKS):
            reason = None
            due_rb = "RB" in b.plantings and day >= date(day.year, *self.robusta_deadline)
            due_ar = "AR" in b.plantings and day >= date(day.year, *self.arabica_deadline)
            if day.month <= 4 and (
                (due_rb and coffee.unblossomed(b.code, "RB"))
                or (due_ar and coffee.unblossomed(b.code, "AR"))
            ):
                if b.code not in self._forced or self._forced[b.code].year != day.year:
                    plan[i], reason = BLOSSOM_IRRIGATION_MM, "blossom"
                    self._forced[b.code] = day
                    self._rain_since[b.code] = 0.0
            elif (
                b.code in self._forced
                and self._forced[b.code].year == day.year
                and day == self._forced[b.code] + timedelta(days=BACKING_AFTER_DAYS)
                and self._rain_since.get(b.code, 0.0) < BACKING_RAIN_MM
            ):
                plan[i], reason = BACKING_IRRIGATION_MM, "backing"
            if reason:
                self.log.append(
                    {"date": day, "block_code": b.code, "mm": plan[i], "reason": reason}
                )
        return plan

    def observe_rain(self, rain_mm: np.ndarray) -> None:
        """End-of-day: remember natural rain since each forced blossom."""
        for i, b in enumerate(BLOCKS):
            if b.code in self._rain_since:
                self._rain_since[b.code] += float(rain_mm[i])

    @staticmethod
    def hourly(plan_mm: np.ndarray, hour: int) -> np.ndarray:
        if hour in IRRIGATION_HOURS:
            return plan_mm / len(IRRIGATION_HOURS)
        return np.zeros_like(plan_mm)

    # --- harvest -----------------------------------------------------------------

    def plan_picking(
        self, day: date, coffee: CoffeeModel, harvest: HarvestModel, rain_mm: float
    ) -> dict[str, list[str]]:
        """Send today's pickers where the ripe crop is.

        A unit is worth a round once it holds MIN_PICKABLE_KG_PER_PLANT of red or
        over-ripe cherry. Each such unit "needs" enough pickers to clear it today;
        if there aren't enough pickers, they're shared out in proportion to need
        (largest-remainder, so every picker is placed). Over-ripe cherry counts
        double -- it falls soon, so it's the most urgent.
        """
        if rain_mm > NO_PICKING_RAIN_MM:
            return {}
        pickers = harvest.available_pickers(day)
        if not pickers:
            return {}
        need: dict[str, int] = {}
        for u in coffee.units:
            plants = harvest.plants[u.code]
            if (u.ripe_kg + u.overripe_kg) / plants < MIN_PICKABLE_KG_PER_PLANT:
                continue
            urgent_kg = u.ripe_kg + 2 * u.overripe_kg
            need[u.code] = max(1, math.ceil(urgent_kg / harvest.expected_rate(u.code, coffee)))
        if not need:
            return {}
        if sum(need.values()) > len(pickers):
            need = {k: v for k, v in apportion(len(pickers), need).items() if v}
        order = iter(pickers)
        return {code: [next(order) for _ in range(n)] for code, n in need.items()}

    # --- inventory ---------------------------------------------------------------

    def reorder(self, day: date, ledger: InventoryLedger, from_day: date) -> None:
        """Top any item that fell below its reorder level back up to its opening stock."""
        if day < from_day:
            return
        for item in INVENTORY_ITEMS:
            if item.reorder_level is None:
                continue
            on_hand = ledger.on_hand[item.code]
            if on_hand < float(item.reorder_level):
                ledger.record(day, item.code, float(item.opening_balance) - on_hand, "purchase",
                              notes="reorder: below reorder level")  # fmt: skip

    # --- integrated pest management (monitor -> analyse -> intervene) --------------

    def ipm(
        self,
        day: date,
        coffee: CoffeeModel,
        pests: PestModel,
        ledger: InventoryLedger,
        ledger_from: date,
        rain_mm: float,
        rng: np.random.Generator,
    ) -> None:
        """Organic pest management, following the estate concept's IPM table.

        Scouting is *sampling*: the keeper inspects SCOUT_SAMPLE berries or leaves per
        block, so decisions rest on a noisy estimate, never on the true infestation.
        """
        if not self.ipm_enabled:
            pests.trapping = False
            return

        def use(item: str, qty: float, block: str | None, note: str) -> None:
            if day < ledger_from or qty <= 0:
                return
            # A planned job: buy the shortfall (plus the usual buffer) *before* starting.
            if ledger.on_hand[item] < qty:
                buffer = float(
                    next(i for i in INVENTORY_ITEMS if i.code == item).reorder_level or 0
                )
                ledger.record(day, item, qty - ledger.on_hand[item] + buffer, "purchase",
                              notes=f"bought ahead of: {note}")  # fmt: skip
            ledger.record(day, item, -qty, "used", block_code=block, notes=note)

        if day.day == 1:  # fresh pheromone lures in every trap, monthly
            use("CBB-LURE", sum(pests.traps.values()), None, "monthly lure replacement")

        sprayable = rain_mm < SPRAY_MAX_RAIN_MM
        is_scouting_day = day.weekday() == 5
        for b in BLOCKS:
            units = pests.block_units(b.code)
            acres = float(b.area_acres - b.buffer_acres)
            last = self._last_spray.setdefault(b.code, {})

            # Berry borer: Beauveria when sampled infestation crosses the threshold and
            # there are host berries for the beetles to be in.
            if is_scouting_day and sprayable:
                bored = max(rng.binomial(SCOUT_SAMPLE, p.infestation) for p in units) / SCOUT_SAMPLE
                hosts = sum(coffee.susceptible_kg(p.code) for p in units)
                due = day - last.get("beauveria", date.min) >= timedelta(days=BEAUVERIA_GAP_DAYS)
                if bored >= CBB_THRESHOLD and hosts > 0 and due:
                    pests.spray_beauveria(b.code, day)
                    last["beauveria"] = day
                    kg = BEAUVERIA_KG_PER_ACRE * acres
                    use("BEAUVERIA", kg, b.code, f"Beauveria spray, scouted {bored:.0%} bored")
                    self.ipm_log.append({"day": day, "block_code": b.code, "action": "beauveria",
                                         "scouted": bored})  # fmt: skip

            # Leaf rust: Bordeaux only above the risk threshold (not on a calendar).
            arabica = [p for p in units if p.variety == "AR"]
            if is_scouting_day and sprayable and arabica:
                p = arabica[0]
                rusted = rng.binomial(SCOUT_SAMPLE, p.rust_incidence) / SCOUT_SAMPLE
                due = day - last.get("bordeaux", date.min) >= timedelta(days=BORDEAUX_GAP_DAYS)
                if rusted >= RUST_THRESHOLD and p.rust_risk() >= RUST_RISK_THRESHOLD and due:
                    pests.spray_bordeaux(b.code)
                    last["bordeaux"] = day
                    for item in ("CU-SULPHATE", "LIME"):
                        use(item, BORDEAUX_KG_PER_ACRE * acres, b.code,
                            f"1% Bordeaux, scouted {rusted:.0%} rusted")  # fmt: skip
                    self.ipm_log.append({"day": day, "block_code": b.code, "action": "bordeaux",
                                         "scouted": rusted})  # fmt: skip

            # Field hygiene: once a block's harvest is over, collect the fallen berries.
            season = day.year if day.month <= 6 else day.year + 1
            done = all(
                coffee.unit(p.code).ripe_kg + coffee.unit(p.code).overripe_kg < 1.0
                and coffee.unit(p.code).green_kg() < 1.0
                for p in units
            )
            if 2 <= day.month <= 5 and done and units[0].gleaned_season != season:
                pests.glean(b.code, season)
                self.ipm_log.append({"day": day, "block_code": b.code, "action": "gleaning",
                                     "scouted": None})  # fmt: skip
