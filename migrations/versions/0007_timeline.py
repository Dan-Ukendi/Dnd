"""World chronology: timeline events, links, fiches and era settings

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "timeline_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("summary", sa.Text(), server_default="", nullable=False),
        sa.Column("start_year", sa.Integer(), nullable=False),
        sa.Column("end_year", sa.Integer(), nullable=True),
        sa.Column("place", sa.String(300), nullable=True),
        sa.Column("importance", sa.String(10), nullable=False),
        sa.Column("origin", sa.String(10), nullable=False),
        sa.Column("status", sa.String(10), server_default="a_valider", nullable=False),
        sa.Column("visibility", sa.String(10), server_default="mj", nullable=False),
        sa.Column("chronicle", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("session", sa.Integer(), nullable=True),
        sa.Column("day", sa.String(2), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("wa_history_id", sa.String(36), nullable=True),
        sa.Column("wa_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("modified_by_dm", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "end_year IS NULL OR end_year >= start_year", name="ck_timeline_events_years"
        ),
        sa.CheckConstraint(
            "importance IN ('majeur', 'notable', 'mineur')", name="ck_timeline_events_importance"
        ),
        sa.CheckConstraint("origin IN ('canon', 'ajout')", name="ck_timeline_events_origin"),
        sa.CheckConstraint("status IN ('etabli', 'a_valider')", name="ck_timeline_events_status"),
        sa.CheckConstraint("visibility IN ('mj', 'joueurs')", name="ck_timeline_events_visibility"),
        sa.CheckConstraint("day IS NULL OR day IN ('J1', 'J2')", name="ck_timeline_events_day"),
        sa.CheckConstraint("session IS NULL OR session >= 1", name="ck_timeline_events_session"),
        sa.UniqueConstraint("code", name="uq_timeline_events_code"),
        sa.UniqueConstraint("wa_history_id", name="uq_timeline_events_wa_history_id"),
    )
    op.create_index(
        "ix_timeline_events_start_year_sort_order",
        "timeline_events",
        ["start_year", "sort_order"],
    )
    op.create_table(
        "timeline_event_links",
        sa.Column(
            "source_id",
            sa.Integer(),
            sa.ForeignKey("timeline_events.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "target_id",
            sa.Integer(),
            sa.ForeignKey("timeline_events.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("origin", sa.String(3), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("source_id <> target_id", name="ck_timeline_event_links_distinct"),
        sa.CheckConstraint("origin IN ('wa', 'mj')", name="ck_timeline_event_links_origin"),
    )
    op.create_index("ix_timeline_event_links_target_id", "timeline_event_links", ["target_id"])
    op.create_table(
        "timeline_event_fiches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("timeline_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "wa_article_id",
            sa.String(36),
            sa.ForeignKey("wa_articles.wa_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_timeline_event_fiches_event_id", "timeline_event_fiches", ["event_id"])
    op.create_table(
        "timeline_eras",
        sa.Column("slug", sa.String(30), primary_key=True),
        sa.Column("name", sa.String(100), nullable=True),
        sa.Column("subtitle", sa.String(300), nullable=True),
        sa.Column("color", sa.String(7), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    for column in ("events_created", "events_updated", "events_removed"):
        op.add_column(
            "sync_runs", sa.Column(column, sa.Integer(), server_default="0", nullable=False)
        )


def downgrade() -> None:
    for column in ("events_removed", "events_updated", "events_created"):
        op.drop_column("sync_runs", column)
    op.drop_table("timeline_eras")
    op.drop_index("ix_timeline_event_fiches_event_id", table_name="timeline_event_fiches")
    op.drop_table("timeline_event_fiches")
    op.drop_index("ix_timeline_event_links_target_id", table_name="timeline_event_links")
    op.drop_table("timeline_event_links")
    op.drop_index("ix_timeline_events_start_year_sort_order", table_name="timeline_events")
    op.drop_table("timeline_events")
