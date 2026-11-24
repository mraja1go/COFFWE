"""The COFFWE estate (docs/estate-concept.md) expressed as plain Python data.

This module has no database dependency on purpose: tests can check that the numbers
add up (70 acres, 44,950 tags, ...) without Postgres running, and the seed script
turns exactly this data into rows.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

# --- Species -----------------------------------------------------------------------


@dataclass(frozen=True)
class SpeciesSpec:
    code: str  # 2-letter tag code, e.g. "RB"
    common_name: str
    scientific_name: str
    category: str  # "coffee" | "intercrop" | "shade"


SPECIES: list[SpeciesSpec] = [
    SpeciesSpec("AR", "Arabica", "Coffea arabica", "coffee"),
    SpeciesSpec("RB", "Robusta", "Coffea canephora", "coffee"),
    SpeciesSpec("PP", "Black pepper", "Piper nigrum", "intercrop"),
    SpeciesSpec("AN", "Areca nut", "Areca catechu", "intercrop"),
    SpeciesSpec("SO", "Silver oak", "Grevillea robusta", "shade"),
    SpeciesSpec("DD", "Dadap", "Erythrina subumbrans", "shade"),
    SpeciesSpec("WJ", "Wild jack (Ainili)", "Artocarpus hirsutus", "shade"),
    SpeciesSpec("JF", "Jackfruit", "Artocarpus heterophyllus", "shade"),
    SpeciesSpec("RW", "Indian rosewood", "Dalbergia latifolia", "shade"),
    SpeciesSpec("HG", "Indian beech (Honge)", "Pongamia pinnata", "shade"),
    SpeciesSpec("KD", "Kindal", "Terminalia paniculata", "shade"),
    SpeciesSpec("MT", "Matti", "Terminalia elliptica", "shade"),
    SpeciesSpec("PN", "Indian laurel (Punna)", "Calophyllum inophyllum", "shade"),
    SpeciesSpec("AT", "Cluster fig (Atthi)", "Ficus racemosa", "shade"),
    SpeciesSpec("BY", "Banyan", "Ficus benghalensis", "shade"),
    SpeciesSpec("IW", "Ironwood (Nagasampige)", "Mesua ferrea", "shade"),
    SpeciesSpec("HP", "Hopea", "Hopea parviflora", "shade"),
    SpeciesSpec("WD", "White dammar (Dhoopa)", "Vateria indica", "shade"),
    SpeciesSpec("NM", "Wild nutmeg", "Myristica malabarica", "shade"),
    SpeciesSpec("WC", "Wild cinnamon", "Cinnamomum malabatrum", "shade"),
    SpeciesSpec("KB", "Kadamba", "Neolamarckia cadamba", "shade"),
    SpeciesSpec("JM", "Jamun (Naval)", "Syzygium cumini", "shade"),
    SpeciesSpec("KN", "Indian kino (Honne)", "Pterocarpus marsupium", "shade"),
    SpeciesSpec("MG", "Mango", "Mangifera indica", "shade"),
    SpeciesSpec("BB", "Bamboo", "Bambusa bambos", "shade"),
]

# --- Blocks ------------------------------------------------------------------------


@dataclass(frozen=True)
class BlockSpec:
    code: str
    name: str
    area_acres: Decimal  # total block area, including any forest buffer
    buffer_acres: Decimal  # uncultivated forest buffer inside the block
    elevation_min_m: int
    elevation_max_m: int
    slope: str  # "gentle" | "steep"
    soil: str
    shade_pct: int  # canopy shade over the cultivated area; 0 = open sun
    plantings: dict[str, int]  # coffee + intercrop counts by species code
    shade_trees: int  # total shade/native trees, split by species via weights below
    notes: str | None = None


D = Decimal
BLOCKS: list[BlockSpec] = [
    BlockSpec("B1", "Stream Bend", D(5), D(0), 1200, 1210, "gentle",
              "Alluvial-lateritic loam", 100, {"RB": 2500, "PP": 750, "AN": 40}, 350,
              "Borders Stream 1; nearest to entrance; areca on border"),
    BlockSpec("B2", "Lower Robusta", D(6), D(0), 1205, 1225, "gentle",
              "Red lateritic loam", 100, {"RB": 3000, "PP": 900}, 420),
    BlockSpec("B3", "Areca Grove", D(7), D(0), 1210, 1235, "gentle",
              "Lateritic loam, well-drained", 100, {"RB": 3150, "AN": 1260, "PP": 1120}, 280,
              "Dedicated areca-coffee-pepper polyculture"),
    BlockSpec("B4", "Transition Lower", D(8), D(0), 1225, 1250, "gentle",
              "Lateritic loam", 90, {"RB": 2000, "AR": 2000, "PP": 1200}, 560,
              "First Arabica/Robusta transition block; long gentle bump"),
    BlockSpec("B5", "Valley Steep", D(6), D(0), 1235, 1260, "steep",
              "Thin rocky lateritic", 100, {"AR": 1500, "RB": 1500, "PP": 600, "AN": 60}, 540,
              "Borders Stream 2; erosion-control shade; areca on border"),
    BlockSpec("B6", "Mid Transition", D(7), D(0), 1250, 1270, "gentle",
              "Loam", 85, {"AR": 2100, "RB": 1400, "PP": 1050}, 490,
              "Flanks the estate core (west)"),
    BlockSpec("B7", "Upper Transition", D(8), D(0), 1260, 1280, "gentle",
              "Sandy loam", 90, {"AR": 2800, "RB": 800, "PP": 1120}, 560,
              "Flanks the estate core (east)"),
    BlockSpec("B8", "High Arabica I", D(7), D(0), 1270, 1290, "gentle",
              "Well-drained lateritic loam", 100, {"AR": 3500, "PP": 1050}, 490,
              "Prime Arabica block"),
    BlockSpec("B9", "High Arabica II, Ridge", D(9), D(0), 1280, 1300, "steep",
              "Thin rocky lateritic", 100, {"AR": 2450, "PP": 900}, 810,
              "Wide spacing + extra shade for ridge erosion control"),
    BlockSpec("B10", "Open-Sun Trial & Forest Edge", D(7), D(3), 1285, 1300, "steep",
              "Thin lateritic / forest humus", 0, {"AR": 700, "RB": 700}, 300,
              "Open-sun growth/yield trial vs. shaded blocks; 3 ac forest buffer "
              "(all 300 trees are in the buffer)"),
]  # fmt: skip

# --- Shade-tree species mix --------------------------------------------------------
# The concept doc fixes trees per block but not per species. These relative weights
# are a deliberate, documented modelling choice (decided 2026-11-24):
#   * Planted shade blocks: silver oak dominant (the standard pepper-support tree),
#     dadap common (classic lopped coffee shade), then fruit/timber natives, rare
#     forest species scarce.
#   * Forest buffer (B10): natural forest -- no silver oak (non-native) or dadap
#     (a planted shade tree); evergreen Western Ghats species dominate.

SHADE_WEIGHTS: dict[str, int] = {
    "SO": 35, "DD": 10, "JF": 6, "RW": 6, "WJ": 5, "HG": 4, "KD": 4, "MT": 4,
    "KB": 3, "JM": 3, "MG": 3, "PN": 3, "AT": 3, "BB": 2, "IW": 2, "HP": 2,
    "WD": 2, "KN": 2, "NM": 1, "WC": 1, "BY": 1,
}  # fmt: skip

FOREST_WEIGHTS: dict[str, int] = {
    "HP": 10, "WD": 10, "WJ": 8, "RW": 8, "IW": 8, "KD": 8, "MT": 8, "NM": 6,
    "WC": 6, "KN": 6, "AT": 6, "BB": 6, "JM": 5, "KB": 5, "BY": 3, "MG": 3,
    "PN": 3, "HG": 3, "JF": 2,
}  # fmt: skip

FOREST_BUFFER_BLOCKS = {"B10"}


def apportion(total: int, weights: dict[str, int]) -> dict[str, int]:
    """Split `total` into whole numbers proportional to `weights`, summing exactly.

    Largest-remainder method: give everyone the floor of their share, then hand the
    leftover units to the biggest fractional remainders. Deterministic -- ties go to
    whichever key comes first in `weights` -- so every run seeds the same estate.
    """
    weight_sum = sum(weights.values())
    exact = {k: total * w / weight_sum for k, w in weights.items()}
    result = {k: int(v) for k, v in exact.items()}
    leftover = total - sum(result.values())
    order = sorted(weights, key=lambda k: exact[k] - result[k], reverse=True)
    for k in order[:leftover]:
        result[k] += 1
    return result


def block_species_counts(block: BlockSpec) -> dict[str, int]:
    """Every species planted in a block (coffee, intercrop and shade) with its count."""
    weights = FOREST_WEIGHTS if block.code in FOREST_BUFFER_BLOCKS else SHADE_WEIGHTS
    counts = dict(block.plantings)
    for code, n in apportion(block.shade_trees, weights).items():
        if n:
            counts[code] = counts.get(code, 0) + n
    return counts


def make_tag(block_code: str, species_code: str, sequence: int) -> str:
    """Physical tag label, e.g. make_tag("B3", "RB", 147) -> "B3-RB-00147"."""
    return f"{block_code}-{species_code}-{sequence:05d}"


def plant_specs() -> Iterator[tuple[str, str, int, str]]:
    """Yield (block_code, species_code, sequence, tag) for every tagged plant.

    Sequence numbers restart at 1 for each species within each block.
    """
    for block in BLOCKS:
        for species_code, n in block_species_counts(block).items():
            for seq in range(1, n + 1):
                yield block.code, species_code, seq, make_tag(block.code, species_code, seq)


# ===================================================================================
# Resources & flows (v1.2, 2026-11-24). The concept doc specifies these only loosely;
# the counts below are deliberate modelling choices, documented in estate-concept.md.
# ===================================================================================

# --- Sites -------------------------------------------------------------------------


@dataclass(frozen=True)
class SiteSpec:
    code: str
    name: str
    kind: str  # "block" | "core" | "water" | "forest_edge"
    description: str | None = None
    block_code: str | None = None


SITES: list[SiteSpec] = [
    *(SiteSpec(b.code, b.name, "block", block_code=b.code) for b in BLOCKS),
    SiteSpec("PROC", "Wet processing yard", "core",
             "Cherry receiving & sorting, pulper, settling tanks, fermentation, washing"),
    SiteSpec("DRY", "Drying yard", "core", "Raised sun-drying beds + parabolic polyhouse dryer"),
    SiteSpec("STORE", "Storage warehouse", "core", "Dried coffee, pepper, inputs and packaging"),
    SiteSpec("LAB", "Estate Lab", "core",
             "All-purpose lab: organic fertilizer preparation, soil testing and research"),
    SiteSpec("COMPOST", "Compost & biogas unit", "core", "Vermicompost pits and biogas digester"),
    SiteSpec("NURSERY", "Nursery", "core",
             "Coffee, pepper, areca and native saplings; quarantine bay for new stock"),
    SiteSpec("COWSHED", "Cow shed", "core"),
    SiteSpec("COOP", "Chicken coop", "core", "Night housing; flock forages near block edges"),
    SiteSpec("HOUSING", "Worker housing", "core"),
    SiteSpec("WORKSHOP", "Workshop & garage", "core", "Vehicles, tools, generator, spares"),
    SiteSpec("RESERVOIR", "Stream 1 check-dam & reservoir", "water",
             "Feeds the dry-season drip/sprinkler network; near B1"),
    SiteSpec("STREAM2", "Stream 2 (B5 valley)", "water"),
    SiteSpec("CORRIDOR", "Forest corridor boundary", "forest_edge",
             "Faces the reserve forest above B9/B10: beehive fence, IR sensors, thorn hedge"),
]  # fmt: skip

# --- Workers -----------------------------------------------------------------------
# 15 permanent staff (per the concept doc) + 45 seasonal pickers for Nov-Feb.
# Names are fictional, generated deterministically from the lists below.

FIRST_NAMES = [
    "Ravi", "Lakshmi", "Suresh", "Kaveri", "Manjunath", "Shobha", "Prakash", "Geetha",
    "Mahesh", "Savitha", "Ramesh", "Kumari", "Nagesh", "Pushpa", "Ganesh", "Meena",
    "Somanna", "Bhavani", "Chandru", "Rekha", "Kiran", "Sunitha", "Basavaraj", "Nirmala",
    "Harish", "Leela", "Mohan", "Sharada", "Venkatesh", "Parvathi", "Anand", "Yashoda",
]  # fmt: skip
FAMILY_NAMES = [
    "Gowda", "Naik", "Shetty", "Hegde", "Rai", "Kumar", "Poojary", "Bhat", "Achari",
    "Murthy", "Devaru", "Shenoy", "Kamath", "Swamy", "Appaiah",
]  # fmt: skip

# (role, housed_on_site, hired_on) for the 15 permanent staff
PERMANENT_STAFF: list[tuple[str, bool, date]] = [
    ("manager", True, date(2009, 6, 1)),
    ("supervisor", True, date(2012, 3, 15)),
    ("lab_technician", True, date(2021, 7, 1)),
    ("processing_operator", True, date(2014, 10, 1)),
    ("processing_operator", False, date(2018, 10, 1)),
    ("mechanic", True, date(2016, 1, 10)),
    ("nursery_keeper", False, date(2015, 5, 20)),
    ("livestock_keeper", True, date(2017, 2, 1)),
    ("driver", False, date(2019, 4, 1)),
    ("watchman", True, date(2013, 8, 1)),
    ("field_worker", False, date(2011, 1, 5)),
    ("field_worker", False, date(2016, 6, 1)),
    ("field_worker", False, date(2020, 1, 15)),
    ("field_worker", False, date(2022, 6, 1)),
    ("field_worker", False, date(2024, 3, 1)),
]
SEASONAL_PICKERS = 45
SEASON_START, SEASON_END = date(2026, 11, 1), date(2027, 2, 28)


@dataclass(frozen=True)
class WorkerSpec:
    code: str
    full_name: str
    employment_type: str
    role: str
    housed_on_site: bool
    hired_on: date
    contract_ends_on: date | None


def worker_specs() -> list[WorkerSpec]:
    """15 permanent + 45 seasonal workers, W001..W060, with unique fictional names."""
    total = len(PERMANENT_STAFF) + SEASONAL_PICKERS
    # Stepping through first/family names with co-prime strides gives unique pairs.
    names = [
        f"{FIRST_NAMES[i % len(FIRST_NAMES)]} {FAMILY_NAMES[(i * 7) % len(FAMILY_NAMES)]}"
        for i in range(total)
    ]
    specs = [
        WorkerSpec(f"W{i + 1:03d}", names[i], "permanent", role, housed, hired, None)
        for i, (role, housed, hired) in enumerate(PERMANENT_STAFF)
    ]
    for j in range(SEASONAL_PICKERS):
        i = len(PERMANENT_STAFF) + j
        specs.append(
            WorkerSpec(
                f"W{i + 1:03d}", names[i], "seasonal", "picker", False, SEASON_START, SEASON_END
            )
        )
    return specs


# --- Assets ------------------------------------------------------------------------


@dataclass(frozen=True)
class AssetSpec:
    code: str
    name: str
    asset_type: str
    site_code: str
    commissioned_year: int | None = None
    actuatable: bool = False
    notes: str | None = None


ASSETS: list[AssetSpec] = [
    # Wet processing
    AssetSpec("RECV-SCALE", "Cherry receiving platform scale", "processing", "PROC", 2019),
    AssetSpec("PULPER-01", "Water-efficient eco-pulper", "processing", "PROC", 2018),
    AssetSpec("SETTLE-01", "Wastewater settling tank 1", "processing", "PROC", 2018),
    AssetSpec("SETTLE-02", "Wastewater settling tank 2", "processing", "PROC", 2018),
    AssetSpec("FERM-01", "Fermentation tank 1", "processing", "PROC", 2010),
    AssetSpec("FERM-02", "Fermentation tank 2", "processing", "PROC", 2010),
    AssetSpec("WASH-01", "Washing channel", "processing", "PROC", 2010),
    AssetSpec("HULLER-01", "Huller", "processing", "STORE", 2015),
    # Drying
    AssetSpec("BEDS-01", "Raised drying beds (24 beds)", "drying", "DRY", 2017),
    AssetSpec("POLY-01", "Parabolic polyhouse dryer", "drying", "DRY", 2023),
    AssetSpec("POLY-01-CURTAIN", "Polyhouse dryer curtain motor", "drying", "DRY", 2023,
              actuatable=True, notes="Closes when the rain sensor trips (Phase 8 actuation)"),
    # Composting & energy
    AssetSpec("BIOGAS-01", "Biogas digester", "energy", "COMPOST", 2020),
    AssetSpec("VERMI-01", "Vermicompost pit 1", "composting", "COMPOST", 2020),
    AssetSpec("VERMI-02", "Vermicompost pit 2", "composting", "COMPOST", 2020),
    AssetSpec("VERMI-03", "Vermicompost pit 3", "composting", "COMPOST", 2022),
    AssetSpec("VERMI-04", "Vermicompost pit 4", "composting", "COMPOST", 2022),
    AssetSpec("GENSET-01", "Diesel generator 15 kVA", "energy", "WORKSHOP", 2014),
    AssetSpec("SOLAR-01", "Rooftop solar array 10 kW", "energy", "STORE", 2024),
    # Irrigation (dry-season backup)
    AssetSpec("PUMP-01", "Reservoir irrigation pump", "irrigation", "RESERVOIR", 2016,
              actuatable=True),
    *(AssetSpec(f"VALVE-{b.code}", f"Drip/sprinkler zone valve {b.code}", "irrigation",
                b.code, 2016, actuatable=True) for b in BLOCKS),
    # Vehicles & tools
    AssetSpec("PICKUP-01", "4x4 pickup truck", "vehicle", "WORKSHOP", 2019),
    AssetSpec("TILLER-01", "Power tiller with trailer", "vehicle", "WORKSHOP", 2017),
    AssetSpec("BRUSH-01", "Brush cutter 1", "tool", "WORKSHOP", 2021),
    AssetSpec("BRUSH-02", "Brush cutter 2", "tool", "WORKSHOP", 2023),
    AssetSpec("SPRAY-01", "Backpack sprayer 1", "tool", "WORKSHOP", 2022),
    AssetSpec("SPRAY-02", "Backpack sprayer 2", "tool", "WORKSHOP", 2022),
    AssetSpec("SPRAY-03", "Backpack sprayer 3", "tool", "WORKSHOP", 2024),
    AssetSpec("CHAINSAW-01", "Chainsaw (shade lopping)", "tool", "WORKSHOP", 2020),
    # Lab
    AssetSpec("LAB-SOILKIT", "Soil NPK/pH test kit", "lab_equipment", "LAB", 2021),
    AssetSpec("LAB-MOIST", "Coffee moisture meter", "lab_equipment", "LAB", 2021),
    AssetSpec("LAB-SCALE", "Precision balance", "lab_equipment", "LAB", 2021),
    AssetSpec("LAB-MIXER", "Organic fertilizer blending drum", "lab_equipment", "LAB", 2022),
]  # fmt: skip

# --- Sensor stations & sensors -----------------------------------------------------

# measurement -> (unit, valid_min, valid_max)
MEASUREMENTS: dict[str, tuple[str, Decimal | None, Decimal | None]] = {
    "air_temperature": ("degC", D(-5), D(50)),
    "relative_humidity": ("%", D(0), D(100)),
    "rainfall": ("mm", D(0), D(300)),  # per reporting interval
    "wind_speed": ("m/s", D(0), D(60)),
    "solar_radiation": ("W/m2", D(0), D(1500)),
    "barometric_pressure": ("hPa", D(750), D(950)),  # ~1200-1300 m elevation
    "soil_moisture": ("m3/m3", D(0), D("0.6")),  # volumetric water content
    "soil_temperature": ("degC", D(0), D(45)),
    "leaf_wetness": ("%", D(0), D(100)),
    "rain_detected": ("bool", D(0), D(1)),
    "water_level": ("m", D(0), D(6)),
    "stream_flow": ("L/s", D(0), D(5000)),
    "motion_events": ("count", D(0), None),
}

FIELD_MEASUREMENTS = [
    "soil_moisture", "soil_temperature", "leaf_wetness", "air_temperature", "relative_humidity",
]  # fmt: skip


@dataclass(frozen=True)
class StationSpec:
    code: str
    name: str
    station_type: str
    site_code: str
    installed_on: date
    measurements: list[str]


STATIONS: list[StationSpec] = [
    StationSpec("WX-CORE", "Estate weather mast", "weather", "LAB", date(2025, 1, 15),
                ["air_temperature", "relative_humidity", "rainfall", "wind_speed",
                 "solar_radiation", "barometric_pressure"]),
    *(StationSpec(f"FS-{b.code}", f"Field station {b.code}", "field", b.code,
                  date(2025, 2, 1), FIELD_MEASUREMENTS) for b in BLOCKS),
    StationSpec("DRY-01", "Drying yard station", "process", "DRY", date(2025, 2, 1),
                ["rain_detected", "air_temperature", "relative_humidity"]),
    StationSpec("RES-01", "Reservoir level gauge", "water", "RESERVOIR", date(2025, 3, 1),
                ["water_level"]),
    StationSpec("STR2-01", "Stream 2 flow gauge", "water", "STREAM2", date(2025, 3, 1),
                ["stream_flow"]),
    *(StationSpec(f"CORR-{n:02d}", f"Corridor motion/IR sensor {n}", "corridor", "CORRIDOR",
                  date(2025, 4, 1), ["motion_events"]) for n in range(1, 5)),
]  # fmt: skip

# --- Livestock ---------------------------------------------------------------------


@dataclass(frozen=True)
class LivestockSpec:
    code: str
    name: str
    animal_type: str
    site_code: str
    head_count: int | None  # only for flock-tracked poultry; cattle/bees are individuals
    count_unit: str | None
    purpose: str


LIVESTOCK: list[LivestockSpec] = [
    LivestockSpec("COWS", "Cattle herd", "cattle", "COWSHED", None, None,
                  "Dung for biogas + vermicompost; milk for estate use"),
    LivestockSpec("HENS", "Free-range layer flock", "poultry", "COOP", 60, "birds",
                  "Ground-insect control near block edges; eggs for estate use"),
    LivestockSpec("HIVES", "Corridor beehive fence", "bees", "CORRIDOR", None, None,
                  "Elephant deterrent along the forest boundary; honey"),
]  # fmt: skip

# --- Inventory ---------------------------------------------------------------------


@dataclass(frozen=True)
class InventoryItemSpec:
    code: str
    name: str
    category: str
    unit: str
    site_code: str
    reorder_level: Decimal | None
    opening_balance: Decimal  # recorded as an opening_balance movement on OPENING_DATE


OPENING_DATE = date(2026, 11, 1)  # start of the 2026-27 harvest season

INVENTORY_ITEMS: list[InventoryItemSpec] = [
    InventoryItemSpec("VERMICOMPOST", "Vermicompost", "fertilizer", "kg", "COMPOST", D(500), D(4200)),
    InventoryItemSpec("BIOSLURRY", "Biogas slurry", "fertilizer", "L", "COMPOST", None, D(3000)),
    InventoryItemSpec("NPK-LEGACY", "NPK 17:17:17 (legacy, phasing out)", "fertilizer", "kg", "STORE", None, D(350)),
    InventoryItemSpec("ROCK-PHOS", "Rock phosphate", "fertilizer", "kg", "STORE", D(200), D(800)),
    InventoryItemSpec("DOLOMITE", "Dolomite lime", "fertilizer", "kg", "STORE", D(300), D(1500)),
    InventoryItemSpec("NEEM-OIL", "Neem oil botanical spray", "biocontrol", "L", "LAB", D(20), D(60)),
    InventoryItemSpec("BEAUVERIA", "Beauveria bassiana formulation", "biocontrol", "kg", "LAB", D(5), D(25)),
    InventoryItemSpec("CU-SULPHATE", "Copper sulphate (Bordeaux)", "biocontrol", "kg", "LAB", D(10), D(40)),
    InventoryItemSpec("LIME", "Hydrated lime (Bordeaux)", "biocontrol", "kg", "LAB", D(10), D(40)),
    InventoryItemSpec("CBB-LURE", "Coffee berry borer pheromone lure", "biocontrol", "pcs", "LAB", D(50), D(200)),
    InventoryItemSpec("STICKY-TRAP", "Yellow sticky trap", "biocontrol", "pcs", "LAB", D(50), D(300)),
    InventoryItemSpec("JUTE-BAG", "Jute bag, 60 kg", "packaging", "pcs", "STORE", D(100), D(600)),
    InventoryItemSpec("DIESEL", "Diesel", "fuel", "L", "WORKSHOP", D(100), D(400)),
    InventoryItemSpec("PETROL-2T", "Petrol 2T mix", "fuel", "L", "WORKSHOP", D(20), D(60)),
    InventoryItemSpec("PULPER-SCREEN", "Pulper screen (spare)", "spare_part", "pcs", "WORKSHOP", D(1), D(2)),
    InventoryItemSpec("DRIP-EMITTER", "Drip emitter (spare)", "spare_part", "pcs", "WORKSHOP", D(100), D(500)),
    InventoryItemSpec("SAP-AR", "Arabica sapling", "planting_material", "pcs", "NURSERY", D(200), D(1200)),
    InventoryItemSpec("SAP-RB", "Robusta sapling", "planting_material", "pcs", "NURSERY", D(200), D(1000)),
    InventoryItemSpec("CUT-PP", "Pepper rooted cutting", "planting_material", "pcs", "NURSERY", D(100), D(800)),
    InventoryItemSpec("SAP-AN", "Areca sapling", "planting_material", "pcs", "NURSERY", D(20), D(150)),
    InventoryItemSpec("SAP-NATIVE", "Native shade-tree sapling (mixed)", "planting_material", "pcs", "NURSERY", D(50), D(400)),
    InventoryItemSpec("CATTLE-FEED", "Cattle feed", "feed", "kg", "COWSHED", D(100), D(450)),
    InventoryItemSpec("CHICKEN-FEED", "Chicken feed", "feed", "kg", "COOP", D(50), D(180)),
]  # fmt: skip


# ===================================================================================
# Livestock detail (v1.3, 2026-11-24): individual cattle with pedigree and health,
# and individual hives with colonies, queens, nectar flows and honey harvests.
# History is seeded from when estate records "start" so durations are meaningful;
# from 1 Nov 2026 on, new events (milkings, harvests...) are the simulator's job.
# ===================================================================================

VET = "Visiting veterinary officer"
KEEPER = "Livestock keeper (W008)"
BEEKEEPER_CODE = "W008"  # the permanent livestock keeper also tends the hives

# --- Cattle ------------------------------------------------------------------------
# Mixed herd: native Malnad Gidda + Jersey crossbreds, one estate bull (Bhima, C01)
# siring every calf since he arrived. Calf of a Jersey-cross dam stays "jersey_cross".


@dataclass(frozen=True)
class CattleSpec:
    code: str
    name: str
    sex: str
    breed: str
    born_on: date
    origin: str
    acquired_on: date | None = None
    dam_code: str | None = None
    sire_code: str | None = None
    notes: str | None = None


MG, JX = "malnad_gidda", "jersey_cross"
CATTLE: list[CattleSpec] = [
    CattleSpec("C01", "Bhima", "male", MG, date(2020, 3, 10), "purchased", date(2021, 6, 15), notes="Estate breeding bull"),
    CattleSpec("C02", "Kaveri", "female", MG, date(2017, 5, 14), "born_on_estate", notes="Founder; parents not recorded"),
    CattleSpec("C03", "Ganga", "female", MG, date(2018, 2, 8), "born_on_estate", notes="Founder; parents not recorded"),
    CattleSpec("C04", "Tunga", "female", MG, date(2021, 9, 3), "born_on_estate", dam_code="C02", notes="Sire not recorded (before Bhima)"),
    CattleSpec("C05", "Lakshmi", "female", JX, date(2018, 11, 22), "purchased", date(2021, 2, 1)),
    CattleSpec("C06", "Gowri", "female", JX, date(2019, 8, 30), "purchased", date(2021, 2, 1)),
    CattleSpec("C07", "Nandini", "female", MG, date(2023, 10, 2), "born_on_estate", dam_code="C03", sire_code="C01"),
    CattleSpec("C08", "Hemavathi", "female", JX, date(2024, 1, 15), "born_on_estate", dam_code="C05", sire_code="C01"),
    CattleSpec("C09", "Chinna", "male", MG, date(2025, 11, 28), "born_on_estate", dam_code="C03", sire_code="C01"),
    CattleSpec("C10", "Shyama", "female", MG, date(2026, 6, 20), "born_on_estate", dam_code="C02", sire_code="C01"),
    CattleSpec("C11", "Basava", "male", MG, date(2026, 8, 5), "born_on_estate", dam_code="C04", sire_code="C01"),
    CattleSpec("C12", "Mallige", "female", JX, date(2026, 9, 12), "born_on_estate", dam_code="C06", sire_code="C01"),
]  # fmt: skip

GESTATION_DAYS = 283  # cattle average


@dataclass(frozen=True)
class HealthEventSpec:
    cattle_code: str
    occurred_on: date
    event_type: str
    subject: str | None = None
    treatment: str | None = None
    performed_by: str | None = None
    weight_kg: Decimal | None = None
    body_condition_score: Decimal | None = None
    temperature_c: Decimal | None = None
    next_due_on: date | None = None
    notes: str | None = None


# 1 Nov 2026 herd checkup: (weight kg, body condition score 1-5, temperature degC)
CHECKUP_2026_11 = {
    "C01": (280, "3.0", "38.6"), "C02": (175, "3.0", "38.5"), "C03": (168, "3.0", "38.4"),
    "C04": (160, "2.75", "38.7"), "C05": (340, "3.5", "38.5"), "C06": (325, "2.5", "38.6"),
    "C07": (140, "3.0", "38.6"), "C08": (230, "3.25", "38.5"), "C09": (95, "3.0", "38.8"),
    "C10": (55, "3.0", "38.9"), "C11": (48, "3.0", "39.0"), "C12": (52, "3.0", "38.9"),
}  # fmt: skip

# (vaccine, given on, next due, min age in days on the day)
VACCINATIONS = [
    ("Lumpy skin disease (LSD)", date(2026, 4, 15), date(2027, 4, 15), 120),
    ("Haemorrhagic septicaemia (HS)", date(2026, 5, 20), date(2027, 5, 20), 120),
    ("Foot-and-mouth disease (FMD)", date(2026, 9, 10), date(2027, 3, 10), 120),
]


def cattle_health_specs() -> list[HealthEventSpec]:
    by_code = {c.code: c for c in CATTLE}
    events: list[HealthEventSpec] = []
    for vaccine, given_on, due_on, min_age in VACCINATIONS:
        for c in CATTLE:
            if (given_on - c.born_on).days >= min_age:
                events.append(
                    HealthEventSpec(
                        c.code,
                        given_on,
                        "vaccination",
                        vaccine,
                        performed_by=VET,
                        next_due_on=due_on,
                    )
                )
    # Brucellosis: once in life, female calves at 4-8 months.
    for c in CATTLE:
        if c.sex == "female" and c.born_on >= date(2026, 1, 1):
            vax_on = date(2026, 11, 5)
            if 120 <= (vax_on - c.born_on).days <= 240:
                events.append(
                    HealthEventSpec(
                        c.code, vax_on, "vaccination", "Brucellosis (S19)", performed_by=VET
                    )
                )
    for c in CATTLE:  # whole-herd deworming, quarterly
        events.append(
            HealthEventSpec(
                c.code,
                date(2026, 10, 1),
                "deworming",
                "Albendazole",
                performed_by=KEEPER,
                next_due_on=date(2027, 1, 1),
            )
        )
    # Gowri's mastitis episode -- shows illness -> treatment -> follow-up.
    events += [
        HealthEventSpec(
            "C06",
            date(2026, 10, 18),
            "illness",
            "Clinical mastitis (left hind quarter)",
            performed_by=VET,
            temperature_c=D("39.6"),
            notes="Clots in milk, swollen quarter; milk discarded",
        ),
        HealthEventSpec(
            "C06",
            date(2026, 10, 18),
            "treatment",
            "Clinical mastitis (left hind quarter)",
            treatment="Intramammary antibiotic 3 days + anti-inflammatory; 5-day milk withdrawal",
            performed_by=VET,
            next_due_on=date(2026, 10, 25),
        ),
        HealthEventSpec(
            "C06",
            date(2026, 10, 25),
            "checkup",
            "Mastitis follow-up",
            performed_by=VET,
            temperature_c=D("38.6"),
            notes="Resolved",
        ),
    ]
    for code, (kg, bcs, temp) in CHECKUP_2026_11.items():
        assert code in by_code
        events.append(
            HealthEventSpec(
                code,
                date(2026, 11, 1),
                "checkup",
                "Monthly herd checkup",
                performed_by=KEEPER,
                weight_kg=D(kg),
                body_condition_score=D(bcs),
                temperature_c=D(temp),
            )
        )
    return events


@dataclass(frozen=True)
class BreedingEventSpec:
    cow_code: str
    occurred_on: date
    event_type: str
    sire_code: str | None = None
    calf_code: str | None = None
    pregnant: bool | None = None
    notes: str | None = None


def cattle_breeding_specs() -> list[BreedingEventSpec]:
    events: list[BreedingEventSpec] = []
    # Every recorded calf: mating (if sire known) -> positive pregnancy check -> calving.
    for calf in CATTLE:
        if calf.dam_code is None:
            continue
        if calf.sire_code:
            mated_on = calf.born_on - timedelta(days=GESTATION_DAYS)
            events.append(
                BreedingEventSpec(calf.dam_code, mated_on, "mating", sire_code=calf.sire_code)
            )
            events.append(
                BreedingEventSpec(
                    calf.dam_code, mated_on + timedelta(days=60), "pregnancy_check", pregnant=True
                )
            )
        events.append(
            BreedingEventSpec(calf.dam_code, calf.born_on, "calving", calf_code=calf.code)
        )
    # Where the herd stands now (late Nov 2026):
    events += [
        BreedingEventSpec("C05", date(2026, 3, 4), "heat"),
        BreedingEventSpec("C05", date(2026, 3, 5), "mating", sire_code="C01"),
        BreedingEventSpec("C05", date(2026, 5, 4), "pregnancy_check", pregnant=True),
        BreedingEventSpec("C05", date(2026, 10, 14), "dry_off", notes="Due mid-Dec 2026"),
        BreedingEventSpec("C03", date(2026, 9, 1), "dry_off", notes="End of lactation"),
        BreedingEventSpec("C03", date(2026, 9, 20), "mating", sire_code="C01"),
        BreedingEventSpec("C03", date(2026, 11, 19), "pregnancy_check", pregnant=True),
        BreedingEventSpec(
            "C07", date(2026, 7, 1), "mating", sire_code="C01", notes="First service"
        ),
        BreedingEventSpec("C07", date(2026, 8, 30), "pregnancy_check", pregnant=False),
        BreedingEventSpec("C07", date(2026, 11, 15), "heat"),
        BreedingEventSpec("C04", date(2026, 11, 10), "heat", notes="First heat after calving"),
    ]
    return sorted(events, key=lambda e: (e.occurred_on, e.cow_code))


# --- Bees --------------------------------------------------------------------------
# 24 Newton boxes of native Apis cerana indica along the corridor fence, 40 m apart.

HIVE_COUNT = 24
HIVE_SPACING_M = 40


@dataclass(frozen=True)
class HiveSpec:
    code: str
    fence_position_m: int
    box_type: str
    brood_frames: int
    super_count: int
    installed_on: date


@dataclass(frozen=True)
class ColonySpec:
    hive_code: str
    origin: str
    established_on: date
    ended_on: date | None = None
    end_reason: str | None = None


@dataclass(frozen=True)
class QueenSpec:
    hive_code: str
    colony_established_on: date  # identifies the colony within its hive
    origin: str
    introduced_on: date
    replaced_on: date | None = None


def _hive_code(n: int) -> str:
    return f"HV-{n:02d}"


def _installed_on(n: int) -> date:
    if n <= 12:
        return date(2022, 11, 15)  # first fence line
    if n <= 20:
        return date(2024, 1, 20)  # extension
    return date(2025, 9, 10)  # newest boxes, stocked with splits


def colony_specs() -> list[ColonySpec]:
    colonies = []
    for n in range(1, HIVE_COUNT + 1):
        code, installed = _hive_code(n), _installed_on(n)
        origin = "swarm_capture" if n <= 12 else "purchased" if n <= 20 else "split"
        if n == 5:  # absconded mid-2024, re-occupied by a captured swarm
            colonies += [
                ColonySpec(code, origin, installed, date(2024, 6, 12), "absconded"),
                ColonySpec(code, "swarm_capture", date(2024, 10, 3)),
            ]
        elif n == 17:  # absconded in the 2025 monsoon, re-stocked with a split
            colonies += [
                ColonySpec(code, origin, installed, date(2025, 7, 20), "absconded"),
                ColonySpec(code, "split", date(2026, 1, 15)),
            ]
        elif n == 22:  # lost to Thai sacbrood virus; box currently empty
            colonies.append(ColonySpec(code, origin, installed, date(2026, 7, 5), "collapsed"))
        else:
            colonies.append(ColonySpec(code, origin, installed))
    return colonies


def hive_specs() -> list[HiveSpec]:
    occupied = {c.hive_code for c in colony_specs() if c.ended_on is None}
    return [
        HiveSpec(_hive_code(n), n * HIVE_SPACING_M, "newton", 8,
                 1 if _hive_code(n) in occupied else 0, _installed_on(n))
        for n in range(1, HIVE_COUNT + 1)
    ]  # fmt: skip


def colony_alive_on(colony: ColonySpec, day: date) -> bool:
    return colony.established_on < day and (colony.ended_on is None or day < colony.ended_on)


def queen_specs() -> list[QueenSpec]:
    """Original queen per colony; older colonies superseded or were requeened."""
    queens = []
    for c in colony_specs():
        n = int(c.hive_code[3:])
        reign_start, origin = c.established_on, "original"
        changes: list[tuple[date, str]] = []
        if c.established_on < date(2024, 1, 1) and colony_alive_on(c, date(2024, 3, 1)):
            changes.append((date(2024, 3, 1 + n % 20), "supersedure"))
            if n % 2 == 0 and colony_alive_on(c, date(2026, 2, 1)):
                changes.append((date(2026, 2, 1 + n % 20), "requeened"))
        elif (
            c.established_on == date(2024, 1, 20)
            and n % 3 == 0
            and colony_alive_on(c, date(2025, 12, 1))
        ):
            changes.append((date(2025, 12, 1 + n % 20), "requeened"))
        for changed_on, new_origin in changes:
            queens.append(QueenSpec(c.hive_code, c.established_on, origin, reign_start, changed_on))
            reign_start, origin = changed_on, new_origin
        queens.append(QueenSpec(c.hive_code, c.established_on, origin, reign_start))
    return queens


@dataclass(frozen=True)
class NectarFlowSpec:
    code: str
    name: str
    flora: str
    starts_on: date
    ends_on: date


NECTAR_FLOWS: list[NectarFlowSpec] = [
    NectarFlowSpec("2025-POSTMON", "Post-monsoon forest flow", "Forest shrubs, climbers and Kadamba after the monsoon", date(2025, 10, 1), date(2025, 11, 30)),
    NectarFlowSpec("2026-COFFEE", "Coffee blossom flow", "Arabica/Robusta blossom after the blossom showers; silver oak", date(2026, 2, 20), date(2026, 4, 10)),
    NectarFlowSpec("2026-SUMMER", "Summer forest flow", "Honge (Pongamia), jamun, wild jack and forest trees", date(2026, 4, 11), date(2026, 5, 31)),
    NectarFlowSpec("2026-POSTMON", "Post-monsoon forest flow", "Forest shrubs, climbers and Kadamba after the monsoon", date(2026, 10, 1), date(2026, 11, 30)),
    NectarFlowSpec("2027-COFFEE", "Coffee blossom flow (forecast)", "Arabica/Robusta blossom after the blossom showers; silver oak", date(2027, 2, 20), date(2027, 4, 10)),
]  # fmt: skip

# flow -> (first harvest day, base honey kg per hive, frames extracted, base moisture %)
# Honey records start with the 2025 post-monsoon flow.
HARVEST_PLAN: dict[str, tuple[date, Decimal, int, Decimal]] = {
    "2025-POSTMON": (date(2025, 11, 20), D("1.2"), 3, D("22.5")),
    "2026-COFFEE": (date(2026, 3, 28), D("2.8"), 4, D("20.5")),
    "2026-SUMMER": (date(2026, 5, 22), D("1.8"), 3, D("20.0")),
    "2026-POSTMON": (date(2026, 11, 14), D("1.2"), 3, D("22.5")),
}
MIN_COLONY_AGE_FOR_HARVEST_DAYS = 60


@dataclass(frozen=True)
class HoneyHarvestSpec:
    hive_code: str
    flow_code: str
    harvested_on: date
    frames_extracted: int
    honey_kg: Decimal
    moisture_pct: Decimal
    harvested_by_code: str = BEEKEEPER_CODE


def honey_harvest_specs() -> list[HoneyHarvestSpec]:
    """One harvest per hive per flow, if a colony old enough was living in the box."""
    colonies = colony_specs()
    harvests = []
    for flow_code, (first_day, base_kg, frames, base_moisture) in HARVEST_PLAN.items():
        for n in range(1, HIVE_COUNT + 1):
            code = _hive_code(n)
            on = first_day + timedelta(days=n % 7)  # beekeeper works along the fence
            alive = any(
                c.hive_code == code
                and (on - c.established_on).days >= MIN_COLONY_AGE_FOR_HARVEST_DAYS
                and (c.ended_on is None or on < c.ended_on)
                for c in colonies
            )
            if alive:
                kg = (base_kg * (D("0.8") + D(n % 5) / 10)).quantize(D("0.01"))
                moisture = base_moisture + D(n % 4) / 2
                harvests.append(HoneyHarvestSpec(code, flow_code, on, frames, kg, moisture))
    return harvests
