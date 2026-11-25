"""Lore split into legends, artefacts and books; artefact fiches keep their sections

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-25
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

JOUG_ID = "dcdedb4d-686e-483d-a35d-137823b77eb1"
SPECULUM_ID = "9a0ff719-7202-4be9-a52c-997e5af92751"
HELIOPOLIS_ID = "ea2b7304-a3e1-41ac-aa67-0ab4462a18fa"
FEUILLETS_ID = "b347541d-92e6-40ed-92e6-c6fdc691389b"

DEFAULT_TYPES = ("Mythe", "Conte", "Légende", "Histoire")
JOUG_PARTS = (
    "Le sorcier",
    "La forge",
    "Le règne du Dompteur",
    "La chute",
    "Le long sommeil",
    "La réapparition",
    "Légendes et rumeurs",
)
PHYSICAL = frozenset({"Description"})
CHARACTERISTICS = frozenset(
    {
        "Origine",
        "Fonctionnement",
        "Les degrés de la charge",
        "Pouvoirs",
        "Le prix du pouvoir",
        "La restauration",
    }
)
DM_NOTES = frozenset(
    {
        "Secrets",
        "Pistes pour la suite",
        "Ceux qui la convoitent",
        "L'amulette de Khaetar",
        "Le patron démoniaque",
        "Fiche rapide (origine)",
        "Événements de la session 1",
    }
)
EMPIRE_PARTS = (
    ("L'Empire d'Ébène et les Doublés", "B1"),
    ("Le Cataclysme : La Rébellion des Échos", "B7"),
)
FEUILLETS_PARTS = (
    "Avertissement du copiste",
    "Fragment I : le Dompteur",
    "Fragment II : la forge",
    "Fragment III : les mots en cercle",
    "Fragment IV : la chute",
    "Fragment V : le long silence",
    "On dit aussi",
    "Questions notées en marge",
)
articles = sa.table(
    "wa_articles",
    sa.column("wa_id", sa.String),
    sa.column("title", sa.String),
    sa.column("removed_at", sa.DateTime),
)
events = sa.table(
    "timeline_events",
    sa.column("id", sa.Integer),
    sa.column("code", sa.String),
    sa.column("removed_at", sa.DateTime),
)
artefacts_table = sa.table(
    "artefacts", sa.column("id", sa.Integer), sa.column("wa_article_id", sa.String)
)
entries = sa.table(
    "lore_entries",
    sa.column("id", sa.Integer),
    sa.column("title", sa.String),
    sa.column("element_type", sa.String),
    sa.column("text", sa.Text),
    sa.column("position", sa.Integer),
    sa.column("wa_article_id", sa.String),
    sa.column("timeline_event_id", sa.Integer),
    sa.column("source_article_id", sa.String),
    sa.column("source_section", sa.String),
    sa.column("story_id", sa.Integer),
    sa.column("placement", sa.String),
    sa.column("gm_only", sa.Boolean),
    sa.column("review_note", sa.Text),
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


def upgrade() -> None:
    types = op.create_table(
        "lore_types",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("label", sa.String(60), nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("label", name="uq_lore_types_label"),
    )
    groups = op.create_table(
        "lore_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("title", name="uq_lore_groups_title"),
    )
    stories = op.create_table(
        "lore_stories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("intro_sections", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "type_id",
            sa.Integer(),
            sa.ForeignKey("lore_types.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "group_id",
            sa.Integer(),
            sa.ForeignKey("lore_groups.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("start_year", sa.Integer(), nullable=True),
        sa.Column("end_year", sa.Integer(), nullable=True),
        sa.Column("era", sa.String(30), nullable=True),
        sa.Column("approximate", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "wa_article_id",
            sa.String(36),
            sa.ForeignKey("wa_articles.wa_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "artefact_id",
            sa.Integer(),
            sa.ForeignKey("artefacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "legend_id",
            sa.Integer(),
            sa.ForeignKey("lore_stories.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("gm_only", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("review_note", sa.Text(), server_default="", nullable=False),
        *_timestamps(),
        sa.CheckConstraint("kind IN ('legende', 'ouvrage')", name="ck_lore_stories_kind"),
        sa.CheckConstraint(
            "end_year IS NULL OR (start_year IS NOT NULL AND end_year >= start_year)",
            name="ck_lore_stories_years",
        ),
    )
    for column in ("type_id", "group_id", "wa_article_id", "artefact_id", "legend_id"):
        op.create_index(f"ix_lore_stories_{column}", "lore_stories", [column])
    op.add_column(
        "lore_entries",
        sa.Column(
            "story_id",
            sa.Integer(),
            sa.ForeignKey(
                "lore_stories.id", ondelete="CASCADE", name="fk_lore_entries_story_id_lore_stories"
            ),
            nullable=True,
        ),
    )
    op.create_index("ix_lore_entries_story_id", "lore_entries", ["story_id"])
    op.add_column(
        "lore_entries",
        sa.Column("placement", sa.String(20), server_default="part", nullable=False),
    )
    op.create_check_constraint(
        "ck_lore_entries_placement",
        "lore_entries",
        "placement IN ('part', 'physical', 'characteristic', 'dm_note')",
    )
    op.add_column(
        "artefacts",
        sa.Column("gm_only", sa.Boolean(), server_default="false", nullable=False),
    )
    _fill(types, groups, stories)


def _article_exists(connection: sa.Connection, wa_id: str) -> bool:
    return (
        connection.execute(
            sa.select(articles.c.wa_id).where(
                articles.c.wa_id == wa_id, articles.c.removed_at.is_(None)
            )
        ).first()
        is not None
    )


def _insert(connection: sa.Connection, table: sa.Table, **values: object) -> int:
    return connection.execute(table.insert().values(**values).returning(table.c.id)).scalar_one()


def _new_part(
    connection: sa.Connection,
    story_id: int,
    wa_id: str,
    title: str,
    position: int,
    element_type: str,
    event_id: int | None = None,
) -> None:
    connection.execute(
        entries.insert().values(
            title=title,
            element_type=element_type,
            text="",
            position=position,
            wa_article_id=wa_id,
            timeline_event_id=event_id,
            source_article_id=wa_id,
            source_section=title,
            story_id=story_id,
            placement="part",
            gm_only=True,
            review_note="",
        )
    )


def _place_artefact_entries(connection: sa.Connection) -> None:
    fiche_articles = set(connection.execute(sa.select(artefacts_table.c.wa_article_id)).scalars())
    rows = connection.execute(
        sa.select(entries.c.id, entries.c.title, entries.c.wa_article_id)
    ).all()
    for row in rows:
        if row.wa_article_id not in fiche_articles:
            continue
        if row.title in PHYSICAL:
            placement = "physical"
        elif row.title in CHARACTERISTICS:
            placement = "characteristic"
        elif row.title in DM_NOTES:
            placement = "dm_note"
        else:
            continue
        connection.execute(
            entries.update().where(entries.c.id == row.id).values(placement=placement)
        )
        logger.info(
            "Lore entry %s (%s) moved to the artefact fiche as %s", row.id, row.title, placement
        )


def _fill(types: sa.Table, groups: sa.Table, stories: sa.Table) -> None:
    connection = op.get_bind()
    type_ids = {
        label: _insert(connection, types, label=label, position=index)
        for index, label in enumerate(DEFAULT_TYPES, start=1)
    }
    _place_artefact_entries(connection)
    event_ids = {
        row.code: row.id
        for row in connection.execute(
            sa.select(events.c.code, events.c.id).where(events.c.removed_at.is_(None))
        )
    }
    artefact_ids = {
        row.wa_article_id: row.id
        for row in connection.execute(
            sa.select(artefacts_table.c.wa_article_id, artefacts_table.c.id)
        )
    }
    joug_story = None
    if _article_exists(connection, JOUG_ID):
        group_id = _insert(connection, groups, title="Malvaar", position=1)
        joug_story = _insert(
            connection,
            stories,
            kind="legende",
            title="Le Joug de Malvaar",
            type_id=type_ids["Légende"],
            group_id=group_id,
            position=1,
            wa_article_id=JOUG_ID,
            artefact_id=artefact_ids.get(JOUG_ID),
            gm_only=True,
        )
        attached = 0
        for title in JOUG_PARTS:
            result = connection.execute(
                entries.update()
                .where(
                    entries.c.wa_article_id == JOUG_ID,
                    entries.c.title == title,
                    entries.c.story_id.is_(None),
                    entries.c.placement == "part",
                )
                .values(story_id=joug_story)
            )
            attached += result.rowcount
            if result.rowcount != 1:
                logger.info("Joug part %s matched %d entries", title, result.rowcount)
        logger.info("Story Le Joug de Malvaar created with %d existing parts", attached)
    if _article_exists(connection, SPECULUM_ID):
        story = _insert(
            connection,
            stories,
            kind="legende",
            title="L'Empire d'Ébène et les Échos",
            type_id=type_ids["Histoire"],
            position=2,
            wa_article_id=SPECULUM_ID,
            gm_only=True,
        )
        for position, (title, code) in enumerate(EMPIRE_PARTS):
            _new_part(
                connection, story, SPECULUM_ID, title, position, "organisation", event_ids.get(code)
            )
            if code not in event_ids:
                logger.info("Event %s missing, part %s left undated", code, title)
        logger.info("Story L'Empire d'Ébène et les Échos created")
    if _article_exists(connection, HELIOPOLIS_ID):
        story = _insert(
            connection,
            stories,
            kind="legende",
            title="La fondation de l'Héliopolis",
            type_id=type_ids["Histoire"],
            position=3,
            era="tumultes",
            approximate=True,
            wa_article_id=HELIOPOLIS_ID,
            gm_only=True,
        )
        _new_part(connection, story, HELIOPOLIS_ID, "Histoire Fondatrice", 0, "organisation")
        logger.info("Story La fondation de l'Héliopolis created")
    if _article_exists(connection, FEUILLETS_ID):
        story = _insert(
            connection,
            stories,
            kind="ouvrage",
            title="Les Feuillets du Dompteur",
            intro_sections="Fiche rapide\nDescription",
            position=1,
            wa_article_id=FEUILLETS_ID,
            artefact_id=artefact_ids.get(JOUG_ID),
            legend_id=joug_story,
            gm_only=True,
        )
        for position, title in enumerate(FEUILLETS_PARTS):
            _new_part(connection, story, FEUILLETS_ID, title, position, "artefact")
        logger.info("Book Les Feuillets du Dompteur created")
    orphans = connection.execute(
        sa.select(entries.c.id, entries.c.title).where(
            entries.c.story_id.is_(None), entries.c.placement == "part"
        )
    ).all()
    for row in orphans:
        logger.info("Lore entry %s (%s) has no story yet", row.id, row.title)


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        entries.delete().where(entries.c.story_id.is_not(None), entries.c.text == "")
    )
    op.drop_column("artefacts", "gm_only")
    op.drop_constraint("ck_lore_entries_placement", "lore_entries", type_="check")
    op.drop_column("lore_entries", "placement")
    op.drop_index("ix_lore_entries_story_id", table_name="lore_entries")
    op.drop_constraint("fk_lore_entries_story_id_lore_stories", "lore_entries", type_="foreignkey")
    op.drop_column("lore_entries", "story_id")
    for column in ("type_id", "group_id", "wa_article_id", "artefact_id", "legend_id"):
        op.drop_index(f"ix_lore_stories_{column}", table_name="lore_stories")
    op.drop_table("lore_stories")
    op.drop_table("lore_groups")
    op.drop_table("lore_types")
