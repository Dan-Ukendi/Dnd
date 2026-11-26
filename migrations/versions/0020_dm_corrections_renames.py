"""DM corrections: Lux Victrix, Kratos, Shönö, Gérard the barman, Guild ranks, Perte du Joug

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-26
"""

import json
import logging
import re
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

KRATOS_ID = "a807dc99-1661-455b-8faa-211393c1b0ab"
SHONO_ID = "c08747b8-2c6d-4617-b44e-f55ffba196f2"
LUX_VICTRIX_ID = "c28faade-953f-4baf-870b-a3ee3bd64cb8"

_NOT_A_RITUAL = r"(?! des (?:Ombres|Lumières))"
RULES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), replacement)
    for pattern, replacement in (
        (
            r"\b(?:Le Temple Blanc|La Lux Victrix) d'Arès est bâti\b",
            "La Lux Victrix d'Arès est bâtie",
        ),
        (r"Le temple d'Arès \(le Temple Blanc\)", "Lux Victrix"),
        (r"Le temple d'Arès le Temple Blanc", "Lux Victrix"),
        (r"\bdu Temple Blanc\b", "de la Lux Victrix"),
        (r"\bau Temple Blanc\b", "à la Lux Victrix"),
        (r"\bAu Temple Blanc\b", "À la Lux Victrix"),
        (r"\ble Temple Blanc\b", "la Lux Victrix"),
        (r"\bLe Temple Blanc\b", "La Lux Victrix"),
        (r"\bTemple Blanc\b", "Lux Victrix"),
        (r"Kratéros", "Kratos"),
        (r"KRATÉROS", "KRATOS"),
        (r"\b([Dd])e la Nuit\b" + _NOT_A_RITUAL, r"\1e Shönö"),
        (r"(?<!\w)([Àà]) la Nuit\b" + _NOT_A_RITUAL, r"\1 Shönö"),
        (r"\b[Ll]a Nuit\b" + _NOT_A_RITUAL, "Shönö"),
        (r"\ble majordome de Sophie\b", "le barman de Sophie"),
        (r"\bson majordome Gérard\b", "son barman Gérard"),
        (r"\bmajordome de Sophie\b", "barman de Sophie"),
        (
            r"A pour les plus puissants, puis B, C et D\.",
            "A pour les plus puissants, puis B, C, D, E et F.",
        ),
        (r"La République de Fer", "Le Khaanat des Cinq Lances"),
        (r"La Concordance Logique", "L'Héliopolis des Harmoniens"),
        (
            r"Le Dernier Bastion(\[/b\])? \(ou Royaume de l'Aube\)",
            r"Le Royaume de Sanguis Coronae\1 (Secundum Imperium Sanguicor)",
        ),
        (r"Long sommeil du Joug", "Perte du Joug"),
        (
            r"Jean-Charles ouvre la taverne Le Troll qui Rit",
            "Jean-Charles hérite de la taverne Le Troll qui Rit",
        ),
    )
)

TEXT_COLUMNS: dict[str, tuple[str, ...]] = {
    "timeline_events": ("title", "summary", "place"),
    "timeline_event_fiches": ("title",),
    "character_profiles": ("backstory", "combat"),
    "character_events": ("title", "text", "place_name", "consequences", "gm_notes"),
    "character_event_people": ("name", "role"),
    "character_event_organizations": ("name",),
    "character_relations": ("name", "role", "text", "gm_notes"),
    "character_items": ("name", "text", "gm_notes"),
    "character_actions": ("name", "text"),
    "character_journal_entries": ("title", "text", "lists"),
    "artefacts": (
        "tagline",
        "object_type",
        "other_names",
        "properties",
        "bonuses",
        "curse",
        "holder",
        "location",
        "status",
    ),
    "lore_groups": ("title", "description"),
    "lore_stories": ("title", "description", "intro_sections"),
    "lore_entries": ("title", "text", "source_section"),
    "places": ("name", "location", "source_anchor", "description", "gm_notes"),
    "positions": ("name", "title", "scope", "source_anchor", "gm_notes"),
    "npcs": ("name", "title", "standing", "description", "gm_notes"),
    "npc_roles": ("name", "description", "gm_notes"),
    "fiche_sections": ("title", "text"),
    "npc_relations": ("label", "reverse_label"),
    "stat_blocks": ("name", "source", "traits", "actions", "bonus_actions", "reactions"),
    "link_aliases": ("alias",),
    "media_assets": ("title",),
}
JSON_COLUMNS = frozenset({"combat", "lists", "traits", "actions", "bonus_actions", "reactions"})
PRIMARY_KEYS = {"character_profiles": "character_id"}

PERTE_SUMMARY = "Après la mort de Malvaar, le Joug est perdu."
HERITAGE_SUMMARY = (
    "Jean-Charles hérite du Troll qui Rit de ses ancêtres : un de ses aïeuls, un aventurier "
    "chasseur de trolls, avait acheté la taverne avec la fortune de ses chasses."
)
STATUS_PARAGRAPH = re.compile(r"\n*À valider :[^\n]*")
TOMORZORIG_POSITION = "khuchniin-khondii-tomorzorig"
OLD_EXCLUSION = "La Nuit"
NEW_EXCLUSION = "Süüdriin shönö"


def rename(text: str) -> str:
    for pattern, replacement in RULES:
        text = pattern.sub(replacement, text)
    return text


def rename_value(value: Any) -> Any:
    if isinstance(value, str):
        return rename(value)
    if isinstance(value, list):
        return [rename_value(item) for item in value]
    if isinstance(value, dict):
        return {key: rename_value(item) for key, item in value.items()}
    return value


def _existing_columns(connection: sa.Connection, table: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table):
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def _rename_table(connection: sa.Connection, table: str, columns: tuple[str, ...]) -> None:
    present = [column for column in columns if column in _existing_columns(connection, table)]
    if not present:
        return
    key = PRIMARY_KEYS.get(table, "id")
    rows = connection.execute(
        sa.text(f"SELECT {key} AS id, {', '.join(present)} FROM {table} ORDER BY {key}")  # noqa: S608
    ).mappings()
    for row in list(rows):
        changes: dict[str, Any] = {}
        for column in present:
            value = row[column]
            if value is None:
                continue
            renamed = rename_value(value)
            if renamed != value:
                changes[column] = json.dumps(renamed) if column in JSON_COLUMNS else renamed
                logger.info("Renamed %s.%s of row %s", table, column, row["id"])
        if changes:
            assignments = ", ".join(
                f"{column} = CAST(:{column} AS JSONB)"
                if column in JSON_COLUMNS
                else f"{column} = :{column}"
                for column in changes
            )
            connection.execute(
                sa.text(f"UPDATE {table} SET {assignments} WHERE {key} = :row_id"),  # noqa: S608
                {**changes, "row_id": row["id"]},
            )


def _correct_chronology(connection: sa.Connection) -> None:
    events = sa.table(
        "timeline_events",
        sa.column("code", sa.String),
        sa.column("title", sa.String),
        sa.column("summary", sa.Text),
        sa.column("start_year", sa.Integer),
        sa.column("end_year", sa.Integer),
    )
    corrections: dict[str, dict[str, Any]] = {
        "A4": {"title": "Perte du Joug", "end_year": None, "summary": PERTE_SUMMARY},
        "C11": {
            "title": "Jean-Charles hérite de la taverne Le Troll qui Rit",
            "summary": HERITAGE_SUMMARY,
        },
    }
    for code, values in corrections.items():
        result = connection.execute(events.update().where(events.c.code == code).values(**values))
        logger.info("Chronology %s corrected: %d row(s)", code, result.rowcount)
    result = connection.execute(
        events.update()
        .where(events.c.code == "B8", events.c.start_year > 754)
        .values(start_year=754)
    )
    logger.info("Chronology B8 now starts in 754: %d row(s)", result.rowcount)
    row = connection.execute(sa.select(events.c.summary).where(events.c.code == "S5")).first()
    if row is not None and STATUS_PARAGRAPH.search(row.summary or ""):
        connection.execute(
            events.update()
            .where(events.c.code == "S5")
            .values(summary=STATUS_PARAGRAPH.sub("", row.summary).strip())
        )
        logger.info("Chronology S5: status paragraph removed")


def _links(connection: sa.Connection) -> None:
    exclusions = sa.table("link_exclusions", sa.column("term", sa.String))
    removed = connection.execute(exclusions.delete().where(exclusions.c.term == OLD_EXCLUSION))
    logger.info("Link exclusion %s removed: %d row(s)", OLD_EXCLUSION, removed.rowcount)
    exists = connection.execute(
        sa.select(exclusions.c.term).where(exclusions.c.term == NEW_EXCLUSION)
    ).first()
    if exists is None:
        connection.execute(exclusions.insert().values(term=NEW_EXCLUSION))
        logger.info("Link exclusion %s added", NEW_EXCLUSION)


def upgrade() -> None:
    connection = op.get_bind()
    for table, columns in TEXT_COLUMNS.items():
        _rename_table(connection, table, columns)
    _correct_chronology(connection)
    result = connection.execute(
        sa.text("DELETE FROM positions WHERE key = :key"), {"key": TOMORZORIG_POSITION}
    )
    logger.info("Duplicate Khan position of Tömörzorig removed: %d row(s)", result.rowcount)
    _links(connection)


def downgrade() -> None:
    connection = op.get_bind()
    exclusions = sa.table("link_exclusions", sa.column("term", sa.String))
    connection.execute(exclusions.delete().where(exclusions.c.term == NEW_EXCLUSION))
    exists = connection.execute(
        sa.select(exclusions.c.term).where(exclusions.c.term == OLD_EXCLUSION)
    ).first()
    if exists is None:
        connection.execute(exclusions.insert().values(term=OLD_EXCLUSION))
