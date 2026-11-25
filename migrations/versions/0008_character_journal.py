"""Character adventure journal entries

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "character_journal_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "character_id",
            sa.Integer(),
            sa.ForeignKey("characters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("episode", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("days", sa.String(100), nullable=True),
        sa.Column("text", sa.Text(), server_default="", nullable=False),
        sa.Column("lists", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.UniqueConstraint("character_id", "episode", name="uq_character_journal_entries_episode"),
        sa.CheckConstraint("episode > 0", name="ck_character_journal_entries_episode"),
    )
    op.create_index(
        "ix_character_journal_entries_character_id",
        "character_journal_entries",
        ["character_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_character_journal_entries_character_id", table_name="character_journal_entries"
    )
    op.drop_table("character_journal_entries")
