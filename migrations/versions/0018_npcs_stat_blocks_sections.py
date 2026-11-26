"""App-owned NPCs, generic roles, stat blocks and titled fiche sections

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VISIBILITIES = "visibility IN ('mj', 'joueurs')"


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    ]


def _text(name: str, length: int | None = None) -> sa.Column:
    kind = sa.String(length) if length else sa.Text()
    return sa.Column(name, kind, nullable=False, server_default="")


def _json(name: str) -> sa.Column:
    return sa.Column(name, postgresql.JSONB(), nullable=False, server_default="[]")


def _fk(name: str, target: str, ondelete: str, nullable: bool = True) -> sa.Column:
    kind = sa.String(36) if target.startswith("wa_articles") else sa.Integer()
    return sa.Column(name, kind, sa.ForeignKey(target, ondelete=ondelete), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "stat_blocks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.UniqueConstraint("key", name="uq_stat_blocks_key"),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        _text("size", 40),
        _text("creature_type", 200),
        _text("alignment", 200),
        sa.Column("armor_class", sa.Integer()),
        _text("armor_desc", 200),
        sa.Column("hit_points", sa.Integer()),
        _text("hit_dice", 60),
        _text("speed", 200),
        _json("abilities"),
        _text("saves", 300),
        _text("skills", 500),
        _text("vulnerabilities", 300),
        _text("resistances", 300),
        _text("immunities", 300),
        _text("condition_immunities", 300),
        _text("senses", 300),
        _text("languages", 300),
        _text("challenge", 20),
        _json("traits"),
        _json("actions"),
        _json("bonus_actions"),
        _json("reactions"),
        _text("source", 300),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        *_timestamps(),
    )
    op.create_table(
        "npcs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.UniqueConstraint("key", name="uq_npcs_key"),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        _text("title", 300),
        _text("gender", 60),
        _text("race", 100),
        _text("age", 60),
        _text("standing", 300),
        _text("description"),
        _text("gm_notes"),
        sa.Column("category", sa.String(10), nullable=False, server_default="lore"),
        _fk("stat_block_id", "stat_blocks.id", "SET NULL"),
        _fk("organisation_id", "wa_articles.wa_id", "SET NULL"),
        _fk("place_id", "places.id", "SET NULL"),
        _fk("wa_article_id", "wa_articles.wa_id", "SET NULL"),
        sa.Column("visibility", sa.String(10), nullable=False, server_default="mj"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        *_timestamps(),
        sa.CheckConstraint(
            "category IN ('lore', 'character', 'encounter')", name="ck_npcs_category"
        ),
        sa.CheckConstraint(VISIBILITIES, name="ck_npcs_visibility"),
        sa.UniqueConstraint("wa_article_id", name="uq_npcs_wa_article_id"),
    )
    op.create_table(
        "npc_roles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.UniqueConstraint("key", name="uq_npc_roles_key"),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        _text("description"),
        _text("headcount", 100),
        _text("gm_notes"),
        _fk("stat_block_id", "stat_blocks.id", "SET NULL"),
        _fk("organisation_id", "wa_articles.wa_id", "SET NULL"),
        _fk("place_id", "places.id", "SET NULL"),
        sa.Column("visibility", sa.String(10), nullable=False, server_default="mj"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        *_timestamps(),
        sa.CheckConstraint(VISIBILITIES, name="ck_npc_roles_visibility"),
    )
    op.create_table(
        "fiche_sections",
        sa.Column("id", sa.Integer(), primary_key=True),
        _fk("place_id", "places.id", "CASCADE"),
        _fk("npc_id", "npcs.id", "CASCADE"),
        _fk("npc_role_id", "npc_roles.id", "CASCADE"),
        sa.Column("title", sa.String(200), nullable=False),
        _text("text"),
        sa.Column("visibility", sa.String(10), nullable=False, server_default="joueurs"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint(
            "num_nonnulls(place_id, npc_id, npc_role_id) = 1", name="ck_fiche_sections_owner"
        ),
        sa.CheckConstraint(VISIBILITIES, name="ck_fiche_sections_visibility"),
    )
    op.create_table(
        "npc_relations",
        sa.Column("id", sa.Integer(), primary_key=True),
        _fk("npc_id", "npcs.id", "CASCADE", nullable=False),
        _fk("target_npc_id", "npcs.id", "CASCADE"),
        _fk("target_article_id", "wa_articles.wa_id", "CASCADE"),
        _text("label", 200),
        _text("reverse_label", 200),
        sa.Column("visibility", sa.String(10), nullable=False, server_default="mj"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint(
            "num_nonnulls(target_npc_id, target_article_id) = 1", name="ck_npc_relations_target"
        ),
        sa.CheckConstraint(VISIBILITIES, name="ck_npc_relations_visibility"),
    )
    for table, columns in (
        ("npcs", ("stat_block_id", "organisation_id", "place_id")),
        ("npc_roles", ("stat_block_id", "organisation_id", "place_id")),
        ("fiche_sections", ("place_id", "npc_id", "npc_role_id")),
        ("npc_relations", ("npc_id", "target_npc_id", "target_article_id")),
    ):
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])
    op.add_column(
        "positions",
        sa.Column("npc_id", sa.Integer(), sa.ForeignKey("npcs.id", ondelete="SET NULL")),
    )
    op.add_column(
        "positions",
        sa.Column("npc_role_id", sa.Integer(), sa.ForeignKey("npc_roles.id", ondelete="SET NULL")),
    )
    op.create_index("ix_positions_npc_id", "positions", ["npc_id"])
    op.create_index("ix_positions_npc_role_id", "positions", ["npc_role_id"])


def downgrade() -> None:
    op.drop_index("ix_positions_npc_role_id", table_name="positions")
    op.drop_index("ix_positions_npc_id", table_name="positions")
    op.drop_column("positions", "npc_role_id")
    op.drop_column("positions", "npc_id")
    op.drop_table("npc_relations")
    op.drop_table("fiche_sections")
    op.drop_table("npc_roles")
    op.drop_table("npcs")
    op.drop_table("stat_blocks")
