"""Buildings: app-owned building type and household roles on positions

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-26
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

HOUSEHOLDS = {
    "demeure-sophie-proprietaire": "owner",
    "demeure-sophie-gerard": "employee",
    "demeure-sophie-domestiques": "employee",
}


def upgrade() -> None:
    op.create_table(
        "building_types",
        sa.Column(
            "wa_id",
            sa.String(36),
            sa.ForeignKey("wa_articles.wa_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("building_type", sa.String(10), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "building_type IN ('branch', 'dwelling')", name="ck_building_types_building_type"
        ),
    )
    op.add_column(
        "positions",
        sa.Column("household", sa.String(10), nullable=False, server_default=""),
    )
    op.create_check_constraint(
        "ck_positions_household",
        "positions",
        "household IN ('', 'owner', 'resident', 'employee')",
    )
    positions = sa.table("positions", sa.column("key", sa.String), sa.column("household"))
    bind = op.get_bind()
    updated = 0
    for key, household in HOUSEHOLDS.items():
        result = bind.execute(
            positions.update().where(positions.c.key == key).values(household=household)
        )
        updated += result.rowcount or 0
    logger.info("Set the household role of %s positions", updated)


def downgrade() -> None:
    op.drop_constraint("ck_positions_household", "positions", type_="check")
    op.drop_column("positions", "household")
    op.drop_table("building_types")
