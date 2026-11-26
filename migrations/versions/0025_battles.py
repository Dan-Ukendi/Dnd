"""Battle maps: one map image per battle and the grid cell of each token

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "battles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("cols", sa.Integer(), nullable=False),
        sa.Column("rows", sa.Integer(), nullable=False),
        sa.Column("image", sa.LargeBinary(), nullable=True),
        sa.Column("image_type", sa.String(50), nullable=True),
        sa.Column("image_sha256", sa.String(64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("cols > 0 AND rows > 0", name="ck_battles_grid"),
    )
    op.create_table(
        "battle_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "battle_id",
            sa.Integer(),
            sa.ForeignKey("battles.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("label", sa.String(60), nullable=False),
        sa.Column("color", sa.String(7), nullable=False),
        sa.Column("size", sa.String(10), server_default="medium", nullable=False),
        sa.Column(
            "character_id",
            sa.Integer(),
            sa.ForeignKey("characters.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("col", sa.Integer(), nullable=True),
        sa.Column("row", sa.Integer(), nullable=True),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint("size IN ('small', 'medium', 'large')", name="ck_battle_tokens_size"),
        sa.CheckConstraint("(col IS NULL) = (row IS NULL)", name="ck_battle_tokens_cell"),
        sa.CheckConstraint("col IS NULL OR (col >= 0 AND row >= 0)", name="ck_battle_tokens_range"),
        sa.UniqueConstraint("battle_id", "label", name="uq_battle_tokens_label"),
    )


def downgrade() -> None:
    op.drop_table("battle_tokens")
    op.drop_table("battles")
