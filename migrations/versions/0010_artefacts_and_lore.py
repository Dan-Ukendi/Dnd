"""Artefact characteristics and Lore entries, split from the World Anvil item articles

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-25
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.services.artefact_split import artefact_row, entry_rows, split_item_article

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

articles = sa.table(
    "wa_articles",
    sa.column("wa_id", sa.String),
    sa.column("title", sa.String),
    sa.column("template_type", sa.String),
    sa.column("content", sa.Text),
)
events = sa.table(
    "timeline_events",
    sa.column("id", sa.Integer),
    sa.column("code", sa.String),
    sa.column("removed_at", sa.DateTime),
)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def _text(name: str, length: int | None = None) -> sa.Column:
    kind = sa.String(length) if length else sa.Text()
    return sa.Column(name, kind, server_default="", nullable=False)


def upgrade() -> None:
    artefacts = op.create_table(
        "artefacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "wa_article_id",
            sa.String(36),
            sa.ForeignKey("wa_articles.wa_id", ondelete="CASCADE"),
            nullable=False,
        ),
        _text("tagline", 300),
        _text("object_type", 300),
        _text("other_names", 500),
        _text("rarity", 300),
        _text("attunement", 300),
        _text("properties"),
        _text("bonuses"),
        _text("charges", 100),
        _text("recharge", 200),
        _text("curse"),
        _text("holder", 200),
        _text("location", 300),
        _text("status", 200),
        _text("review_note"),
        *_timestamps(),
        sa.UniqueConstraint("wa_article_id", name="uq_artefacts_wa_article_id"),
    )
    entries = op.create_table(
        "lore_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("element_type", sa.String(20), nullable=False),
        _text("text"),
        sa.Column("start_year", sa.Integer(), nullable=True),
        sa.Column("end_year", sa.Integer(), nullable=True),
        sa.Column("era", sa.String(30), nullable=True),
        sa.Column("approximate", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "wa_article_id",
            sa.String(36),
            sa.ForeignKey("wa_articles.wa_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "timeline_event_id",
            sa.Integer(),
            sa.ForeignKey("timeline_events.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "source_article_id",
            sa.String(36),
            sa.ForeignKey("wa_articles.wa_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_section", sa.String(300), nullable=True),
        sa.Column("gm_only", sa.Boolean(), server_default="false", nullable=False),
        _text("review_note"),
        *_timestamps(),
        sa.CheckConstraint(
            "element_type IN ('artefact', 'lieu', 'personnage', 'organisation', 'divinite', "
            "'evenement')",
            name="ck_lore_entries_element_type",
        ),
        sa.CheckConstraint(
            "end_year IS NULL OR (start_year IS NOT NULL AND end_year >= start_year)",
            name="ck_lore_entries_years",
        ),
    )
    op.create_index("ix_lore_entries_wa_article_id", "lore_entries", ["wa_article_id"])
    op.create_index("ix_lore_entries_timeline_event_id", "lore_entries", ["timeline_event_id"])
    _fill(artefacts, entries)


def _fill(artefacts: sa.Table, entries: sa.Table) -> None:
    connection = op.get_bind()
    event_ids = {
        row.code: row.id
        for row in connection.execute(
            sa.select(events.c.code, events.c.id).where(events.c.removed_at.is_(None))
        )
    }
    items = connection.execute(
        sa.select(articles.c.wa_id, articles.c.title, articles.c.content)
        .where(articles.c.template_type == "item")
        .order_by(articles.c.title)
    ).all()
    for item in items:
        draft = split_item_article(item.wa_id, item.title, item.content)
        fiche = artefact_row(item.wa_id, draft)
        rows = entry_rows(item.wa_id, draft, event_ids)
        connection.execute(artefacts.insert().values(**fiche))
        if rows:
            connection.execute(entries.insert(), rows)
        logger.info("Split %s into a fiche and %d Lore entries", item.title, len(rows))


def downgrade() -> None:
    op.drop_index("ix_lore_entries_timeline_event_id", table_name="lore_entries")
    op.drop_index("ix_lore_entries_wa_article_id", table_name="lore_entries")
    op.drop_table("lore_entries")
    op.drop_table("artefacts")
