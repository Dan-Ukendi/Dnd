"""Places and positions shared by the Organisation and Monde sections

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _article(name: str, ondelete: str = "SET NULL") -> sa.Column:
    return sa.Column(
        name, sa.String(36), sa.ForeignKey("wa_articles.wa_id", ondelete=ondelete), nullable=True
    )


def _common() -> list[sa.Column]:
    return [
        _article("source_article_id"),
        sa.Column("source_anchor", sa.String(300), server_default="", nullable=False),
        sa.Column("gm_notes", sa.Text(), server_default="", nullable=False),
        sa.Column("visibility", sa.String(10), server_default="mj", nullable=False),
        sa.Column("status", sa.String(10), server_default="a_valider", nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "places",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column(
            "parent_id",
            sa.Integer(),
            sa.ForeignKey("places.id", ondelete="SET NULL"),
            nullable=True,
        ),
        _article("wa_article_id"),
        _article("owner_id"),
        sa.Column("location", sa.String(300), server_default="", nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        *_common(),
        sa.UniqueConstraint("key", name="uq_places_key"),
        sa.UniqueConstraint("wa_article_id", name="uq_places_wa_article_id"),
        sa.CheckConstraint(
            "kind IN ('territoire', 'region', 'ville', 'quartier', 'batiment', 'terre', "
            "'niveau', 'piece', 'lieu')",
            name="ck_places_kind",
        ),
        sa.CheckConstraint("parent_id IS NULL OR parent_id <> id", name="ck_places_parent"),
        sa.CheckConstraint("visibility IN ('mj', 'joueurs')", name="ck_places_visibility"),
        sa.CheckConstraint("status IN ('etabli', 'a_valider')", name="ck_places_status"),
    )
    for column in ("parent_id", "owner_id", "source_article_id"):
        op.create_index(f"ix_places_{column}", "places", [column])

    op.create_table(
        "positions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(80), nullable=False),
        _article("organisation_id", ondelete="CASCADE"),
        sa.Column(
            "place_id", sa.Integer(), sa.ForeignKey("places.id", ondelete="CASCADE"), nullable=True
        ),
        _article("person_article_id"),
        sa.Column("name", sa.String(200), server_default="", nullable=False),
        sa.Column("title", sa.String(300), server_default="", nullable=False),
        sa.Column("scope", sa.String(200), server_default="", nullable=False),
        sa.Column(
            "superior_id",
            sa.Integer(),
            sa.ForeignKey("positions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "site_id", sa.Integer(), sa.ForeignKey("places.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("is_head", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("is_group", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("in_hierarchy", sa.Boolean(), server_default="true", nullable=False),
        *_common(),
        sa.UniqueConstraint("key", name="uq_positions_key"),
        sa.CheckConstraint(
            "num_nonnulls(organisation_id, place_id) = 1", name="ck_positions_attachment"
        ),
        sa.CheckConstraint(
            "superior_id IS NULL OR superior_id <> id", name="ck_positions_superior"
        ),
        sa.CheckConstraint("visibility IN ('mj', 'joueurs')", name="ck_positions_visibility"),
        sa.CheckConstraint("status IN ('etabli', 'a_valider')", name="ck_positions_status"),
    )
    for column in (
        "organisation_id",
        "place_id",
        "person_article_id",
        "superior_id",
        "site_id",
        "source_article_id",
    ):
        op.create_index(f"ix_positions_{column}", "positions", [column])


def downgrade() -> None:
    op.drop_table("positions")
    op.drop_table("places")
