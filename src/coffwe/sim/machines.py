"""Machine wear, failures and repairs for every estate asset.

Each asset wears in its own unit of use -- pulper hours, curtain cycles, pickup km,
valve operations, days in service. Failures follow a Weibull distribution in that
usage since the last service:

    P(survive to usage u) = exp(-(u / eta)^k)

eta (the "characteristic life") is the usage by which ~63% of machines have failed;
k is the shape: k < 1 means infant mortality, k = 1 random failures, k > 1 wear-out --
the more a machine has been used since its last service, the likelier it fails
*today*. Each day's failure chance is the conditional probability of failing during
today's usage, given it survived to yesterday's:

    P(fail today) = 1 - exp((u0/eta)^k - (u1/eta)^k)

A failed machine is out of service for a repair time, may use a spare part, and
comes back with its wear reset. Planned maintenance resets wear without a failure.
The failure history (with usage at the time) is the training data for Phase 6's
predictive-maintenance model.
"""

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

from coffwe.estate_data import ASSETS, BLOCKS


@dataclass(frozen=True)
class WearModel:
    unit: str  # "h", "cycles", "km", "days"
    eta: float  # characteristic life, in `unit`, between services
    k: float  # Weibull shape
    repair_days: float  # mean time to repair
    spare: tuple[str, float] | None = None  # (inventory item, qty) used on a repair


# Wear models by asset code (or code prefix). Calibrated (2026-11-25) so each machine's
# *yearly* use gives a realistic chance of a breakdown -- e.g. the pulper runs ~70 h a
# season and breaks down about once; structures barely wear at all.
WEAR: dict[str, WearModel] = {
    "PULPER-01": WearModel("h", 70, 1.8, 2, ("PULPER-SCREEN", 1)),
    "RECV-SCALE": WearModel("cycles", 6000, 1.5, 2),
    "POLY-01": WearModel("days", 900, 3.0, 5),  # UV-degraded polyhouse film tears
    "POLY-01-CURTAIN": WearModel("cycles", 80, 1.8, 2),
    "BIOGAS-01": WearModel("days", 1400, 1.5, 4),
    "GENSET-01": WearModel("h", 300, 2.0, 3),  # an old (2014) generator
    "PUMP-01": WearModel("h", 400, 2.0, 3),
    "VALVE-": WearModel("cycles", 15, 1.3, 1, ("DRIP-EMITTER", 10)),
    "PICKUP-01": WearModel("km", 12000, 1.8, 3),
    "TILLER-01": WearModel("h", 600, 2.0, 2),
    "BRUSH-": WearModel("h", 300, 2.0, 2),
    "SPRAY-": WearModel("cycles", 30, 1.5, 1),
    "CHAINSAW-01": WearModel("h", 200, 2.0, 2),
}
STRUCTURE = WearModel("days", 20000, 1.2, 3)  # tanks, beds, pits, solar, lab kit

POWER_CUT_PER_DAY = 0.12  # rural grid: a cut on ~1 day in 8
POWER_CUT_HOURS = (2, 7)
PICKUP_DAILY_KM, PICKUP_TRIP_KM = 20.0, 60.0
TILLER_HOURS_PER_PICKING_DAY = 4.0
PREVENTIVE_SERVICE_DAY = (10, 15)  # before the harvest season
PREVENTIVE_ASSETS = (
    "PULPER-01",
    "GENSET-01",
    "PICKUP-01",
    "TILLER-01",
    "PUMP-01",
    "POLY-01-CURTAIN",
)


def wear_model(code: str) -> WearModel:
    if code in WEAR:
        return WEAR[code]
    for prefix, model in WEAR.items():
        if prefix.endswith("-") and code.startswith(prefix):
            return model
    return STRUCTURE


def failure_probability(model: WearModel, used_before: float, used_today: float) -> float:
    """Conditional Weibull: chance of failing during today's use, having survived so far."""
    if used_today <= 0:
        return 0.0
    h0 = (used_before / model.eta) ** model.k
    h1 = ((used_before + used_today) / model.eta) ** model.k
    return 1 - math.exp(h0 - h1)


@dataclass
class Machine:
    code: str
    model: WearModel
    usage: float  # since last service
    status: str = "operational"
    back_on: date | None = None
    total_usage: float = 0.0


@dataclass
class MachineModel:
    rng: np.random.Generator
    machines: dict[str, Machine] = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)
    daily: list[dict] = field(default_factory=list)
    spares_used: list[tuple[date, str, float, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        for a in ASSETS:
            m = wear_model(a.code)
            # Unknown wear history at spin-up: somewhere in the first half of its life.
            self.machines[a.code] = Machine(
                a.code, m, usage=float(self.rng.uniform(0, 0.5)) * m.eta
            )

    # --- what works today ------------------------------------------------------------

    def ok(self, code: str) -> bool:
        return self.machines[code].status == "operational"

    def capabilities(self, day: date) -> dict:
        """What the estate can do today, given which machines are working."""
        self._return_repaired(day)
        power_cut = self.rng.random() < POWER_CUT_PER_DAY
        cut_hours = int(self.rng.integers(*POWER_CUT_HOURS)) if power_cut else 0
        genset = self.ok("GENSET-01")
        return {
            # The pulper needs power: grid, or the generator during a cut.
            "pulping": self.ok("PULPER-01") and (not power_cut or genset),
            "rain_cover": self.ok("POLY-01") and self.ok("POLY-01-CURTAIN"),
            "transport": self.ok("PICKUP-01"),
            "irrigation": {
                b.code: self.ok("PUMP-01") and self.ok(f"VALVE-{b.code}") for b in BLOCKS
            },
            "power_cut_hours": cut_hours if genset else 0,
        }

    def _return_repaired(self, day: date) -> None:
        for m in self.machines.values():
            if m.status != "operational" and m.back_on and day >= m.back_on:
                kind = "repair" if m.status == "faulty" else None
                m.status, m.back_on, m.usage = "operational", None, 0.0
                if kind:
                    self._event(day, m, "repair", "back in service")

    # --- wear from today's work ------------------------------------------------------

    def on_day(self, day: date, usage: dict[str, float]) -> None:
        for code, used in usage.items():
            m = self.machines[code]
            if m.status != "operational" or used <= 0:
                continue
            p = failure_probability(m.model, m.usage, used)
            m.usage += used
            m.total_usage += used
            if self.rng.random() < p:
                self._fail(day, m)
        for m in self.machines.values():  # structures age by the day
            if m.model.unit == "days" and m.code not in usage and m.status == "operational":
                if self.rng.random() < failure_probability(m.model, m.usage, 1.0):
                    self._fail(day, m)
                m.usage += 1.0
        if (day.month, day.day) == PREVENTIVE_SERVICE_DAY:
            for code in PREVENTIVE_ASSETS:
                m = self.machines[code]
                if m.status == "operational":
                    self._event(day, m, "maintenance", "pre-season service")
                    m.status, m.back_on = "maintenance", day + timedelta(days=1)
        for m in self.machines.values():
            self.daily.append(
                {
                    "day": day,
                    "asset_code": m.code,
                    "status": m.status,
                    "usage_since_service": round(m.usage, 2),
                }
            )

    def _fail(self, day: date, m: Machine) -> None:
        self._event(day, m, "failure", "breakdown")
        repair = max(1, round(m.model.repair_days * float(self.rng.lognormal(0, 0.4))))
        m.status, m.back_on = "faulty", day + timedelta(days=repair)
        if m.model.spare and self.rng.random() < 0.6:  # not every fault needs the spare
            item, qty = m.model.spare
            self.spares_used.append((day, item, qty, m.code))

    def _event(self, day: date, m: Machine, kind: str, notes: str) -> None:
        self.events.append(
            {
                "day": day,
                "asset_code": m.code,
                "event_type": kind,
                "usage_since_service": round(m.usage, 2),
                "usage_unit": m.model.unit,
                "notes": notes,
            }
        )
