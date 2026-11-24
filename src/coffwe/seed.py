"""Wipe and reload the estate's baseline data from coffwe.estate_data.

Run with:  uv run python -m coffwe.seed

Migrations own the *shape* of the database; this script owns the *contents*. It's
idempotent: every run truncates every estate table and rebuilds them identically.

Harvest batches/picks are left empty on purpose: they are events, and generating
events is the Phase 2 simulator's job. Inventory gets only its opening balances.
"""

from datetime import UTC, datetime, time

from sqlalchemy import func, insert, select, text
from sqlalchemy.orm import Session

from coffwe.db.base import Base, SessionLocal
from coffwe.db.models import (
    Asset,
    Block,
    InventoryItem,
    InventoryMovement,
    LivestockGroup,
    Plant,
    Sensor,
    SensorStation,
    Site,
    Species,
    Worker,
)
from coffwe.estate_data import (
    ASSETS,
    BLOCKS,
    INVENTORY_ITEMS,
    LIVESTOCK,
    MEASUREMENTS,
    OPENING_DATE,
    SITES,
    SPECIES,
    STATIONS,
    plant_specs,
    worker_specs,
)


def _ids(session: Session, model: type[Base]) -> dict[str, int]:
    """Map each row's business code to its database id, e.g. {"B1": 1, ...}."""
    return dict(session.execute(select(model.code, model.id)).all())


def seed(session: Session) -> None:
    # Every table, in one statement: Postgres works out the foreign-key order itself.
    # RESTART IDENTITY resets the id counters, so B1 is always id 1 after a reseed.
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY"))

    # --- Structure ---
    session.add_all(
        Species(
            code=s.code,
            common_name=s.common_name,
            scientific_name=s.scientific_name,
            category=s.category,
        )
        for s in SPECIES
    )
    session.add_all(
        Block(
            code=b.code,
            name=b.name,
            area_acres=b.area_acres,
            buffer_acres=b.buffer_acres,
            elevation_min_m=b.elevation_min_m,
            elevation_max_m=b.elevation_max_m,
            slope=b.slope,
            soil=b.soil,
            shade_pct=b.shade_pct,
            notes=b.notes,
        )
        for b in BLOCKS
    )
    session.flush()  # sends the INSERTs so Postgres assigns ids, without committing yet
    block_ids, species_ids = _ids(session, Block), _ids(session, Species)

    session.add_all(
        Site(
            code=s.code,
            name=s.name,
            kind=s.kind,
            description=s.description,
            block_id=block_ids[s.block_code] if s.block_code else None,
        )
        for s in SITES
    )

    # 44,950 rows: a bulk Core insert, not 44,950 ORM objects. SQLAlchemy batches
    # these into multi-row INSERT statements -- seconds instead of minutes.
    session.execute(
        insert(Plant),
        [
            {
                "tag": tag,
                "block_id": block_ids[block_code],
                "species_id": species_ids[species_code],
                "sequence": seq,
            }
            for block_code, species_code, seq, tag in plant_specs()
        ],
    )
    session.flush()
    site_ids = _ids(session, Site)

    # --- Resources ---
    session.add_all(
        Worker(
            code=w.code,
            full_name=w.full_name,
            employment_type=w.employment_type,
            role=w.role,
            housed_on_site=w.housed_on_site,
            hired_on=w.hired_on,
            contract_ends_on=w.contract_ends_on,
        )
        for w in worker_specs()
    )
    session.add_all(
        Asset(
            code=a.code,
            name=a.name,
            asset_type=a.asset_type,
            site_id=site_ids[a.site_code],
            actuatable=a.actuatable,
            commissioned_year=a.commissioned_year,
            notes=a.notes,
        )
        for a in ASSETS
    )
    for st in STATIONS:
        station = SensorStation(
            code=st.code,
            name=st.name,
            station_type=st.station_type,
            site_id=site_ids[st.site_code],
            installed_on=st.installed_on,
        )
        for m in st.measurements:
            unit, lo, hi = MEASUREMENTS[m]
            # Appending to the relationship sets station_id for us on flush.
            station.sensors.append(
                Sensor(code=f"{st.code}-{m}", measurement=m, unit=unit, valid_min=lo, valid_max=hi)
            )
        session.add(station)
    session.add_all(
        LivestockGroup(
            code=lv.code,
            name=lv.name,
            animal_type=lv.animal_type,
            site_id=site_ids[lv.site_code],
            head_count=lv.head_count,
            count_unit=lv.count_unit,
            purpose=lv.purpose,
        )
        for lv in LIVESTOCK
    )

    # --- Flows: inventory opening balances ---
    opened_at = datetime.combine(OPENING_DATE, time(6, 0), tzinfo=UTC)
    for spec in INVENTORY_ITEMS:
        item = InventoryItem(
            code=spec.code,
            name=spec.name,
            category=spec.category,
            unit=spec.unit,
            site_id=site_ids[spec.site_code],
            reorder_level=spec.reorder_level,
        )
        item.movements.append(
            InventoryMovement(
                quantity=spec.opening_balance, reason="opening_balance", occurred_at=opened_at
            )
        )
        session.add(item)

    session.commit()  # all-or-nothing: if anything above failed, nothing is half-loaded


def main() -> None:
    with SessionLocal() as session:
        seed(session)
        print("Seeded:")
        for table in Base.metadata.sorted_tables:
            n = session.scalar(select(func.count()).select_from(table))
            print(f"  {table.name:<20} {n:>7,}")


if __name__ == "__main__":
    main()
