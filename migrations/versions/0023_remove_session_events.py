"""Notable events: drop the session-1 events (the journal holds them) and date Zilvra's backstory

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-26
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

KHAETAR = ("khaetar voss", "khaetar")
ZILVRA = ("zilvra vhaelor", "zilvra")

SESSION_EVENTS: dict[tuple[str, ...], tuple[str, ...]] = {
    KHAETAR: (
        "Dispute pour l'amulette au marché noir",
        "Refuge au Troll qui Rit",
        "Licence de rang B et contrat de Sophie",
        "À la Lux Victrix d'Arès",
        "Bob chassé de la taverne",
    ),
    ZILVRA: (
        "L'amulette disputée au marché noir",
        "L'accord au Troll qui Rit",
        "La bourse volée par Bob, puis reprise",
        "La licence de rang B",
        "Le coffre de Sophie",
        "Le contrat signé « Maelys »",
    ),
}

# Zilvra is 21 in An 829 (born about 808) and left the convent at about 18 (about 826).
ZILVRA_DATES: dict[str, tuple[int, int | None]] = {
    "Naissance dans la Maison Veldrynn": (808, None),
    "Le cercle de fidèles": (820, 12),
    "L'entrée au couvent de Lolth": (822, 14),
    "La rupture du serment": (826, 18),
    "Le départ du couvent et de la baronnie": (826, 18),
    "La remontée à la surface": (827, 19),
}

characters = sa.table(
    "characters",
    sa.column("id", sa.Integer),
    sa.column("name", sa.String),
    sa.column("main_article_id", sa.String),
)
articles = sa.table("wa_articles", sa.column("wa_id", sa.String), sa.column("title", sa.String))
events = sa.table(
    "character_events",
    sa.column("id", sa.Integer),
    sa.column("character_id", sa.Integer),
    sa.column("session", sa.String),
    sa.column("title", sa.String),
    sa.column("world_year", sa.Integer),
    sa.column("age", sa.Integer),
    sa.column("date_approximate", sa.Boolean),
)


def _character_ids(names: tuple[str, ...]) -> list[int]:
    by_article = sa.select(articles.c.wa_id).where(sa.func.lower(articles.c.title).in_(names))
    query = sa.select(characters.c.id).where(
        sa.or_(
            sa.func.lower(characters.c.name).in_(names),
            characters.c.main_article_id.in_(by_article),
        )
    )
    return list(op.get_bind().scalars(query))


def upgrade() -> None:
    connection = op.get_bind()
    for names, titles in SESSION_EVENTS.items():
        ids = _character_ids(names)
        if not ids:
            continue
        result = connection.execute(
            events.delete().where(
                events.c.character_id.in_(ids),
                events.c.title.in_(titles),
                events.c.session.is_not(None),
            )
        )
        logger.info("Removed %d session events for %s", result.rowcount or 0, names[0])
    ids = _character_ids(ZILVRA)
    dated = 0
    for title, (year, age) in ZILVRA_DATES.items():
        if not ids:
            break
        result = connection.execute(
            events.update()
            .where(
                events.c.character_id.in_(ids),
                events.c.title == title,
                events.c.world_year.is_(None),
                events.c.age.is_(None),
            )
            .values(world_year=year, age=age, date_approximate=True)
        )
        dated += result.rowcount or 0
    logger.info("Dated %d of Zilvra's events", dated)


def downgrade() -> None:
    connection = op.get_bind()
    ids = _character_ids(ZILVRA)
    for title, (year, age) in ZILVRA_DATES.items():
        if not ids:
            break
        connection.execute(
            events.update()
            .where(
                events.c.character_id.in_(ids),
                events.c.title == title,
                events.c.world_year == year,
                events.c.age.is_(None) if age is None else events.c.age == age,
                events.c.date_approximate.is_(True),
            )
            .values(world_year=None, age=None, date_approximate=False)
        )
    logger.info(
        "Session events are not restored: reimport a dossier backup with import-dossiers "
        "--replace-events to bring them back"
    )
