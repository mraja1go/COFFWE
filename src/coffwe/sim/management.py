"""Baseline estate management: the decisions a typical Western Ghats manager makes.

The simulator is the "real estate", but a real estate is also *managed*. Until the AI
brains take over decisions (Phase 5+, acting through the Phase 8 actuators), this
policy plays the estate manager, using standard Karnataka practice:

* Blossom irrigation -- if a block's coffee hasn't blossomed by a set date (rain
  failed), run the sprinklers BLOSSOM_IRRIGATION_MM to force the blossom.
* Backing irrigation -- if a blossom was forced and no meaningful rain follows within
  BACKING_AFTER_DAYS, irrigate once more so the flowers set fruit.

Decisions are taken at the start of each day and applied by the soil model as
irrigation over the morning hours (when the sprinkler crew works).
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

from coffwe.estate_data import BLOCKS
from coffwe.sim.coffee import CoffeeModel

BLOSSOM_IRRIGATION_MM = 30.0
BACKING_IRRIGATION_MM = 25.0
BACKING_AFTER_DAYS = 18
BACKING_RAIN_MM = 10.0  # a natural shower this size makes backing irrigation unnecessary
IRRIGATION_HOURS = range(6, 12)  # 06:00-11:59


@dataclass
class BaselineManagement:
    # Robusta is usually irrigated first (earlier crop, warmer blocks); Arabica later.
    robusta_deadline: tuple[int, int] = (2, 20)
    arabica_deadline: tuple[int, int] = (3, 10)
    log: list[dict] = field(default_factory=list)
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
