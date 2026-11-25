"""Harvest and processing: pickers turn ripe cherry into traceable batches of coffee.

Daily, in order:
1. Pickers work the blocks the manager assigned them. Each picker's day depends on
   the variety (Robusta clusters pick faster), slope, how many ripe cherries hang per
   plant (thin pickings are slow) and their own skill.
2. Each (block, variety) picked today becomes one harvest batch, weighed at cherry
   receiving -- a little lighter than the pickers' field weights (sorting, spillage).
3. Batches move through processing:
     washed  (Arabica): received -> processing (pulper + fermentation tanks)
                        -> drying as parchment -> stored
     natural (Robusta): received -> drying as whole cherry -> stored
   Drying needs a number of good drying days; rain stalls it (the polyhouse dryer
   keeps some progress going). Stored coffee is bagged in 60 kg jute bags and
   leaves for the curing works on the 1st and 15th of each month.
4. Consumables are recorded in an inventory ledger: diesel for pulping and lorry
   trips, jute bags when coffee is stored.
"""

import math
from dataclasses import dataclass, field
from datetime import date

import numpy as np

from coffwe.estate_data import (
    BLOCKS,
    INVENTORY_ITEMS,
    SEASON_END,
    SEASON_START,
    WorkerSpec,
    worker_specs,
)
from coffwe.sim.coffee import CoffeeModel

PICK_RATE_KG_PER_DAY = {"AR": 45.0, "RB": 60.0}  # selective picking, good crop
STEEP_PICK_FACTOR = 0.8
FULL_RATE_RIPE_KG_PER_PLANT = 0.25  # below this much ripe cherry per plant, picking slows
ATTENDANCE = 0.9
NO_PICKING_RAIN_MM = 20.0
RECEIVING_LOSS = (0.005, 0.02)  # field weight -> receiving weight
PROCESSING_METHOD = {"AR": "washed", "RB": "natural"}
OUTTURN = {"washed": 0.20, "natural": 0.42}  # dried kg per fresh cherry kg
FERMENTATION_DAYS = 2  # washed coffee: pulping + fermentation before drying starts
DRYING_DAYS_NEEDED = {"washed": 8.0, "natural": 14.0}  # in "good drying day" credits
GOOD_DRYING_SOLAR_WM2 = 150.0  # daily mean
DRY_DAY_CREDIT, CLOUDY_DAY_CREDIT, RAIN_DAY_CREDIT = 1.0, 0.5, 0.3  # rain: polyhouse only
BAG_KG = 60.0
DISPATCH_DAYS_OF_MONTH = (1, 15)
DIESEL_L_PER_100KG_PULPED = 0.6
DIESEL_L_PER_LORRY_TRIP = 25.0


@dataclass
class Batch:
    code: str
    block_code: str
    variety: str
    harvested_on: date
    field_kg: float  # sum of picker weights
    cherry_kg: float  # official receiving weight
    overripe_kg: float
    method: str
    picks: list[tuple[str, float]]  # (worker code, field kg)
    status: str = "received"
    drying_credit: float = 0.0
    drying_started_on: date | None = None
    stored_on: date | None = None
    dispatched_on: date | None = None
    output_kg: float | None = None


@dataclass
class Movement:
    day: date
    item_code: str
    quantity: float
    reason: str
    block_code: str | None = None
    notes: str | None = None


@dataclass
class InventoryLedger:
    """Append-only stock ledger, mirroring the Postgres inventory_movements table."""

    on_hand: dict[str, float] = field(
        default_factory=lambda: {i.code: float(i.opening_balance) for i in INVENTORY_ITEMS}
    )
    movements: list[Movement] = field(default_factory=list)

    def record(self, day: date, item: str, quantity: float, reason: str, **kw) -> None:
        if quantity == 0:
            return
        self.on_hand[item] += quantity
        self.movements.append(Movement(day, item, round(quantity, 3), reason, **kw))


class HarvestModel:
    def __init__(self, rng: np.random.Generator, ledger_from: date) -> None:
        self.rng = rng
        self.ledger_from = ledger_from  # consumption is tracked from this day on
        self.ledger = InventoryLedger()
        self.workers: list[WorkerSpec] = worker_specs()
        # A fixed personal skill per worker: some pickers are simply faster.
        self.skill = {w.code: float(rng.lognormal(0, 0.15)) for w in self.workers}
        self.batches: list[Batch] = []
        self.plants = {
            f"{b.code}-{v}": n for b in BLOCKS for v, n in b.plantings.items() if v in ("AR", "RB")
        }
        self.steep = {b.code: b.slope == "steep" for b in BLOCKS}

    # --- workforce -------------------------------------------------------------

    def available_pickers(self, day: date) -> list[str]:
        """Workers who turn up to pick today (before attendance is applied: all)."""
        if day.weekday() == 6:  # Sunday off
            return []
        if SEASON_START <= day <= SEASON_END:
            pool = [w for w in self.workers if w.role == "picker"]
        elif SEASON_END < day and day.month <= 4:
            pool = [w for w in self.workers if w.role == "field_worker"]  # late tail
        else:
            pool = []
        return [w.code for w in pool if self.rng.random() < ATTENDANCE]

    def expected_rate(self, unit_code: str, coffee: CoffeeModel) -> float:
        """Rough kg one average picker would get from this unit today (for planning)."""
        u = coffee.unit(unit_code)
        per_plant = (u.ripe_kg + u.overripe_kg) / self.plants[unit_code]
        density = min(per_plant / FULL_RATE_RIPE_KG_PER_PLANT, 1.0)
        steep = STEEP_PICK_FACTOR if self.steep[u.block.code] else 1.0
        return max(PICK_RATE_KG_PER_DAY[u.variety] * steep * density, 1.0)

    # --- picking ---------------------------------------------------------------

    def pick_day(
        self, day: date, assignments: dict[str, list[str]], coffee: CoffeeModel
    ) -> list[dict]:
        rows = []
        for unit_code, crew in assignments.items():
            if not crew:
                continue
            rate = self.expected_rate(unit_code, coffee)
            capacity = {w: rate * self.skill[w] * float(self.rng.lognormal(0, 0.1)) for w in crew}
            ripe, overripe = coffee.pick(unit_code, sum(capacity.values()))
            field_total = ripe + overripe
            if field_total < 0.5:
                continue
            # Everyone's weight shrinks in proportion if the branches ran out first.
            scale = field_total / sum(capacity.values())
            picks = [(w, round(c * scale, 2)) for w, c in capacity.items() if c * scale >= 0.01]
            field_kg = sum(kg for _, kg in picks)
            u = coffee.unit(unit_code)
            loss = self.rng.uniform(*RECEIVING_LOSS)
            batch = Batch(
                code=f"HB-{day:%Y%m%d}-{u.block.code}-{u.variety}",
                block_code=u.block.code,
                variety=u.variety,
                harvested_on=day,
                field_kg=field_kg,
                cherry_kg=round(field_kg * (1 - loss), 2),
                overripe_kg=round(overripe, 2),
                method=PROCESSING_METHOD[u.variety],
                picks=picks,
            )
            self.batches.append(batch)
            rows.append(
                {
                    "block_code": u.block.code,
                    "variety": u.variety,
                    "pickers": len(picks),
                    "picked_kg": field_kg,
                    "overripe_kg": overripe,
                }
            )
        return rows

    # --- processing ------------------------------------------------------------

    def process_day(self, day: date, rain_mm: float, solar_mean_wm2: float) -> None:
        if rain_mm >= 2.0:
            credit = RAIN_DAY_CREDIT
        elif solar_mean_wm2 >= GOOD_DRYING_SOLAR_WM2:
            credit = DRY_DAY_CREDIT
        else:
            credit = CLOUDY_DAY_CREDIT

        for b in self.batches:
            age = (day - b.harvested_on).days
            if b.status == "received" and b.method == "washed" and age == 0:
                b.status = "processing"
            elif b.status in ("received", "processing"):
                ready = age >= (FERMENTATION_DAYS if b.method == "washed" else 1)
                if ready:
                    b.status, b.drying_started_on = "drying", day
            if b.status == "drying":
                b.drying_credit += credit
                if b.drying_credit >= DRYING_DAYS_NEEDED[b.method]:
                    b.status, b.stored_on = "stored", day
                    b.output_kg = round(b.cherry_kg * OUTTURN[b.method], 2)

        # Dried coffee sits in bulk in the store; it's bagged when the lorry comes, one
        # lot per processing method (parchment and dry cherry are never mixed).
        if day.day in DISPATCH_DAYS_OF_MONTH:
            ready = [b for b in self.batches if b.status == "stored"]
            for method in ("washed", "natural"):
                lot = [b for b in ready if b.method == method]
                if not lot:
                    continue
                kg = sum(b.output_kg or 0 for b in lot)
                self._use(day, "JUTE-BAG", math.ceil(kg / BAG_KG),
                          notes=f"bagging {kg:.0f} kg {method} for dispatch")  # fmt: skip
            for b in ready:
                b.status, b.dispatched_on = "dispatched", day
            if ready:
                self._use(day, "DIESEL", DIESEL_L_PER_LORRY_TRIP,
                          notes=f"lorry to curing works ({len(ready)} batches)")  # fmt: skip

        # The pulper's diesel is written up once a day, not per batch.
        pulped = sum(
            b.cherry_kg for b in self.batches if b.method == "washed" and b.harvested_on == day
        )
        if pulped:
            self._use(day, "DIESEL", pulped / 100 * DIESEL_L_PER_100KG_PULPED,
                      notes=f"pulper, {pulped:.0f} kg cherry")  # fmt: skip

    def _use(self, day: date, item: str, quantity: float, **kw) -> None:
        if day >= self.ledger_from:
            self.ledger.record(day, item, -quantity, "used", **kw)
