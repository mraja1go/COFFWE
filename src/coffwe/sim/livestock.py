"""Livestock: the cattle herd and the corridor apiary, stepped daily from EVENTS_START.

Unlike the coffee physics, livestock doesn't spin up from New Year: the seeded Phase 1
data *is* the herd's recorded history up to 24 Nov 2026 (calvings, pregnancies,
vaccinations, colonies, queens, honey harvests). The models read their starting
state from that history and take over the day after.

Cattle
  * Milk follows Wood's lactation curve y(t) = a * t^b * exp(-c t): yield climbs to a
    peak around day b/c after calving, then declines. Milked twice a day.
  * 21-day heat cycles; the keeper notices most heats; Bhima serves them; pregnancy
    check at 60 days; calving after ~283 days creates a new calf; cows are dried off
    ~60 days before calving (or once yield has fallen away).
  * Health: vaccinations and deworming when due, monthly weigh-ins (calves grow),
    mastitis (with milk withdrawal) and occasional other illness.

Bees
  * Colony strength in frames of bees (0-8) grows during nectar flows and shrinks in a
    dearth; honey stores fill when bees can forage (no rain) and are eaten daily.
  * The coffee-blossom flow is boosted on the days the simulated coffee is in bloom.
  * Colonies can abscond (likelier when weak) or collapse (sacbrood season); old
    queens are superseded. The keeper harvests honey and re-stocks empty boxes.
"""

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

from coffwe.estate_data import (
    BEEKEEPER_CODE,
    CATTLE,
    NECTAR_FLOWS,
    cattle_breeding_specs,
    cattle_health_specs,
    colony_specs,
    hive_specs,
    queen_specs,
)

KEEPER = "Livestock keeper (W008)"
VET = "Visiting veterinary officer"


# --- cattle parameters -------------------------------------------------------------


@dataclass(frozen=True)
class BreedParams:
    wood_a: float
    wood_b: float
    wood_c: float
    lactation_days: int  # dry off after this long, even if not pregnant
    dry_below_litres: float  # ...or once daily yield falls this low
    fat_pct: float
    conception_rate: float  # chance a service results in pregnancy
    mastitis_per_day: float
    birth_weight_kg: float
    calf_gain_kg_per_day: float


BREEDS = {
    # Peak ~3 L/day at day 50: a = 3 / (50^0.2 * e^-0.2)
    "malnad_gidda": BreedParams(1.675, 0.20, 0.004, 270, 0.6, 5.2, 0.55, 0.0004, 14, 0.25),
    # Peak ~11 L/day at day ~57
    "jersey_cross": BreedParams(5.99, 0.20, 0.0035, 305, 2.0, 4.4, 0.45, 0.0015, 22, 0.45),
}
MORNING_SHARE = 0.55
GESTATION_DAYS, GESTATION_SD = 283, 4
OESTRUS_CYCLE_DAYS = 21
HEAT_DETECTION = 0.8
POSTPARTUM_WAIT_DAYS = 60  # voluntary waiting period before re-breeding
HEIFER_BREEDING_AGE_DAYS = 450
PREGNANCY_CHECK_AFTER_DAYS = 60
DRY_OFF_BEFORE_CALVING_DAYS = 60
ABORTION_RISK = 0.02
MILK_WITHDRAWAL_DAYS = 5
MASTITIS_YIELD_FACTOR = 0.7
OTHER_ILLNESS_PER_DAY = 0.0002
VACCINE_INTERVALS = {
    "Foot-and-mouth disease (FMD)": 182,
    "Haemorrhagic septicaemia (HS)": 365,
    "Lumpy skin disease (LSD)": 365,
}
DEWORMING_INTERVAL = 92
MIN_VACCINATION_AGE_DAYS = 120
BRUCELLOSIS_AGE_DAYS = 130  # female calves, once
SIRE_CODE = "C01"
CALF_NAMES = {
    "female": ["Kapila", "Nethra", "Varada", "Malati", "Sharavathi", "Bhadra", "Tara", "Gowri II"],
    "male": ["Nandi", "Veera", "Ranga", "Arjuna", "Kalinga", "Sooraj", "Basava II", "Hanuma"],
}


@dataclass
class Animal:
    code: str
    name: str
    sex: str
    breed: str
    born_on: date
    dam_code: str | None
    sire_code: str | None
    weight_kg: float
    last_calved_on: date | None = None
    lactating: bool = False
    pregnant: bool = False
    conceived_on: date | None = None
    due_on: date | None = None
    served_on: date | None = None  # awaiting pregnancy check
    next_heat_on: date | None = None
    withdrawal_until: date | None = None
    vaccines_due: dict[str, date] = field(default_factory=dict)
    deworm_due: date | None = None
    brucellosis_done: bool = False
    is_new: bool = False  # born during the simulation (needs a new DB row)

    def age_days(self, day: date) -> int:
        return (day - self.born_on).days

    @property
    def params(self) -> BreedParams:
        return BREEDS[self.breed]

    def days_in_milk(self, day: date) -> int:
        return (day - self.last_calved_on).days if self.last_calved_on else 0

    def wood_yield(self, day: date) -> float:
        t = max(self.days_in_milk(day), 1)
        p = self.params
        return p.wood_a * t**p.wood_b * math.exp(-p.wood_c * t)


class CattleModel:
    def __init__(self, rng: np.random.Generator, start: date) -> None:
        self.rng = rng
        self.herd: dict[str, Animal] = {}
        self.milkings: list[dict] = []  # {day, cattle_code, session, litres, fat_pct}
        self.health: list[dict] = []
        self.breeding: list[dict] = []
        self._load_history(start)

    # --- starting state from the recorded history --------------------------------

    def _load_history(self, start: date) -> None:
        health = cattle_health_specs()
        breeding = cattle_breeding_specs()
        for c in CATTLE:
            weights = [h for h in health if h.cattle_code == c.code and h.weight_kg]
            a = Animal(
                code=c.code,
                name=c.name,
                sex=c.sex,
                breed=c.breed,
                born_on=c.born_on,
                dam_code=c.dam_code,
                sire_code=c.sire_code,
                weight_kg=float(max(weights, key=lambda h: h.occurred_on).weight_kg),
            )
            mine = sorted(
                (e for e in breeding if e.cow_code == c.code), key=lambda e: e.occurred_on
            )
            calvings = [e for e in mine if e.event_type == "calving"]
            if calvings:
                a.last_calved_on = calvings[-1].occurred_on
                a.lactating = not any(
                    e.event_type == "dry_off" and e.occurred_on > a.last_calved_on for e in mine
                )
            matings = [e for e in mine if e.event_type == "mating"]
            if matings and (not calvings or matings[-1].occurred_on > a.last_calved_on):
                checks = [
                    e
                    for e in mine
                    if e.event_type == "pregnancy_check" and e.occurred_on > matings[-1].occurred_on
                ]
                if checks and checks[-1].pregnant:
                    a.pregnant, a.conceived_on = True, matings[-1].occurred_on
                    a.due_on = a.conceived_on + timedelta(days=GESTATION_DAYS)
            heats = [e.occurred_on for e in mine if e.event_type == "heat"]
            if a.sex == "female" and not a.pregnant:
                last = (
                    max(heats) if heats else start - timedelta(days=int(self.rng.integers(1, 21)))
                )
                nxt = last + timedelta(days=OESTRUS_CYCLE_DAYS)
                while nxt < start:
                    nxt += timedelta(days=OESTRUS_CYCLE_DAYS)
                a.next_heat_on = nxt
            for h in health:
                if h.cattle_code != c.code:
                    continue
                if h.event_type == "vaccination" and h.next_due_on:
                    a.vaccines_due[h.subject] = h.next_due_on
                if h.event_type == "vaccination" and h.subject.startswith("Brucellosis"):
                    a.brucellosis_done = True
                if h.event_type == "deworming":
                    a.deworm_due = h.next_due_on
            self.herd[c.code] = a

    # --- daily step ----------------------------------------------------------------

    def on_day(self, day: date) -> None:
        for a in list(self.herd.values()):
            if a.sex == "female":
                self._reproduction(a, day)
                self._milk(a, day)
            self._health(a, day)
        if day.day == 1:
            self._monthly_checkup(day)

    def _breedable(self, a: Animal, day: date) -> bool:
        # An open cow that was served but didn't conceive comes back into heat ~21 days
        # later and is simply served again -- only a pregnancy stops the cycle.
        if a.pregnant:
            return False
        if a.last_calved_on:
            return a.days_in_milk(day) >= POSTPARTUM_WAIT_DAYS
        return a.age_days(day) >= HEIFER_BREEDING_AGE_DAYS

    def _reproduction(self, a: Animal, day: date) -> None:
        p = a.params
        # Pregnancy check on a served cow.
        if a.served_on and (day - a.served_on).days == PREGNANCY_CHECK_AFTER_DAYS:
            self._breeding_event(a, day, "pregnancy_check", pregnant=a.pregnant)
            a.served_on = None
            if not a.pregnant:
                a.next_heat_on = day + timedelta(days=int(self.rng.integers(1, 21)))
        # Heat: noticed -> served by the bull (if she's eligible).
        if a.next_heat_on == day:
            a.next_heat_on = day + timedelta(
                days=OESTRUS_CYCLE_DAYS + int(self.rng.integers(-2, 3))
            )
            if self._breedable(a, day) and self.rng.random() < HEAT_DETECTION:
                self._breeding_event(a, day, "heat")
                self._breeding_event(a, day, "mating", sire=SIRE_CODE)
                a.served_on, a.next_heat_on = day, None
                if self.rng.random() < p.conception_rate:
                    a.pregnant, a.conceived_on = True, day
                    a.due_on = day + timedelta(
                        days=int(self.rng.normal(GESTATION_DAYS, GESTATION_SD))
                    )
                else:  # she'll cycle back in ~21 days (seen at the check if not before)
                    a.next_heat_on = day + timedelta(days=OESTRUS_CYCLE_DAYS)
        # Late pregnancy: dry off, rare abortion, calving.
        if a.pregnant and a.due_on:
            if a.lactating and (a.due_on - day).days <= DRY_OFF_BEFORE_CALVING_DAYS:
                a.lactating = False
                self._breeding_event(a, day, "dry_off", notes="Ahead of calving")
            if day == a.conceived_on + timedelta(days=120) and self.rng.random() < ABORTION_RISK:
                a.pregnant, a.due_on = False, None
                self._breeding_event(a, day, "abortion")
                a.next_heat_on = day + timedelta(days=30)
            elif day >= a.due_on:
                self._calve(a, day)

    def _calve(self, dam: Animal, day: date) -> None:
        sex = "female" if self.rng.random() < 0.5 else "male"
        used = {a.name for a in self.herd.values()}
        name = next(n for n in CALF_NAMES[sex] if n not in used)
        code = f"C{len(self.herd) + 1:02d}"
        p = dam.params
        calf = Animal(
            code=code,
            name=name,
            sex=sex,
            breed=dam.breed,
            born_on=day,
            dam_code=dam.code,
            sire_code=SIRE_CODE,
            weight_kg=round(p.birth_weight_kg * float(self.rng.normal(1, 0.08)), 1),
            is_new=True,
        )
        if sex == "female":
            calf.next_heat_on = day + timedelta(days=HEIFER_BREEDING_AGE_DAYS)
        self.herd[code] = calf
        dam.pregnant, dam.conceived_on, dam.due_on = False, None, None
        dam.last_calved_on, dam.lactating = day, True
        dam.next_heat_on = day + timedelta(days=OESTRUS_CYCLE_DAYS * 2)
        self._breeding_event(dam, day, "calving", calf=code)
        self._health_event(
            calf,
            day,
            "checkup",
            "Birth check",
            weight_kg=calf.weight_kg,
            temperature_c=round(float(self.rng.normal(38.9, 0.2)), 1),
        )

    def _milk(self, a: Animal, day: date) -> None:
        if not a.lactating:
            return
        p = a.params
        litres = a.wood_yield(day)
        if a.withdrawal_until and day <= a.withdrawal_until + timedelta(days=2):
            litres *= MASTITIS_YIELD_FACTOR
        if a.days_in_milk(day) > p.lactation_days or litres < p.dry_below_litres:
            a.lactating = False
            self._breeding_event(a, day, "dry_off", notes="End of lactation")
            return
        dim = a.days_in_milk(day)
        fat = p.fat_pct + 0.002 * dim  # fat rises as yield falls through lactation
        for session, share in (("morning", MORNING_SHARE), ("evening", 1 - MORNING_SHARE)):
            amount = litres * share * float(self.rng.lognormal(0, 0.08))
            self.milkings.append(
                {
                    "day": day,
                    "cattle_code": a.code,
                    "session": session,
                    "litres": round(max(amount, 0.05), 2),
                    "fat_pct": round(fat + float(self.rng.normal(0, 0.15)), 2),
                }
            )

    def _health(self, a: Animal, day: date) -> None:
        age = a.age_days(day)
        for vaccine, due in list(a.vaccines_due.items()):
            if day >= due:
                nxt = day + timedelta(days=VACCINE_INTERVALS.get(vaccine, 365))
                a.vaccines_due[vaccine] = nxt
                self._health_event(
                    a, day, "vaccination", vaccine, performed_by=VET, next_due_on=nxt
                )
        if age >= MIN_VACCINATION_AGE_DAYS:  # calves join the schedule once old enough
            for vaccine in VACCINE_INTERVALS:
                a.vaccines_due.setdefault(vaccine, day + timedelta(days=1))
        if a.sex == "female" and not a.brucellosis_done and age == BRUCELLOSIS_AGE_DAYS:
            a.brucellosis_done = True
            self._health_event(a, day, "vaccination", "Brucellosis (S19)", performed_by=VET)
        if a.deworm_due and day >= a.deworm_due:
            a.deworm_due = day + timedelta(days=DEWORMING_INTERVAL)
            self._health_event(
                a, day, "deworming", "Albendazole", performed_by=KEEPER, next_due_on=a.deworm_due
            )
        elif a.deworm_due is None and age >= 21:
            a.deworm_due = day
        # Mastitis in milking cows: illness -> treatment -> follow-up, milk withdrawn.
        if a.lactating and self.rng.random() < a.params.mastitis_per_day:
            quarter = self.rng.choice(["left fore", "right fore", "left hind", "right hind"])
            subject = f"Clinical mastitis ({quarter} quarter)"
            a.withdrawal_until = day + timedelta(days=MILK_WITHDRAWAL_DAYS)
            self._health_event(
                a,
                day,
                "illness",
                subject,
                performed_by=VET,
                temperature_c=round(float(self.rng.normal(39.6, 0.3)), 1),
            )
            self._health_event(
                a,
                day,
                "treatment",
                subject,
                performed_by=VET,
                treatment="Intramammary antibiotic 3 days; 5-day milk withdrawal",
                next_due_on=day + timedelta(days=7),
            )
        elif self.rng.random() < OTHER_ILLNESS_PER_DAY:
            subject = str(self.rng.choice(["Tick fever", "Foot rot", "Bloat", "Diarrhoea"]))
            self._health_event(
                a,
                day,
                "illness",
                subject,
                performed_by=VET,
                temperature_c=round(float(self.rng.normal(39.8, 0.4)), 1),
            )
            self._health_event(
                a,
                day,
                "treatment",
                subject,
                performed_by=VET,
                treatment="Treated by vet",
                next_due_on=day + timedelta(days=5),
            )

    def _monthly_checkup(self, day: date) -> None:
        for a in self.herd.values():
            age = a.age_days(day)
            if age < 30:
                continue
            if age < 700:  # still growing
                a.weight_kg += a.params.calf_gain_kg_per_day * 30 * float(self.rng.normal(1, 0.1))
            else:
                a.weight_kg *= float(self.rng.normal(1, 0.01))
            bcs = np.clip(self.rng.normal(3.0, 0.2), 2.0, 4.0)
            self._health_event(
                a,
                day,
                "checkup",
                "Monthly herd checkup",
                performed_by=KEEPER,
                weight_kg=round(a.weight_kg, 1),
                body_condition_score=round(float(bcs) * 4) / 4,
                temperature_c=round(float(self.rng.normal(38.6, 0.2)), 1),
            )

    def _health_event(self, a: Animal, day: date, event_type: str, subject: str, **kw) -> None:
        self.health.append(
            {"day": day, "cattle_code": a.code, "event_type": event_type, "subject": subject, **kw}
        )

    def _breeding_event(
        self, a: Animal, day: date, event_type: str, sire=None, calf=None, pregnant=None, notes=None
    ) -> None:
        self.breeding.append(
            {
                "day": day,
                "cow_code": a.code,
                "event_type": event_type,
                "sire_code": sire,
                "calf_code": calf,
                "pregnant": pregnant,
                "notes": notes,
            }
        )


# --- bees --------------------------------------------------------------------------

MAX_FRAMES = 8.0
GROWTH_PER_DAY = 0.05  # frames, at full flow, for a small colony
DEARTH_DECLINE_PER_DAY = 0.012
FORAGE_KG_PER_FRAME_DAY = 0.035  # honey gathered per frame of bees on a full-flow dry day
EAT_KG_PER_FRAME_DAY = 0.008
SUPER_CAPACITY_KG = 4.5
BACKGROUND_FLOW = 0.15  # forest always offers a little
COFFEE_BLOOM_BOOST = 1.5
NO_FORAGING_RAIN_MM = 5.0
HARVEST_MIN_KG = 1.8
HARVEST_LEAVE_KG = 0.5
HARVEST_MIN_GAP_DAYS = 30
KG_PER_FRAME_EXTRACTED = 0.45
ABSCOND_BASE, ABSCOND_WEAK = 0.0003, 0.004  # per day; "weak" = under 2.5 frames
SACBROOD_PER_DAY = 0.0002  # Jan-Apr
SUPERSEDURE_AGE_DAYS, SUPERSEDURE_PER_DAY = 540, 0.0015
SPLIT_DONOR_MIN_FRAMES, SPLIT_FRAMES = 5.0, 2.0  # Apis cerana colonies are small
EMPTY_BEFORE_RESTOCK_DAYS = 30


@dataclass
class Colony:
    hive_code: str
    origin: str
    established_on: date
    strength: float
    stores_kg: float
    queen_since: date
    queen_origin: str
    ended_on: date | None = None
    end_reason: str | None = None
    is_new: bool = False
    old_queens: list[tuple[str, date, date]] = field(default_factory=list)  # (origin, from, to)


class BeeModel:
    def __init__(self, rng: np.random.Generator, start: date) -> None:
        self.rng = rng
        self.hives = [h.code for h in hive_specs()]
        self.colonies: list[Colony] = []
        self.harvests: list[dict] = []
        self.daily: list[dict] = []
        self.last_harvest: dict[str, date] = {}
        self.empty_since: dict[str, date] = {}
        queens = queen_specs()
        for c in colony_specs():
            if c.ended_on is not None:
                self.empty_since.setdefault(c.hive_code, c.ended_on)
                continue
            reigning = next(
                q
                for q in queens
                if q.hive_code == c.hive_code
                and q.colony_established_on == c.established_on
                and q.replaced_on is None
            )
            self.colonies.append(
                Colony(
                    c.hive_code,
                    c.origin,
                    c.established_on,
                    strength=float(rng.uniform(4, 6)),
                    stores_kg=0.5,
                    queen_since=reigning.introduced_on,
                    queen_origin=reigning.origin,
                )
            )
        live = {c.hive_code for c in self.colonies}
        self.empty_since = {h: d for h, d in self.empty_since.items() if h not in live}
        self.last_harvest = {c.hive_code: start - timedelta(days=10) for c in self.colonies}

    def living(self) -> list[Colony]:
        return [c for c in self.colonies if c.ended_on is None]

    @staticmethod
    def flow_on(day: date) -> tuple[str | None, float]:
        for f in NECTAR_FLOWS:
            if f.starts_on <= day <= f.ends_on:
                return f.code, 1.0
        return None, BACKGROUND_FLOW

    def on_day(self, day: date, rain_mm: float, coffee_bloom: float, recent_rain_mm: float) -> None:
        flow_code, flow = self.flow_on(day)
        if flow_code and flow_code.endswith("COFFEE"):
            flow *= 1 + COFFEE_BLOOM_BOOST * coffee_bloom
        foraging = rain_mm < NO_FORAGING_RAIN_MM
        for c in self.living():
            # Growth during a flow (logistic: slows as the box fills), decline in dearth.
            if flow >= 1:
                c.strength += GROWTH_PER_DAY * min(flow, 2) * (1 - c.strength / MAX_FRAMES)
            else:
                c.strength -= DEARTH_DECLINE_PER_DAY
            c.strength = float(np.clip(c.strength, 0.5, MAX_FRAMES))
            gathered = FORAGE_KG_PER_FRAME_DAY * c.strength * flow if foraging else 0.0
            c.stores_kg = float(
                np.clip(
                    c.stores_kg + gathered - EAT_KG_PER_FRAME_DAY * c.strength, 0, SUPER_CAPACITY_KG
                )
            )
            self._risks(c, day)
        self._keeper(day, flow_code, recent_rain_mm)
        for c in self.living():
            self.daily.append(
                {
                    "day": day,
                    "hive_code": c.hive_code,
                    "strength_frames": round(c.strength, 2),
                    "stores_kg": round(c.stores_kg, 2),
                    "flow": round(flow, 2),
                }
            )

    def _risks(self, c: Colony, day: date) -> None:
        p_abscond = ABSCOND_BASE + (ABSCOND_WEAK if c.strength < 2.5 else 0)
        if self.rng.random() < p_abscond:
            self._end(c, day, "absconded")
            return
        if day.month <= 4 and self.rng.random() < SACBROOD_PER_DAY:
            self._end(c, day, "collapsed")
            return
        if (
            day - c.queen_since
        ).days > SUPERSEDURE_AGE_DAYS and self.rng.random() < SUPERSEDURE_PER_DAY:
            c.old_queens.append((c.queen_origin, c.queen_since, day))
            c.queen_since, c.queen_origin = day, "supersedure"
            c.strength = max(c.strength - 0.5, 0.5)  # brood break while the new queen mates

    def _end(self, c: Colony, day: date, reason: str) -> None:
        c.ended_on, c.end_reason = day, reason
        self.empty_since[c.hive_code] = day

    def _keeper(self, day: date, flow_code: str | None, recent_rain_mm: float) -> None:
        if day.weekday() != 5:  # the keeper works the hives on Saturdays
            return
        for c in self.living():
            since = (day - self.last_harvest.get(c.hive_code, date.min)).days
            if c.stores_kg >= HARVEST_MIN_KG and since >= HARVEST_MIN_GAP_DAYS:
                kg = round(c.stores_kg - HARVEST_LEAVE_KG, 2)
                c.stores_kg = HARVEST_LEAVE_KG
                self.last_harvest[c.hive_code] = day
                moisture = float(np.clip(20.0 + recent_rain_mm / 20, 18, 26))
                self.harvests.append(
                    {
                        "day": day,
                        "hive_code": c.hive_code,
                        "flow_code": flow_code,
                        "frames_extracted": int(
                            np.clip(math.ceil(kg / KG_PER_FRAME_EXTRACTED), 1, 8)
                        ),
                        "honey_kg": kg,
                        "moisture_pct": round(moisture, 1),
                        "harvested_by_code": BEEKEEPER_CODE,
                    }
                )
        # Re-stock one long-empty box per week by splitting the strongest colony, in a flow.
        if flow_code is None:
            return
        waiting = [
            h for h, d in self.empty_since.items() if (day - d).days >= EMPTY_BEFORE_RESTOCK_DAYS
        ]
        donors = sorted(self.living(), key=lambda c: -c.strength)
        if waiting and donors and donors[0].strength >= SPLIT_DONOR_MIN_FRAMES:
            hive = sorted(waiting)[0]
            donors[0].strength -= SPLIT_FRAMES
            self.colonies.append(
                Colony(hive, "split", day, SPLIT_FRAMES, 0.2, day, "original", is_new=True)
            )
            del self.empty_since[hive]
