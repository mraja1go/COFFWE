"""The COFFWE estate (docs/estate-concept.md) expressed as plain Python data.

This module has no database dependency on purpose: tests can check that the numbers
add up (70 acres, 44,950 tags, ...) without Postgres running, and the seed script
turns exactly this data into rows.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
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
    head_count: int
    count_unit: str
    purpose: str


LIVESTOCK: list[LivestockSpec] = [
    LivestockSpec("COWS", "Cow herd", "cattle", "COWSHED", 6, "head",
                  "Dung for biogas + vermicompost; milk for estate use"),
    LivestockSpec("HENS", "Free-range layer flock", "poultry", "COOP", 60, "birds",
                  "Ground-insect control near block edges; eggs for estate use"),
    LivestockSpec("HIVES", "Corridor beehive fence", "bees", "CORRIDOR", 24, "hives",
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
