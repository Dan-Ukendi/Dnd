"""Dates, place, people, organisations and consequences of character events

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EVENT_TABLES = ("character_event_people", "character_event_organizations")


def _article() -> sa.Column:
    return sa.Column(
        "wa_article_id",
        sa.String(36),
        sa.ForeignKey("wa_articles.wa_id", ondelete="SET NULL"),
        nullable=True,
    )


def upgrade() -> None:
    op.add_column("character_events", sa.Column("world_year", sa.Integer(), nullable=True))
    op.add_column("character_events", sa.Column("age", sa.Integer(), nullable=True))
    op.add_column(
        "character_events",
        sa.Column("date_approximate", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column("character_events", sa.Column("place_name", sa.String(200), nullable=True))
    op.add_column(
        "character_events",
        sa.Column(
            "place_article_id",
            sa.String(36),
            sa.ForeignKey(
                "wa_articles.wa_id",
                ondelete="SET NULL",
                name="fk_character_events_place_article_id_wa_articles",
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "character_events",
        sa.Column("consequences", sa.Text(), server_default="", nullable=False),
    )
    op.add_column(
        "character_events", sa.Column("gm_notes", sa.Text(), server_default="", nullable=False)
    )
    op.create_check_constraint(
        "ck_character_events_age", "character_events", "age IS NULL OR age >= 0"
    )
    op.add_column(
        "character_profiles",
        sa.Column("events_edited_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "character_event_people",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("character_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("role", sa.String(200), nullable=True),
        _article(),
    )
    op.create_table(
        "character_event_organizations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("character_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        _article(),
    )
    for table in EVENT_TABLES:
        op.create_index(f"ix_{table}_event_id", table, ["event_id"])


def downgrade() -> None:
    for table in reversed(EVENT_TABLES):
        op.drop_table(table)
    op.drop_column("character_profiles", "events_edited_at")
    op.drop_constraint("ck_character_events_age", "character_events", type_="check")
    for column in (
        "gm_notes",
        "consequences",
        "place_article_id",
        "place_name",
        "date_approximate",
        "age",
        "world_year",
    ):
        op.drop_column("character_events", column)
