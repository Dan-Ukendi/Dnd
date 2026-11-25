"""Categories of the Personnage fiches: lore, character, encounter

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-25
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.sections import section_key

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

PERSON_SECTION = "personnage"

articles = sa.table(
    "wa_articles",
    sa.column("wa_id", sa.String),
    sa.column("title", sa.String),
    sa.column("template_type", sa.String),
)
characters = sa.table(
    "characters",
    sa.column("player_id", sa.Integer),
    sa.column("main_article_id", sa.String),
)
users = sa.table("users", sa.column("id", sa.Integer), sa.column("is_active", sa.Boolean))


def upgrade() -> None:
    categories = op.create_table(
        "article_categories",
        sa.Column(
            "wa_id",
            sa.String(36),
            sa.ForeignKey("wa_articles.wa_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("category", sa.String(10), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "category IN ('lore', 'character', 'encounter')",
            name="ck_article_categories_category",
        ),
    )
    _classify(categories)


def _classify(categories: sa.Table) -> None:
    connection = op.get_bind()
    played = set(
        connection.scalars(
            sa.select(characters.c.main_article_id)
            .join(users, users.c.id == characters.c.player_id)
            .where(characters.c.main_article_id.is_not(None), users.c.is_active.is_(True))
        )
    )
    rows = []
    for article in connection.execute(
        sa.select(articles.c.wa_id, articles.c.title, articles.c.template_type).order_by(
            articles.c.title
        )
    ):
        if section_key(article.wa_id, article.template_type) != PERSON_SECTION:
            continue
        category = "character" if article.wa_id in played else "encounter"
        rows.append({"wa_id": article.wa_id, "category": category})
        logger.info("Classified %s as %s", article.title, category)
    if rows:
        connection.execute(categories.insert(), rows)


def downgrade() -> None:
    op.drop_table("article_categories")
