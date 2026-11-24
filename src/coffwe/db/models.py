"""ORM models for the COFFWE estate.

Structure: blocks, species, tagged plants, and the named sites everything else sits at.
Resources: workers, assets (machines/equipment), sensor stations + sensors, livestock.
Flows: inventory items + their movement ledger, harvest batches + per-picker picks.
"""

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from coffwe.db.base import Base


class Slope(enum.StrEnum):
    GENTLE = "gentle"
    STEEP = "steep"


class SpeciesCategory(enum.StrEnum):
    COFFEE = "coffee"
    INTERCROP = "intercrop"
    SHADE = "shade"


class PlantStatus(enum.StrEnum):
    ACTIVE = "active"
    DEAD = "dead"
    REMOVED = "removed"


def str_enum(enum_cls: type[enum.StrEnum], name: str) -> Enum:
    """A VARCHAR column + CHECK constraint, rather than a native Postgres ENUM type.

    Native ENUM types are awkward to change later (Alembic can't autogenerate adding
    a value). A CHECK constraint gives the same "only these values" guarantee and is
    trivial to alter in a migration. Stores the enum's *value* ("gentle"), not its
    Python name ("GENTLE").
    """
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=20,
        values_callable=lambda cls: [member.value for member in cls],
    )


class Block(Base):
    __tablename__ = "blocks"
    __table_args__ = (
        CheckConstraint("area_acres > 0", name="area_positive"),
        CheckConstraint("buffer_acres >= 0 AND buffer_acres <= area_acres", name="buffer_range"),
        CheckConstraint("elevation_min_m <= elevation_max_m", name="elevation_range"),
        CheckConstraint("shade_pct BETWEEN 0 AND 100", name="shade_pct_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(10), unique=True)  # "B1" .. "B10"
    name: Mapped[str] = mapped_column(String(100))
    area_acres: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    buffer_acres: Mapped[Decimal] = mapped_column(Numeric(5, 2), server_default="0")
    elevation_min_m: Mapped[int]
    elevation_max_m: Mapped[int]
    slope: Mapped[Slope] = mapped_column(str_enum(Slope, "slope"))
    soil: Mapped[str] = mapped_column(String(100))
    shade_pct: Mapped[int]
    notes: Mapped[str | None] = mapped_column(Text)

    plants: Mapped[list[Plant]] = relationship(back_populates="block")

    def __repr__(self) -> str:
        return f"<Block {self.code} {self.name!r}>"


class Species(Base):
    __tablename__ = "species"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(4), unique=True)  # tag code, e.g. "RB"
    common_name: Mapped[str] = mapped_column(String(100))
    scientific_name: Mapped[str] = mapped_column(String(100), unique=True)
    category: Mapped[SpeciesCategory] = mapped_column(str_enum(SpeciesCategory, "category"))

    plants: Mapped[list[Plant]] = relationship(back_populates="species")

    def __repr__(self) -> str:
        return f"<Species {self.code} {self.common_name!r}>"


class Plant(Base):
    """One physically tagged individual: a coffee bush, pepper vine, areca palm or tree."""

    __tablename__ = "plants"
    __table_args__ = (
        UniqueConstraint("block_id", "species_id", "sequence"),
        CheckConstraint("sequence > 0", name="sequence_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # The label on the physical tag. Derivable from block + species + sequence, but
    # stored because it's how humans (and later the sensors/agents) refer to a plant.
    tag: Mapped[str] = mapped_column(String(16), unique=True)
    block_id: Mapped[int] = mapped_column(ForeignKey("blocks.id"), index=True)
    species_id: Mapped[int] = mapped_column(ForeignKey("species.id"), index=True)
    sequence: Mapped[int]
    status: Mapped[PlantStatus] = mapped_column(
        str_enum(PlantStatus, "status"), server_default=PlantStatus.ACTIVE.value
    )

    block: Mapped[Block] = relationship(back_populates="plants")
    species: Mapped[Species] = relationship(back_populates="plants")

    def __repr__(self) -> str:
        return f"<Plant {self.tag} {self.status}>"


# --- Sites -------------------------------------------------------------------------


class SiteKind(enum.StrEnum):
    BLOCK = "block"  # one site per cultivated block
    CORE = "core"  # estate-core facilities: processing, lab, nursery, sheds, housing
    WATER = "water"  # streams, check-dam/reservoir
    FOREST_EDGE = "forest_edge"  # corridor boundary facing the reserve forest


class Site(Base):
    """A named place on the estate -- the single answer to "where is it?"."""

    __tablename__ = "sites"
    __table_args__ = (
        # A block site must point at its block; no other kind of site may.
        CheckConstraint(
            "(kind = 'block') = (block_id IS NOT NULL)", name="block_kind_matches_block_id"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)  # "B3", "LAB", "CORRIDOR"
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[SiteKind] = mapped_column(str_enum(SiteKind, "kind"))
    block_id: Mapped[int | None] = mapped_column(ForeignKey("blocks.id"), unique=True)
    description: Mapped[str | None] = mapped_column(Text)

    block: Mapped[Block | None] = relationship()

    def __repr__(self) -> str:
        return f"<Site {self.code} {self.kind}>"


# --- Workers -----------------------------------------------------------------------


class EmploymentType(enum.StrEnum):
    PERMANENT = "permanent"
    SEASONAL = "seasonal"  # harvest-season contract, Nov-Feb


class WorkerRole(enum.StrEnum):
    MANAGER = "manager"
    SUPERVISOR = "supervisor"
    FIELD_WORKER = "field_worker"
    PICKER = "picker"
    PROCESSING_OPERATOR = "processing_operator"
    LAB_TECHNICIAN = "lab_technician"
    LIVESTOCK_KEEPER = "livestock_keeper"
    NURSERY_KEEPER = "nursery_keeper"
    MECHANIC = "mechanic"
    DRIVER = "driver"
    WATCHMAN = "watchman"


class Worker(Base):
    __tablename__ = "workers"
    __table_args__ = (
        CheckConstraint(
            "contract_ends_on IS NULL OR contract_ends_on >= hired_on", name="contract_dates"
        ),
        # Seasonal staff always have an end date; permanent staff never do.
        CheckConstraint(
            "(employment_type = 'seasonal') = (contract_ends_on IS NOT NULL)",
            name="seasonal_has_end_date",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(10), unique=True)  # "W001"
    full_name: Mapped[str] = mapped_column(String(100))
    employment_type: Mapped[EmploymentType] = mapped_column(
        str_enum(EmploymentType, "employment_type")
    )
    role: Mapped[WorkerRole] = mapped_column(str_enum(WorkerRole, "role"))
    housed_on_site: Mapped[bool] = mapped_column(server_default="false")
    hired_on: Mapped[date]
    contract_ends_on: Mapped[date | None]

    def __repr__(self) -> str:
        return f"<Worker {self.code} {self.full_name!r} {self.role}>"


# --- Assets ------------------------------------------------------------------------


class AssetType(enum.StrEnum):
    PROCESSING = "processing"
    DRYING = "drying"
    IRRIGATION = "irrigation"
    ENERGY = "energy"
    COMPOSTING = "composting"
    VEHICLE = "vehicle"
    TOOL = "tool"
    LAB_EQUIPMENT = "lab_equipment"


class AssetStatus(enum.StrEnum):
    OPERATIONAL = "operational"
    MAINTENANCE = "maintenance"
    FAULTY = "faulty"
    RETIRED = "retired"


class Asset(Base):
    """A machine, structure or piece of equipment the simulator can wear out or repair."""

    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)  # "PULPER-01"
    name: Mapped[str] = mapped_column(String(100))
    asset_type: Mapped[AssetType] = mapped_column(str_enum(AssetType, "asset_type"))
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), index=True)
    status: Mapped[AssetStatus] = mapped_column(
        str_enum(AssetStatus, "status"), server_default=AssetStatus.OPERATIONAL.value
    )
    # True for things the twin can command (Phase 8): dryer curtains, irrigation valves.
    actuatable: Mapped[bool] = mapped_column(server_default="false")
    commissioned_year: Mapped[int | None]
    notes: Mapped[str | None] = mapped_column(Text)

    site: Mapped[Site] = relationship()

    def __repr__(self) -> str:
        return f"<Asset {self.code} {self.status}>"


# --- Sensors -----------------------------------------------------------------------


class StationType(enum.StrEnum):
    WEATHER = "weather"  # estate-wide weather mast
    FIELD = "field"  # in-block soil + microclimate station
    PROCESS = "process"  # drying yard / processing
    WATER = "water"  # reservoir level, stream flow
    CORRIDOR = "corridor"  # motion/IR wildlife early warning


class StationStatus(enum.StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"
    MAINTENANCE = "maintenance"


class SensorStation(Base):
    """A physical box on a pole: power, radio, and one or more sensors."""

    __tablename__ = "sensor_stations"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)  # "FS-B3"
    name: Mapped[str] = mapped_column(String(100))
    station_type: Mapped[StationType] = mapped_column(str_enum(StationType, "station_type"))
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), index=True)
    status: Mapped[StationStatus] = mapped_column(
        str_enum(StationStatus, "status"), server_default=StationStatus.ONLINE.value
    )
    installed_on: Mapped[date]

    site: Mapped[Site] = relationship()
    sensors: Mapped[list[Sensor]] = relationship(back_populates="station")

    def __repr__(self) -> str:
        return f"<SensorStation {self.code} {self.status}>"


class Sensor(Base):
    """One measuring element on a station. Phase 3 time-series readings point here."""

    __tablename__ = "sensors"
    __table_args__ = (
        UniqueConstraint("station_id", "measurement"),
        CheckConstraint(
            "valid_min IS NULL OR valid_max IS NULL OR valid_min < valid_max", name="valid_range"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)  # "FS-B3-soil_moisture"
    station_id: Mapped[int] = mapped_column(ForeignKey("sensor_stations.id"), index=True)
    measurement: Mapped[str] = mapped_column(String(40))  # "soil_moisture"
    unit: Mapped[str] = mapped_column(String(10))  # "m3/m3", "degC", "mm"
    # Physically plausible range; readings outside it are faults, not weather.
    valid_min: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    valid_max: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))

    station: Mapped[SensorStation] = relationship(back_populates="sensors")

    def __repr__(self) -> str:
        return f"<Sensor {self.code} [{self.unit}]>"


# --- Livestock ---------------------------------------------------------------------


class AnimalType(enum.StrEnum):
    CATTLE = "cattle"
    POULTRY = "poultry"
    BEES = "bees"


class LivestockGroup(Base):
    """A herd, flock or apiary.

    Poultry is tracked only as a flock, so it carries a head_count. Cattle and bees are
    tracked individually (`cattle`, `hives`), so their count is derived from those rows
    and head_count must stay NULL -- a stored copy would drift out of sync.
    """

    __tablename__ = "livestock_groups"
    __table_args__ = (
        CheckConstraint("head_count >= 0", name="head_count_non_negative"),
        CheckConstraint(
            "(animal_type = 'poultry') = (head_count IS NOT NULL)"
            " AND (head_count IS NULL) = (count_unit IS NULL)",
            name="count_only_for_flock_tracked",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)  # "COWS"
    name: Mapped[str] = mapped_column(String(100))
    animal_type: Mapped[AnimalType] = mapped_column(str_enum(AnimalType, "animal_type"))
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), index=True)
    head_count: Mapped[int | None]
    count_unit: Mapped[str | None] = mapped_column(String(10))  # "birds"
    purpose: Mapped[str | None] = mapped_column(Text)

    site: Mapped[Site] = relationship()
    cattle: Mapped[list[Cattle]] = relationship(back_populates="group")
    hives: Mapped[list[Hive]] = relationship(back_populates="group")

    def __repr__(self) -> str:
        return f"<LivestockGroup {self.code} {self.animal_type}>"


# --- Inventory ---------------------------------------------------------------------


class InventoryCategory(enum.StrEnum):
    FERTILIZER = "fertilizer"
    BIOCONTROL = "biocontrol"  # neem, Beauveria, Bordeaux, traps and lures
    PACKAGING = "packaging"
    FUEL = "fuel"
    SPARE_PART = "spare_part"
    PLANTING_MATERIAL = "planting_material"  # nursery saplings and cuttings
    FEED = "feed"


class MovementReason(enum.StrEnum):
    OPENING_BALANCE = "opening_balance"
    PURCHASE = "purchase"
    PRODUCED = "produced"  # made on-estate: vermicompost, biogas slurry, saplings
    USED = "used"
    SOLD = "sold"
    ADJUSTMENT = "adjustment"  # stock-take corrections, spoilage; either sign


class InventoryItem(Base):
    """A stock-keeping item. Its quantity on hand is SUM(movements.quantity)."""

    __tablename__ = "inventory_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)  # "VERMICOMPOST"
    name: Mapped[str] = mapped_column(String(100))
    category: Mapped[InventoryCategory] = mapped_column(str_enum(InventoryCategory, "category"))
    unit: Mapped[str] = mapped_column(String(10))  # "kg", "L", "pcs"
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), index=True)  # stored at
    reorder_level: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))

    site: Mapped[Site] = relationship()
    movements: Mapped[list[InventoryMovement]] = relationship(back_populates="item")

    def __repr__(self) -> str:
        return f"<InventoryItem {self.code} [{self.unit}]>"


class InventoryMovement(Base):
    """One append-only ledger line: stock in (+) or out (-). Never updated, only added."""

    __tablename__ = "inventory_movements"
    __table_args__ = (
        CheckConstraint("quantity <> 0", name="quantity_non_zero"),
        CheckConstraint(
            "(reason IN ('opening_balance', 'purchase', 'produced') AND quantity > 0)"
            " OR (reason IN ('used', 'sold') AND quantity < 0)"
            " OR reason = 'adjustment'",
            name="sign_matches_reason",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id"), index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))  # signed, in the item's unit
    reason: Mapped[MovementReason] = mapped_column(str_enum(MovementReason, "reason"))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Where it was applied, when that's a block -- answers "how much compost did B3 get?"
    block_id: Mapped[int | None] = mapped_column(ForeignKey("blocks.id"), index=True)
    notes: Mapped[str | None] = mapped_column(Text)

    item: Mapped[InventoryItem] = relationship(back_populates="movements")
    block: Mapped[Block | None] = relationship()

    def __repr__(self) -> str:
        return f"<InventoryMovement {self.reason} {self.quantity:+}>"


# --- Harvest -----------------------------------------------------------------------


class ProcessingMethod(enum.StrEnum):
    WASHED = "washed"  # wet processing: pulper -> fermentation -> washing channel
    NATURAL = "natural"  # dry processing: whole cherry on beds / polyhouse dryer


class BatchStatus(enum.StrEnum):
    RECEIVED = "received"
    PROCESSING = "processing"
    DRYING = "drying"
    STORED = "stored"
    DISPATCHED = "dispatched"


class HarvestBatch(Base):
    """One traceable lot: a single crop, from a single block, picked on a single day.

    Usually coffee; pepper and areca batches use the same table (processing_method
    then stays NULL). Restricting species to crops is enforced in application code.
    """

    __tablename__ = "harvest_batches"
    __table_args__ = (
        UniqueConstraint("block_id", "species_id", "harvested_on"),
        CheckConstraint("cherry_kg > 0", name="cherry_kg_positive"),
        CheckConstraint(
            "output_kg IS NULL OR (output_kg > 0 AND output_kg <= cherry_kg)",
            name="output_within_cherry",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)  # "HB-20261124-B4-AR"
    block_id: Mapped[int] = mapped_column(ForeignKey("blocks.id"), index=True)
    species_id: Mapped[int] = mapped_column(ForeignKey("species.id"), index=True)
    harvested_on: Mapped[date]
    # Weighed at cherry receiving -- the official batch weight. The per-picker field
    # weights in harvest_picks may not sum to exactly this (spillage, sorting).
    cherry_kg: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    processing_method: Mapped[ProcessingMethod | None] = mapped_column(
        str_enum(ProcessingMethod, "processing_method")
    )
    status: Mapped[BatchStatus] = mapped_column(
        str_enum(BatchStatus, "status"), server_default=BatchStatus.RECEIVED.value
    )
    output_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))  # dried weight
    notes: Mapped[str | None] = mapped_column(Text)

    block: Mapped[Block] = relationship()
    species: Mapped[Species] = relationship()
    picks: Mapped[list[HarvestPick]] = relationship(back_populates="batch")

    def __repr__(self) -> str:
        return f"<HarvestBatch {self.code} {self.cherry_kg} kg {self.status}>"


class HarvestPick(Base):
    """How much one worker contributed to one batch (field weight, used for pay)."""

    __tablename__ = "harvest_picks"
    __table_args__ = (
        UniqueConstraint("batch_id", "worker_id"),
        CheckConstraint("cherry_kg > 0", name="cherry_kg_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("harvest_batches.id"), index=True)
    worker_id: Mapped[int] = mapped_column(ForeignKey("workers.id"), index=True)
    cherry_kg: Mapped[Decimal] = mapped_column(Numeric(8, 2))

    batch: Mapped[HarvestBatch] = relationship(back_populates="picks")
    worker: Mapped[Worker] = relationship()

    def __repr__(self) -> str:
        return f"<HarvestPick batch={self.batch_id} worker={self.worker_id} {self.cherry_kg} kg>"


# --- Cattle ------------------------------------------------------------------------


class Sex(enum.StrEnum):
    FEMALE = "female"
    MALE = "male"


class CattleBreed(enum.StrEnum):
    MALNAD_GIDDA = "malnad_gidda"  # native Western Ghats dwarf breed, ~1-3 L/day
    JERSEY_CROSS = "jersey_cross"  # Jersey x local, ~8-12 L/day, needs more feed/care


class CattleOrigin(enum.StrEnum):
    BORN_ON_ESTATE = "born_on_estate"
    PURCHASED = "purchased"


class AnimalStatus(enum.StrEnum):
    ACTIVE = "active"
    SOLD = "sold"
    DECEASED = "deceased"


class Cattle(Base):
    """One animal. dam/sire point back into this same table: that's the family tree."""

    __tablename__ = "cattle"
    __table_args__ = (
        CheckConstraint("dam_id <> id AND sire_id <> id", name="not_own_parent"),
        CheckConstraint("(status = 'active') = (left_on IS NULL)", name="left_iff_inactive"),
        CheckConstraint("left_on IS NULL OR left_on >= born_on", name="left_after_birth"),
        CheckConstraint(
            "(origin = 'purchased') = (acquired_on IS NOT NULL)"
            " AND (acquired_on IS NULL OR acquired_on >= born_on)",
            name="purchase_dates",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(10), unique=True)  # "C01"
    name: Mapped[str] = mapped_column(String(50))
    sex: Mapped[Sex] = mapped_column(str_enum(Sex, "sex"))
    breed: Mapped[CattleBreed] = mapped_column(str_enum(CattleBreed, "breed"))
    born_on: Mapped[date]
    origin: Mapped[CattleOrigin] = mapped_column(str_enum(CattleOrigin, "origin"))
    acquired_on: Mapped[date | None]
    # NULL for founders whose parents were never recorded on the estate.
    dam_id: Mapped[int | None] = mapped_column(ForeignKey("cattle.id"), index=True)  # mother
    sire_id: Mapped[int | None] = mapped_column(ForeignKey("cattle.id"), index=True)  # father
    group_id: Mapped[int] = mapped_column(ForeignKey("livestock_groups.id"), index=True)
    status: Mapped[AnimalStatus] = mapped_column(
        str_enum(AnimalStatus, "status"), server_default=AnimalStatus.ACTIVE.value
    )
    left_on: Mapped[date | None]  # sold / died
    notes: Mapped[str | None] = mapped_column(Text)

    # Self-referential: two FKs to the same table, so each relationship must say which
    # column it uses (foreign_keys) and which side is the "parent" (remote_side).
    dam: Mapped[Cattle | None] = relationship(
        foreign_keys=[dam_id], remote_side="Cattle.id", back_populates="offspring_as_dam"
    )
    sire: Mapped[Cattle | None] = relationship(
        foreign_keys=[sire_id], remote_side="Cattle.id", back_populates="offspring_as_sire"
    )
    offspring_as_dam: Mapped[list[Cattle]] = relationship(
        foreign_keys=[dam_id], back_populates="dam"
    )
    offspring_as_sire: Mapped[list[Cattle]] = relationship(
        foreign_keys=[sire_id], back_populates="sire"
    )
    group: Mapped[LivestockGroup] = relationship(back_populates="cattle")
    health_events: Mapped[list[CattleHealthEvent]] = relationship(back_populates="animal")
    milkings: Mapped[list[Milking]] = relationship(back_populates="cow")

    def __repr__(self) -> str:
        return f"<Cattle {self.code} {self.name!r} {self.sex} {self.breed}>"


class HealthEventType(enum.StrEnum):
    CHECKUP = "checkup"  # routine weigh + body condition + temperature
    VACCINATION = "vaccination"  # FMD, HS, BQ, brucellosis, lumpy skin...
    DEWORMING = "deworming"
    ILLNESS = "illness"  # diagnosis, e.g. mastitis
    TREATMENT = "treatment"
    INJURY = "injury"


class CattleHealthEvent(Base):
    __tablename__ = "cattle_health_events"
    __table_args__ = (
        CheckConstraint(
            "body_condition_score IS NULL OR body_condition_score BETWEEN 1 AND 5",
            name="bcs_range",
        ),
        CheckConstraint(
            "temperature_c IS NULL OR temperature_c BETWEEN 35 AND 43", name="temperature_range"
        ),
        CheckConstraint("weight_kg IS NULL OR weight_kg > 0", name="weight_positive"),
        CheckConstraint(
            "next_due_on IS NULL OR next_due_on > occurred_on", name="next_due_after_event"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    cattle_id: Mapped[int] = mapped_column(ForeignKey("cattle.id"), index=True)
    occurred_on: Mapped[date]
    event_type: Mapped[HealthEventType] = mapped_column(str_enum(HealthEventType, "event_type"))
    subject: Mapped[str | None] = mapped_column(String(100))  # "FMD", "Mastitis (LH quarter)"
    treatment: Mapped[str | None] = mapped_column(Text)
    performed_by: Mapped[str | None] = mapped_column(String(100))  # vet or keeper
    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(6, 1))
    body_condition_score: Mapped[Decimal | None] = mapped_column(Numeric(2, 1))  # 1-5 scale
    temperature_c: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    next_due_on: Mapped[date | None]  # booster / follow-up date
    notes: Mapped[str | None] = mapped_column(Text)

    animal: Mapped[Cattle] = relationship(back_populates="health_events")

    def __repr__(self) -> str:
        return f"<CattleHealthEvent {self.event_type} {self.subject!r} on {self.occurred_on}>"


class BreedingEventType(enum.StrEnum):
    HEAT = "heat"  # oestrus observed
    MATING = "mating"  # with the estate bull
    PREGNANCY_CHECK = "pregnancy_check"
    CALVING = "calving"  # calf_id points at the newborn's cattle row
    ABORTION = "abortion"
    DRY_OFF = "dry_off"  # milking stopped ahead of the next calving


class CattleBreedingEvent(Base):
    """A cow's reproductive history. Calving links the calf; mating links the sire."""

    __tablename__ = "cattle_breeding_events"
    __table_args__ = (
        CheckConstraint("(event_type = 'mating') = (sire_id IS NOT NULL)", name="sire_iff_mating"),
        CheckConstraint(
            "(event_type = 'calving') = (calf_id IS NOT NULL)", name="calf_iff_calving"
        ),
        CheckConstraint(
            "(event_type = 'pregnancy_check') = (pregnant IS NOT NULL)",
            name="result_iff_pregnancy_check",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    cow_id: Mapped[int] = mapped_column(ForeignKey("cattle.id"), index=True)
    occurred_on: Mapped[date]
    event_type: Mapped[BreedingEventType] = mapped_column(str_enum(BreedingEventType, "event_type"))
    sire_id: Mapped[int | None] = mapped_column(ForeignKey("cattle.id"))
    calf_id: Mapped[int | None] = mapped_column(ForeignKey("cattle.id"), unique=True)
    pregnant: Mapped[bool | None]
    notes: Mapped[str | None] = mapped_column(Text)

    cow: Mapped[Cattle] = relationship(foreign_keys=[cow_id])
    sire: Mapped[Cattle | None] = relationship(foreign_keys=[sire_id])
    calf: Mapped[Cattle | None] = relationship(foreign_keys=[calf_id])

    def __repr__(self) -> str:
        return f"<CattleBreedingEvent {self.event_type} cow={self.cow_id} on {self.occurred_on}>"


class MilkingSession(enum.StrEnum):
    MORNING = "morning"
    EVENING = "evening"


class Milking(Base):
    """One cow, one milking session. Lactation curves are built from these rows."""

    __tablename__ = "milkings"
    __table_args__ = (
        UniqueConstraint("cattle_id", "milked_on", "session"),
        CheckConstraint("litres > 0", name="litres_positive"),
        CheckConstraint("fat_pct IS NULL OR fat_pct BETWEEN 0 AND 15", name="fat_pct_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    cattle_id: Mapped[int] = mapped_column(ForeignKey("cattle.id"), index=True)
    milked_on: Mapped[date]
    session: Mapped[MilkingSession] = mapped_column(str_enum(MilkingSession, "session"))
    litres: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    fat_pct: Mapped[Decimal | None] = mapped_column(Numeric(4, 2))
    milked_by_id: Mapped[int | None] = mapped_column(ForeignKey("workers.id"))

    cow: Mapped[Cattle] = relationship(back_populates="milkings")

    def __repr__(self) -> str:
        return f"<Milking cow={self.cattle_id} {self.milked_on} {self.session} {self.litres} L>"


# --- Bees --------------------------------------------------------------------------


class HiveBoxType(enum.StrEnum):
    NEWTON = "newton"  # Indian standard box for Apis cerana: 8 smaller frames
    LANGSTROTH = "langstroth"  # larger 10-frame box for Apis mellifera (not used yet)


class Hive(Base):
    """The physical box. Colonies come and go; the box stays (until retired)."""

    __tablename__ = "hives"
    __table_args__ = (
        CheckConstraint("brood_frames BETWEEN 1 AND 10", name="brood_frames_range"),
        CheckConstraint("super_count BETWEEN 0 AND 3", name="super_count_range"),
        CheckConstraint("retired_on IS NULL OR retired_on >= installed_on", name="retired_after"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(10), unique=True)  # "HV-01"
    group_id: Mapped[int] = mapped_column(ForeignKey("livestock_groups.id"), index=True)
    fence_position_m: Mapped[int]  # distance along the corridor boundary, for the map
    box_type: Mapped[HiveBoxType] = mapped_column(str_enum(HiveBoxType, "box_type"))
    brood_frames: Mapped[int]  # frames in the brood chamber
    super_count: Mapped[int]  # honey chambers stacked on top
    installed_on: Mapped[date]
    retired_on: Mapped[date | None]  # NULL = box still in service
    notes: Mapped[str | None] = mapped_column(Text)

    group: Mapped[LivestockGroup] = relationship(back_populates="hives")
    colonies: Mapped[list[BeeColony]] = relationship(back_populates="hive")
    harvests: Mapped[list[HoneyHarvest]] = relationship(back_populates="hive")

    def __repr__(self) -> str:
        return f"<Hive {self.code} {self.box_type}>"


class ColonyOrigin(enum.StrEnum):
    SWARM_CAPTURE = "swarm_capture"
    SPLIT = "split"  # divided from another estate colony
    PURCHASED = "purchased"


class ColonyEndReason(enum.StrEnum):
    ABSCONDED = "absconded"  # whole colony leaves -- common in Apis cerana
    COLLAPSED = "collapsed"  # died out, e.g. Thai sacbrood virus
    MERGED = "merged"  # combined into another colony


class BeeColony(Base):
    """A colony's occupancy of one hive box, from establishment to its end (if any)."""

    __tablename__ = "bee_colonies"
    __table_args__ = (
        CheckConstraint("(ended_on IS NULL) = (end_reason IS NULL)", name="end_reason_iff_ended"),
        CheckConstraint("ended_on IS NULL OR ended_on >= established_on", name="ended_after"),
        # At most one *living* colony per box: a partial unique index (WHERE ended_on IS
        # NULL) -- ended colonies don't count, so a box can have any number of those.
        Index(
            "uq_bee_colonies_one_active_per_hive",
            "hive_id",
            unique=True,
            postgresql_where=text("ended_on IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    hive_id: Mapped[int] = mapped_column(ForeignKey("hives.id"), index=True)
    species_name: Mapped[str] = mapped_column(String(50), server_default="Apis cerana indica")
    origin: Mapped[ColonyOrigin] = mapped_column(str_enum(ColonyOrigin, "origin"))
    established_on: Mapped[date]
    ended_on: Mapped[date | None]
    end_reason: Mapped[ColonyEndReason | None] = mapped_column(
        str_enum(ColonyEndReason, "end_reason")
    )

    hive: Mapped[Hive] = relationship(back_populates="colonies")
    queens: Mapped[list[Queen]] = relationship(back_populates="colony")

    def __repr__(self) -> str:
        return f"<BeeColony hive={self.hive_id} since {self.established_on}>"


class QueenOrigin(enum.StrEnum):
    ORIGINAL = "original"  # came with the colony
    SUPERSEDURE = "supersedure"  # colony raised its own replacement
    REQUEENED = "requeened"  # beekeeper introduced a new queen


class Queen(Base):
    __tablename__ = "queens"
    __table_args__ = (
        CheckConstraint(
            "replaced_on IS NULL OR replaced_on >= introduced_on", name="replaced_after"
        ),
        Index(
            "uq_queens_one_reigning_per_colony",
            "colony_id",
            unique=True,
            postgresql_where=text("replaced_on IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    colony_id: Mapped[int] = mapped_column(ForeignKey("bee_colonies.id"), index=True)
    origin: Mapped[QueenOrigin] = mapped_column(str_enum(QueenOrigin, "origin"))
    introduced_on: Mapped[date]
    replaced_on: Mapped[date | None]  # NULL = still reigning (or colony ended with her)

    colony: Mapped[BeeColony] = relationship(back_populates="queens")

    def __repr__(self) -> str:
        return f"<Queen colony={self.colony_id} since {self.introduced_on}>"


class NectarFlow(Base):
    """A flowering season that bees forage on, e.g. the coffee-blossom flow."""

    __tablename__ = "nectar_flows"
    __table_args__ = (CheckConstraint("ends_on > starts_on", name="ends_after_starts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)  # "2026-COFFEE"
    name: Mapped[str] = mapped_column(String(100))
    flora: Mapped[str | None] = mapped_column(Text)
    starts_on: Mapped[date]
    ends_on: Mapped[date]

    def __repr__(self) -> str:
        return f"<NectarFlow {self.code} {self.starts_on}..{self.ends_on}>"


class HoneyHarvest(Base):
    __tablename__ = "honey_harvests"
    __table_args__ = (
        UniqueConstraint("hive_id", "harvested_on"),
        CheckConstraint("honey_kg > 0", name="honey_kg_positive"),
        CheckConstraint("frames_extracted > 0", name="frames_positive"),
        CheckConstraint(
            "moisture_pct IS NULL OR moisture_pct BETWEEN 10 AND 30", name="moisture_range"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    hive_id: Mapped[int] = mapped_column(ForeignKey("hives.id"), index=True)
    flow_id: Mapped[int | None] = mapped_column(ForeignKey("nectar_flows.id"), index=True)
    harvested_on: Mapped[date]
    frames_extracted: Mapped[int]
    honey_kg: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    # Apis cerana honey runs wetter than mellifera; >~22% risks fermentation.
    moisture_pct: Mapped[Decimal | None] = mapped_column(Numeric(3, 1))
    harvested_by_id: Mapped[int | None] = mapped_column(ForeignKey("workers.id"))
    notes: Mapped[str | None] = mapped_column(Text)

    hive: Mapped[Hive] = relationship(back_populates="harvests")
    flow: Mapped[NectarFlow | None] = relationship()

    def __repr__(self) -> str:
        return f"<HoneyHarvest hive={self.hive_id} {self.harvested_on} {self.honey_kg} kg>"
