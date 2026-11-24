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
    Numeric,
    String,
    Text,
    UniqueConstraint,
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
    """A herd, flock or hive line. Tracked as groups: individuals don't matter here."""

    __tablename__ = "livestock_groups"
    __table_args__ = (CheckConstraint("head_count >= 0", name="head_count_non_negative"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)  # "COWS"
    name: Mapped[str] = mapped_column(String(100))
    animal_type: Mapped[AnimalType] = mapped_column(str_enum(AnimalType, "animal_type"))
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), index=True)
    head_count: Mapped[int]
    count_unit: Mapped[str] = mapped_column(String(10))  # "head", "birds", "hives"
    purpose: Mapped[str | None] = mapped_column(Text)

    site: Mapped[Site] = relationship()

    def __repr__(self) -> str:
        return f"<LivestockGroup {self.code} {self.head_count} {self.count_unit}>"


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
