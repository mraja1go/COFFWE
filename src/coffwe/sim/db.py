"""Write a simulation run's events into the Phase 1 Postgres tables.

Only events on or after the run's events-start day are written: earlier days are the
seeded history (or simulator spin-up that the database should never see).

The simulator works with business codes ("B4", "AR", "W016", "DIESEL"); this module
is the one place that translates them into database ids.
"""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from coffwe.db.models import (
    BeeColony,
    Block,
    Cattle,
    CattleBreedingEvent,
    CattleHealthEvent,
    HarvestBatch,
    HarvestPick,
    Hive,
    HoneyHarvest,
    InventoryItem,
    InventoryMovement,
    LivestockGroup,
    Milking,
    NectarFlow,
    Queen,
    Species,
    Worker,
)
from coffwe.estate_data import BEEKEEPER_CODE
from coffwe.sim.climate import TIMEZONE
from coffwe.sim.harvest import Batch, Movement
from coffwe.sim.livestock import BeeModel, CattleModel

IST = ZoneInfo(TIMEZONE)
MOVEMENT_TIME = time(17, 0)  # the stores clerk writes up the day's ledger at 5 pm


def _codes(session: Session, model) -> dict[str, int]:
    return dict(session.execute(select(model.code, model.id)).all())


def write_events(
    session: Session, batches: list[Batch], movements: list[Movement], events_start: date
) -> dict[str, int]:
    blocks, species = _codes(session, Block), _codes(session, Species)
    workers, items = _codes(session, Worker), _codes(session, InventoryItem)

    batches = [b for b in batches if b.harvested_on >= events_start]
    batch_rows = [
        {
            "code": b.code,
            "block_id": blocks[b.block_code],
            "species_id": species[b.variety],
            "harvested_on": b.harvested_on,
            "cherry_kg": b.cherry_kg,
            "processing_method": b.method,
            "status": b.status,
            "output_kg": b.output_kg,
            "notes": f"over-ripe share {b.overripe_kg / b.field_kg:.0%}" if b.field_kg else None,
        }
        for b in batches
    ]
    if batch_rows:
        # RETURNING hands back the new ids, so picks can point at their batch.
        ids = session.execute(
            insert(HarvestBatch).returning(HarvestBatch.code, HarvestBatch.id), batch_rows
        ).all()
        batch_ids = dict(ids)
        pick_rows = [
            {"batch_id": batch_ids[b.code], "worker_id": workers[w], "cherry_kg": kg}
            for b in batches
            for w, kg in b.picks
        ]
        session.execute(insert(HarvestPick), pick_rows)
    else:
        pick_rows = []

    movement_rows = [
        {
            "item_id": items[m.item_code],
            "quantity": m.quantity,
            "reason": m.reason,
            "occurred_at": datetime.combine(m.day, MOVEMENT_TIME, tzinfo=IST),
            "block_id": blocks[m.block_code] if m.block_code else None,
            "notes": m.notes,
        }
        for m in movements
        if m.day >= events_start
    ]
    if movement_rows:
        session.execute(insert(InventoryMovement), movement_rows)
    return {
        "harvest_batches": len(batch_rows),
        "harvest_picks": len(pick_rows),
        "inventory_movements": len(movement_rows),
    }


def write_livestock(session: Session, cattle: CattleModel, bees: BeeModel) -> dict[str, int]:
    """Herd and apiary events. New calves and colonies are inserted; seeded colonies and
    queens that ended during the run are *updated* (closed off), not duplicated."""
    herd_group = session.scalar(select(LivestockGroup.id).where(LivestockGroup.code == "COWS"))
    keeper = session.scalar(select(Worker.id).where(Worker.code == BEEKEEPER_CODE))

    # --- calves born in the run (parents before offspring, so ids exist in order)
    calves = sorted((a for a in cattle.herd.values() if a.is_new), key=lambda a: a.born_on)
    animal_ids = _codes(session, Cattle)
    for a in calves:
        animal_ids[a.code] = session.scalar(
            insert(Cattle)
            .values(
                code=a.code,
                name=a.name,
                sex=a.sex,
                breed=a.breed,
                born_on=a.born_on,
                origin="born_on_estate",
                dam_id=animal_ids[a.dam_code],
                sire_id=animal_ids[a.sire_code],
                group_id=herd_group,
            )
            .returning(Cattle.id)
        )

    health_rows = [
        {
            "cattle_id": animal_ids[h["cattle_code"]],
            "occurred_on": h["day"],
            **{k: v for k, v in h.items() if k not in ("cattle_code", "day")},
        }
        for h in cattle.health
    ]
    breeding_rows = [
        {
            "cow_id": animal_ids[b["cow_code"]],
            "occurred_on": b["day"],
            "event_type": b["event_type"],
            "sire_id": animal_ids.get(b["sire_code"]),
            "calf_id": animal_ids.get(b["calf_code"]),
            "pregnant": b["pregnant"],
            "notes": b["notes"],
        }
        for b in cattle.breeding
    ]
    milking_rows = [
        {
            "cattle_id": animal_ids[m["cattle_code"]],
            "milked_on": m["day"],
            "session": m["session"],
            "litres": m["litres"],
            "fat_pct": m["fat_pct"],
            "milked_by_id": keeper,
        }
        for m in cattle.milkings
    ]
    for model, rows in (
        (CattleHealthEvent, health_rows),
        (CattleBreedingEvent, breeding_rows),
        (Milking, milking_rows),
    ):
        if rows:
            session.execute(insert(model), rows)

    # --- apiary
    hive_ids, flow_ids = _codes(session, Hive), _codes(session, NectarFlow)
    harvest_rows = [
        {
            "hive_id": hive_ids[h["hive_code"]],
            "flow_id": flow_ids.get(h["flow_code"]),
            "harvested_on": h["day"],
            "frames_extracted": h["frames_extracted"],
            "honey_kg": h["honey_kg"],
            "moisture_pct": h["moisture_pct"],
            "harvested_by_id": keeper,
        }
        for h in bees.harvests
    ]
    if harvest_rows:
        session.execute(insert(HoneyHarvest), harvest_rows)

    new_colonies = ended_colonies = new_queens = 0
    for c in bees.colonies:
        hive_id = hive_ids[c.hive_code]
        if c.is_new:
            colony_id = session.scalar(
                insert(BeeColony)
                .values(
                    hive_id=hive_id,
                    origin=c.origin,
                    established_on=c.established_on,
                    ended_on=c.ended_on,
                    end_reason=c.end_reason,
                )
                .returning(BeeColony.id)
            )
            new_colonies += 1
        else:
            colony_id = session.scalar(
                select(BeeColony.id).where(
                    BeeColony.hive_id == hive_id, BeeColony.established_on == c.established_on
                )
            )
            if c.ended_on:
                session.execute(
                    update(BeeColony)
                    .where(BeeColony.id == colony_id)
                    .values(ended_on=c.ended_on, end_reason=c.end_reason)
                )
                ended_colonies += 1
        # Queen reigns during the run: close the seeded queen's reign, add the new ones.
        reigns = [*c.old_queens, (c.queen_origin, c.queen_since, None)]
        for origin, since, until in reigns:
            existing = session.scalar(
                select(Queen.id).where(Queen.colony_id == colony_id, Queen.introduced_on == since)
            )
            if existing:
                if until:
                    session.execute(
                        update(Queen).where(Queen.id == existing).values(replaced_on=until)
                    )
            else:
                session.execute(
                    insert(Queen).values(
                        colony_id=colony_id, origin=origin, introduced_on=since, replaced_on=until
                    )
                )
                new_queens += 1
        occupied = c.ended_on is None
        session.execute(update(Hive).where(Hive.id == hive_id).values(super_count=int(occupied)))
    # Boxes whose only living colony ended during the run carry no super.
    living = {c.hive_code for c in bees.living()}
    for code, hid in hive_ids.items():
        if code not in living:
            session.execute(update(Hive).where(Hive.id == hid).values(super_count=0))

    return {
        "new_calves": len(calves),
        "cattle_health_events": len(health_rows),
        "cattle_breeding_events": len(breeding_rows),
        "milkings": len(milking_rows),
        "honey_harvests": len(harvest_rows),
        "new_colonies": new_colonies,
        "ended_colonies": ended_colonies,
        "new_queens": new_queens,
    }
