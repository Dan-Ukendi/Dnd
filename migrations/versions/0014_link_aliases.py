"""Aliases and exclusions for the automatic links between fiches

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-25
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

articles = sa.table(
    "wa_articles",
    sa.column("wa_id", sa.String),
    sa.column("title", sa.String),
    sa.column("removed_at", sa.DateTime),
)

ALIASES = (
    ("Khaetar", articles.c.title == "Khaetar Voss"),
    ("Gronk", articles.c.title == "Gronk, fils de Brok"),
    ("Khüchniin Khöndii", articles.c.title.startswith("1. KHÜCHNIIN KHÖNDII", autoescape=True)),
)
EXCLUSIONS = ("La Nuit",)


def upgrade() -> None:
    aliases = op.create_table(
        "link_aliases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("alias", sa.String(200), nullable=False),
        sa.Column(
            "wa_article_id",
            sa.String(36),
            sa.ForeignKey("wa_articles.wa_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("wa_article_id", "alias", name="uq_link_aliases_article_alias"),
    )
    op.create_index("ix_link_aliases_wa_article_id", "link_aliases", ["wa_article_id"])
    exclusions = op.create_table(
        "link_exclusions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("term", sa.String(200), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("term", name="uq_link_exclusions_term"),
    )
    connection = op.get_bind()
    rows = []
    for alias, condition in ALIASES:
        matches = connection.execute(
            sa.select(articles.c.wa_id, articles.c.title).where(
                condition, articles.c.removed_at.is_(None)
            )
        ).all()
        if len(matches) != 1:
            logger.info("Alias %s not added: %d matching fiches", alias, len(matches))
            continue
        rows.append({"alias": alias, "wa_article_id": matches[0].wa_id})
        logger.info("Alias %s added for %s", alias, matches[0].title)
    if rows:
        op.bulk_insert(aliases, rows)
    op.bulk_insert(exclusions, [{"term": term} for term in EXCLUSIONS])


def downgrade() -> None:
    op.drop_table("link_exclusions")
    op.drop_index("ix_link_aliases_wa_article_id", table_name="link_aliases")
    op.drop_table("link_aliases")
