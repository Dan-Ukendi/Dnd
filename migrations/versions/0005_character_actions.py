"""Character combat actions

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "character_profiles",
        sa.Column("combat", postgresql.JSONB(), server_default="{}", nullable=False),
    )
    op.create_table(
        "character_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "character_id",
            sa.Integer(),
            sa.ForeignKey("characters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("hit", sa.String(60), nullable=True),
        sa.Column("damage", sa.String(200), nullable=True),
        sa.Column("save", sa.String(60), nullable=True),
        sa.Column("range", sa.String(100), nullable=True),
        sa.Column("uses", sa.String(100), nullable=True),
        sa.Column("concentration", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("text", sa.Text(), server_default="", nullable=False),
        sa.CheckConstraint(
            "kind IN ('action', 'bonus', 'reaction', 'free')", name="ck_character_actions_kind"
        ),
    )
    op.create_index("ix_character_actions_character_id", "character_actions", ["character_id"])


def downgrade() -> None:
    op.drop_index("ix_character_actions_character_id", table_name="character_actions")
    op.drop_table("character_actions")
    op.drop_column("character_profiles", "combat")
