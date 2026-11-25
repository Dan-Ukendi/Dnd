"""Remove the invented chronology events E-001 to E-053

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REMOVED_CODES = [f"E-{number:03d}" for number in range(1, 54)]
CORRECTIONS = {
    "B3": {"place": "Tout l'empire"},
    "B8": {
        "summary": "L'empire se partage entre le Khaanat des Cinq Lances, "
        "l'Héliopolis des Harmoniens et le Royaume de Sanguis Coronae."
    },
    "C15": {"summary": "Naissance de Khaetar, tiefling orphelin."},
}

events = sa.table(
    "timeline_events",
    sa.column("id", sa.Integer),
    sa.column("code", sa.String),
    sa.column("place", sa.String),
    sa.column("summary", sa.Text),
    sa.column("modified_by_dm", sa.Boolean),
    sa.column("updated_at", sa.DateTime),
)
links = sa.table(
    "timeline_event_links",
    sa.column("source_id", sa.Integer),
    sa.column("target_id", sa.Integer),
    sa.column("origin", sa.String),
)
fiches = sa.table("timeline_event_fiches", sa.column("event_id", sa.Integer))


def _event_id(code: str) -> sa.ScalarSelect:
    return sa.select(events.c.id).where(events.c.code == code).scalar_subquery()


def upgrade() -> None:
    removable = events.c.code.in_(REMOVED_CODES) & events.c.modified_by_dm.is_(False)
    targets = sa.select(events.c.id).where(removable)
    op.execute(
        sa.delete(links).where(links.c.source_id.in_(targets) | links.c.target_id.in_(targets))
    )
    op.execute(sa.delete(fiches).where(fiches.c.event_id.in_(targets)))
    op.execute(sa.delete(events).where(removable))
    for code, values in CORRECTIONS.items():
        op.execute(
            sa.update(events)
            .where(events.c.code == code, events.c.modified_by_dm.is_(False))
            .values(**values, updated_at=sa.func.now())
        )
    source, target = _event_id("B8"), _event_id("B7")
    op.execute(
        postgresql.insert(links)
        .from_select(
            ["source_id", "target_id", "origin"],
            sa.select(source, target, sa.literal("wa")).where(
                source.is_not(None), target.is_not(None)
            ),
        )
        .on_conflict_do_nothing()
    )


def downgrade() -> None:
    pass
