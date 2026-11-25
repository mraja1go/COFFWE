"""The estate's organic loop and daily feed, as inventory flows.

    cow dung --+--> biogas digester --> biogas (cooking) + slurry --> fertigation
               +--> vermicompost pits <-- coffee pulp (wet processing)
                                       --> vermicompost, ~60 days later --> blocks

Feed: concentrate for the cattle (by breed and stage) and for the hens, drawn daily
and written up weekly. All amounts are recorded in the shared inventory ledger, so
reorders happen through the usual stores policy.
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import date, timedelta

from coffwe.sim.harvest import InventoryLedger
from coffwe.sim.livestock import Animal

DUNG_SHARE_OF_BODYWEIGHT = 0.05  # fresh dung per day
DUNG_TO_DIGESTER = 0.6  # the rest goes to the vermicompost pits
SLURRY_L_PER_KG_DUNG = 1.6  # dung mixed ~1:1 with water, minus the gas
PULP_SHARE_OF_CHERRY = 0.40  # wet processing: pulp + skin, by fresh-cherry weight
VERMI_DAYS = 60
VERMI_YIELD = 0.35  # kg vermicompost per kg feedstock
HENS = 60
HEN_FEED_KG = 0.11
SLURRY_KEEP_L = 500.0  # keep this much slurry in the tank
SLURRY_BLOCKS = ("B6", "B7")  # beside the estate core: fertigation by pipe
VERMI_KG_PER_PLANT = 0.4  # post-harvest application


def cattle_feed_kg(a: Animal, day: date) -> float:
    """Daily concentrate. Milking crossbreds eat most; calves on milk eat little."""
    age = a.age_days(day)
    if age < 180:
        return 0.2
    if a.sex == "male":
        return 1.5 if age > 700 else 0.8
    if a.lactating:
        return 3.0 if a.breed == "jersey_cross" else 1.0
    return 0.8 if age > 700 else 0.5


@dataclass
class OrganicsModel:
    ledger: InventoryLedger
    ledger_from: date
    _pits: deque = field(default_factory=deque)  # (ready_on, feedstock_kg)
    _week: dict[str, float] = field(default_factory=dict)
    log: list[dict] = field(default_factory=list)

    def _add(self, key: str, qty: float) -> None:
        self._week[key] = self._week.get(key, 0.0) + qty

    def on_day(self, day: date, herd: list[Animal] | None, pulped_kg: float) -> None:
        dung = 0.0
        if herd:
            dung = sum(a.weight_kg * DUNG_SHARE_OF_BODYWEIGHT for a in herd)
            self._add("CATTLE-FEED", sum(cattle_feed_kg(a, day) for a in herd))
            self._add("CHICKEN-FEED", HENS * HEN_FEED_KG)
        self._add("BIOSLURRY", dung * DUNG_TO_DIGESTER * SLURRY_L_PER_KG_DUNG)
        feedstock = dung * (1 - DUNG_TO_DIGESTER) + pulped_kg * PULP_SHARE_OF_CHERRY
        if feedstock > 0:
            self._pits.append((day + timedelta(days=VERMI_DAYS), feedstock))
        while self._pits and self._pits[0][0] <= day:
            self._add("VERMICOMPOST", self._pits.popleft()[1] * VERMI_YIELD)

        if day.weekday() == 5:  # the stores clerk writes up the week on Saturday
            self._write_week(day)

    def _write_week(self, day: date) -> None:
        if day >= self.ledger_from:
            for item, reason in (
                ("CATTLE-FEED", "used"),
                ("CHICKEN-FEED", "used"),
                ("BIOSLURRY", "produced"),
                ("VERMICOMPOST", "produced"),
            ):
                qty = self._week.get(item, 0.0)
                if qty > 0:
                    sign = -1 if reason == "used" else 1
                    if reason == "used" and self.ledger.on_hand[item] < qty:
                        self.ledger.record(
                            day,
                            item,
                            qty - self.ledger.on_hand[item],
                            "purchase",
                            notes="bought ahead of the week's feeding",
                        )
                    self.ledger.record(day, item, sign * qty, reason, notes="weekly total")
            # Slurry above the tank's working level is piped to the blocks by the core.
            spare = self.ledger.on_hand["BIOSLURRY"] - SLURRY_KEEP_L
            if spare > 0:
                per_block = spare / len(SLURRY_BLOCKS)
                for block in SLURRY_BLOCKS:
                    self.ledger.record(
                        day,
                        "BIOSLURRY",
                        -per_block,
                        "used",
                        block_code=block,
                        notes="slurry fertigation",
                    )
        self._week = {}

    def apply_vermicompost(self, day: date, block_code: str, plants: int) -> float:
        """Post-harvest feeding of a block, as far as the stock allows. Returns kg applied."""
        if day < self.ledger_from:
            return 0.0
        kg = min(VERMI_KG_PER_PLANT * plants, self.ledger.on_hand["VERMICOMPOST"])
        if kg > 0:
            self.ledger.record(
                day,
                "VERMICOMPOST",
                -kg,
                "used",
                block_code=block_code,
                notes="post-harvest application",
            )
            self.log.append({"day": day, "block_code": block_code, "vermicompost_kg": kg})
        return kg
