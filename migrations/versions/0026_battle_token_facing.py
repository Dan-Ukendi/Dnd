"""Facing of battle tokens, in eighths of a turn clockwise from north

Revision ID: 0026
Revises: 0025
"""

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("battle_tokens", sa.Column("facing", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_battle_tokens_facing",
        "battle_tokens",
        "facing IS NULL OR (facing >= 0 AND facing < 8)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_battle_tokens_facing", "battle_tokens", type_="check")
    op.drop_column("battle_tokens", "facing")
