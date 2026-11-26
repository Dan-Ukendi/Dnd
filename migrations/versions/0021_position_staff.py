"""Hierarchies: staff flag on positions, institution nodes removed

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-26
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

INSTITUTION_KEYS = ("clerge-conseil-des-prelats", "clerge-garde-blanche", "nuit-conseil")


def upgrade() -> None:
    op.add_column(
        "positions",
        sa.Column("is_staff", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    positions = sa.table("positions", sa.column("key", sa.String))
    result = op.get_bind().execute(positions.delete().where(positions.c.key.in_(INSTITUTION_KEYS)))
    logger.info("Removed %s institution positions from the hierarchies", result.rowcount)


def downgrade() -> None:
    op.drop_column("positions", "is_staff")
