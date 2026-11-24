"""livestock groups: count only flock-tracked animals

Revision ID: d4660c7330ef
Revises: 8f7a93e1bf21
Create Date: 2026-11-24 12:18:04.917552

Cattle and bees are now tracked individually (`cattle`, `hives`), so a stored
head_count on their group would just drift out of sync. Only poultry, still tracked
as a flock, keeps one. This is a *data* migration as well as a schema one: the
existing group rows must be changed in step with the constraint, in both directions.
It sits on top of the cattle and bee revisions so that its downgrade can rebuild the
counts while both `cattle` and `hives` still exist.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4660c7330ef"
down_revision: str | Sequence[str] | None = "8f7a93e1bf21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column("livestock_groups", "head_count", existing_type=sa.INTEGER(), nullable=True)
    op.alter_column(
        "livestock_groups", "count_unit", existing_type=sa.VARCHAR(length=10), nullable=True
    )
    # Clear the stored counts first, or the new constraint would reject existing rows.
    op.execute(
        "UPDATE livestock_groups SET head_count = NULL, count_unit = NULL"
        " WHERE animal_type IN ('cattle', 'bees')"
    )
    # Hand-written: autogenerate can't detect a CHECK added to an existing table.
    op.create_check_constraint(
        "count_only_for_flock_tracked",
        "livestock_groups",
        "(animal_type = 'poultry') = (head_count IS NOT NULL)"
        " AND (head_count IS NULL) = (count_unit IS NULL)",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        op.f("ck_livestock_groups_count_only_for_flock_tracked"), "livestock_groups", type_="check"
    )
    # Rebuild the stored counts from the individual rows, so NOT NULL can come back.
    op.execute(
        "UPDATE livestock_groups g SET count_unit = 'head', head_count ="
        " (SELECT count(*) FROM cattle c WHERE c.group_id = g.id AND c.status = 'active')"
        " WHERE g.animal_type = 'cattle'"
    )
    op.execute(
        "UPDATE livestock_groups g SET count_unit = 'hives', head_count ="
        " (SELECT count(*) FROM hives h WHERE h.group_id = g.id AND h.retired_on IS NULL)"
        " WHERE g.animal_type = 'bees'"
    )
    op.alter_column(
        "livestock_groups", "count_unit", existing_type=sa.VARCHAR(length=10), nullable=False
    )
    op.alter_column("livestock_groups", "head_count", existing_type=sa.INTEGER(), nullable=False)
