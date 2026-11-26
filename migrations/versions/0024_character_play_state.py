"""Per-character play state: hit points, hit dice, spell slots and limited resources used

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "character_play_states",
        sa.Column(
            "character_id",
            sa.Integer(),
            sa.ForeignKey("characters.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("current_hp", sa.Integer(), nullable=True),
        sa.Column("temp_hp", sa.Integer(), server_default="0", nullable=False),
        sa.Column("hit_dice_used", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "slots_used", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column(
            "resources_used",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "current_hp IS NULL OR current_hp >= 0", name="ck_character_play_states_hp"
        ),
        sa.CheckConstraint("temp_hp >= 0", name="ck_character_play_states_temp_hp"),
        sa.CheckConstraint("hit_dice_used >= 0", name="ck_character_play_states_hit_dice"),
    )


def downgrade() -> None:
    op.drop_table("character_play_states")
