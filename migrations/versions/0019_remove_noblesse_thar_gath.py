"""Remove the Noblesse Thar-Gath fiche deleted from World Anvil

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

THAR_GATH = "0fcf5a36-a03a-455f-ab12-0c843d2a6543"

articles = sa.table(
    "wa_articles", sa.column("wa_id", sa.String), sa.column("linked_ids", sa.ARRAY(sa.String))
)


def upgrade() -> None:
    op.execute(
        articles.update()
        .where(sa.literal(THAR_GATH) == sa.any_(articles.c.linked_ids))
        .values(linked_ids=sa.func.array_remove(articles.c.linked_ids, THAR_GATH))
    )
    op.execute(articles.delete().where(articles.c.wa_id == THAR_GATH))


def downgrade() -> None:
    pass
