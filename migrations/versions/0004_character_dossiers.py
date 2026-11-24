"""Character dossiers

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _character_fk() -> sa.Column:
    return sa.Column(
        "character_id",
        sa.Integer(),
        sa.ForeignKey("characters.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "character_profiles",
        sa.Column(
            "character_id",
            sa.Integer(),
            sa.ForeignKey("characters.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("backstory", sa.Text(), server_default="", nullable=False),
        sa.Column("source_file", sa.String(255), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "character_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        _character_fk(),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("session", sa.String(100), nullable=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("text", sa.Text(), server_default="", nullable=False),
    )
    op.create_index("ix_character_events_character_id", "character_events", ["character_id"])
    op.create_table(
        "character_relations",
        sa.Column("id", sa.Integer(), primary_key=True),
        _character_fk(),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("role", sa.String(200), nullable=True),
        sa.Column("status", sa.String(100), nullable=True),
        sa.Column(
            "wa_article_id",
            sa.String(36),
            sa.ForeignKey("wa_articles.wa_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("text", sa.Text(), server_default="", nullable=False),
    )
    op.create_index("ix_character_relations_character_id", "character_relations", ["character_id"])
    op.create_table(
        "character_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        _character_fk(),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("quantity", sa.Integer(), server_default="1", nullable=False),
        sa.Column("category", sa.String(60), nullable=True),
        sa.Column("equipped", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "wa_article_id",
            sa.String(36),
            sa.ForeignKey("wa_articles.wa_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("text", sa.Text(), server_default="", nullable=False),
        sa.CheckConstraint("quantity >= 0", name="ck_character_items_quantity"),
    )
    op.create_index("ix_character_items_character_id", "character_items", ["character_id"])


def downgrade() -> None:
    op.drop_index("ix_character_items_character_id", table_name="character_items")
    op.drop_table("character_items")
    op.drop_index("ix_character_relations_character_id", table_name="character_relations")
    op.drop_table("character_relations")
    op.drop_index("ix_character_events_character_id", table_name="character_events")
    op.drop_table("character_events")
    op.drop_table("character_profiles")
