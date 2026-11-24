"""Relation and item GM notes, character aliases, relations edited in the app

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "character_profiles",
        sa.Column("aliases", postgresql.JSONB(), server_default="[]", nullable=False),
    )
    op.add_column(
        "character_profiles",
        sa.Column("relations_edited_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "character_relations",
        sa.Column("gm_notes", sa.Text(), server_default="", nullable=False),
    )
    op.add_column(
        "character_items",
        sa.Column("gm_notes", sa.Text(), server_default="", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("character_items", "gm_notes")
    op.drop_column("character_relations", "gm_notes")
    op.drop_column("character_profiles", "relations_edited_at")
    op.drop_column("character_profiles", "aliases")
