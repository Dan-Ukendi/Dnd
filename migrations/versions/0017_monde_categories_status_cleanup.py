"""Monde categories (Pays, Villes, Bâtiments); work-status fields and review notes removed

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-25
"""

import json
import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

LORE_OVERRIDES = frozenset({"9a0ff719-7202-4be9-a52c-997e5af92751"})
TEMPLATE_CATEGORIES = {"settlement": "ville", "landmark": "batiment"}
STATUS_TABLES = {
    "timeline_events": "code",
    "places": "key",
    "positions": "key",
}
REVIEW_TABLES = {
    "artefacts": "wa_article_id",
    "lore_stories": "title",
    "lore_entries": "title",
}
RELATION_FRAGMENTS = (
    (" (identité à fixer : Altan, qui l'a relayée, ou quelqu'un d'autre)", ""),
    (" : ce que Khaetar sait de sa classe est à confirmer", ""),
    (" (que Khaetar le sache ou non est à fixer)", ""),
    ("(couturière ou tisseuse, à fixer)", "(couturière ou tisseuse)"),
    (
        " Choix à valider : on a supposé que Khaetar se souvient qu'Altan menait la foule ; "
        "sinon, déplacer cette ligne du texte vers ces notes.",
        "",
    ),
    (" Identité et motivations à définir ; elle veut", " Elle veut"),
    (" Sa réaction au Gage d'Arès reste à définir.", ""),
)
TITLE_SUFFIX = " (à fixer)"
OLD_DRAFT_REASON = "Brouillon ou statut de publication inconnu"
NEW_DRAFT_REASON = "Non publié sur World Anvil ou statut de publication inconnu"


def upgrade() -> None:
    connection = op.get_bind()
    op.create_table(
        "world_categories",
        sa.Column(
            "wa_id",
            sa.String(36),
            sa.ForeignKey("wa_articles.wa_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("category", sa.String(10), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "category IS NULL OR category IN ('pays', 'ville', 'batiment')",
            name="ck_world_categories_category",
        ),
    )
    _classify(connection)
    _remove_statuses(connection)
    _clean_notes(connection)
    _remove_reviews(connection)
    connection.execute(
        sa.text("UPDATE wa_articles SET visibility_reason = :new WHERE visibility_reason = :old"),
        {"old": OLD_DRAFT_REASON, "new": NEW_DRAFT_REASON},
    )


def _variant(metadata: object) -> str:
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except ValueError:
            return ""
    if isinstance(metadata, dict) and isinstance(metadata.get("variant"), str):
        return metadata["variant"].strip().lower()
    return ""


def _classify(connection: sa.Connection) -> None:
    rows = connection.execute(
        sa.text(
            "SELECT wa_id, title, template_type, raw -> 'articleMetadata' AS metadata "
            "FROM wa_articles ORDER BY title"
        )
    ).all()
    for row in rows:
        template = (row.template_type or "").lower()
        category = TEMPLATE_CATEGORIES.get(template)
        if (
            template == "organization"
            and row.wa_id not in LORE_OVERRIDES
            and _variant(row.metadata) == "country"
        ):
            category = "pays"
        if category is None:
            continue
        connection.execute(
            sa.text("INSERT INTO world_categories (wa_id, category) VALUES (:wa_id, :category)"),
            {"wa_id": row.wa_id, "category": category},
        )
        logger.info("Classified %s (%s) as %s", row.title, template, category)
    result = connection.execute(
        sa.text(
            "UPDATE lore_entries SET element_type = 'lieu' WHERE element_type = 'organisation' "
            "AND wa_article_id IN (SELECT wa_id FROM world_categories WHERE category IS NOT NULL)"
        )
    )
    logger.info("Lore entries of Monde fiches now typed lieu: %d", result.rowcount)


def _remove_statuses(connection: sa.Connection) -> None:
    for table, key in STATUS_TABLES.items():
        rows = sa.table(table, sa.column(key), sa.column("status"))
        for row in connection.execute(
            sa.select(rows.c[key].label("key"), rows.c.status).where(rows.c.status != "etabli")
        ):
            logger.info("Removed %s.status of %s: %s", table, row.key, row.status)
        op.drop_column(table, "status")


def _clean_notes(connection: sa.Connection) -> None:
    for table in ("places", "positions"):
        rows = sa.table(table, sa.column("key"), sa.column("gm_notes"))
        for row in connection.execute(
            sa.select(rows.c.key, rows.c.gm_notes).where(rows.c.gm_notes != "")
        ):
            logger.info("Removed %s.gm_notes of %s: %s", table, row.key, row.gm_notes)
        connection.execute(rows.update().where(rows.c.gm_notes != "").values(gm_notes=""))
    for row in connection.execute(
        sa.text("SELECT id, key, title FROM positions WHERE title LIKE :pattern"),
        {"pattern": f"%{TITLE_SUFFIX}"},
    ).all():
        logger.info("Removed %r from positions.title of %s", TITLE_SUFFIX.strip(), row.key)
        connection.execute(
            sa.text("UPDATE positions SET title = :title WHERE id = :id"),
            {"title": row.title[: -len(TITLE_SUFFIX)], "id": row.id},
        )
    for row in connection.execute(
        sa.text("SELECT id, name, gm_notes FROM character_relations WHERE gm_notes <> ''")
    ).all():
        notes = row.gm_notes
        for fragment, replacement in RELATION_FRAGMENTS:
            if fragment in notes:
                logger.info(
                    "Removed %r from character_relations.gm_notes of %s", fragment, row.name
                )
                notes = notes.replace(fragment, replacement)
        if notes != row.gm_notes:
            connection.execute(
                sa.text("UPDATE character_relations SET gm_notes = :notes WHERE id = :id"),
                {"notes": notes, "id": row.id},
            )


def _remove_reviews(connection: sa.Connection) -> None:
    for table, label in REVIEW_TABLES.items():
        rows = sa.table(table, sa.column(label), sa.column("review_note"))
        for row in connection.execute(
            sa.select(rows.c[label].label("label"), rows.c.review_note).where(
                rows.c.review_note != ""
            )
        ):
            logger.info("Removed %s.review_note of %s: %s", table, row.label, row.review_note)
        op.drop_column(table, "review_note")


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text("UPDATE wa_articles SET visibility_reason = :old WHERE visibility_reason = :new"),
        {"old": OLD_DRAFT_REASON, "new": NEW_DRAFT_REASON},
    )
    for table in REVIEW_TABLES:
        op.add_column(table, sa.Column("review_note", sa.Text(), server_default="", nullable=False))
    for table in STATUS_TABLES:
        op.add_column(
            table, sa.Column("status", sa.String(10), server_default="etabli", nullable=False)
        )
        op.alter_column(table, "status", server_default="a_valider")
        op.create_check_constraint(f"ck_{table}_status", table, "status IN ('etabli', 'a_valider')")
    connection.execute(
        sa.text(
            "UPDATE lore_entries SET element_type = 'organisation' WHERE element_type = 'lieu' "
            "AND wa_article_id IN (SELECT c.wa_id FROM world_categories c JOIN wa_articles a "
            "ON a.wa_id = c.wa_id WHERE a.template_type = 'organization')"
        )
    )
    op.drop_table("world_categories")
