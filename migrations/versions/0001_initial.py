"""Initial schema

Revision ID: 0001
Revises:
Create Date: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEARCH_VECTOR_SQL = (
    "setweight(to_tsvector('french'::regconfig, coalesce(title, '')), 'A') || "
    "setweight(to_tsvector('french'::regconfig, coalesce(content_text, '')), 'B')"
)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("login", sa.String(100), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("session_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("role IN ('dm', 'player')", name="ck_users_role"),
        sa.UniqueConstraint("login", name="uq_users_login"),
    )

    op.create_table(
        "invitations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("token_hash", name="uq_invitations_token_hash"),
    )
    op.create_index("ix_invitations_user_id", "invitations", ["user_id"])

    op.create_table(
        "wa_articles",
        sa.Column("wa_id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("template_type", sa.String(50), nullable=True),
        sa.Column("entity_class", sa.String(50), nullable=True),
        sa.Column("state", sa.String(20), nullable=True),
        sa.Column("is_draft", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("is_wip", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("url", sa.String(1000), nullable=True),
        sa.Column("content", sa.Text(), server_default="", nullable=False),
        sa.Column("content_text", sa.Text(), server_default="", nullable=False),
        sa.Column("raw", postgresql.JSONB(), nullable=False),
        sa.Column(
            "linked_ids", postgresql.ARRAY(sa.String(36)), server_default="{}", nullable=False
        ),
        sa.Column("player_visible", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("visibility_reason", sa.String(200), nullable=True),
        sa.Column("wa_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(SEARCH_VECTOR_SQL, persisted=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_wa_articles_search_vector", "wa_articles", ["search_vector"], postgresql_using="gin"
    )
    op.create_index("ix_wa_articles_template_type", "wa_articles", ["template_type"])
    op.create_index("ix_wa_articles_url", "wa_articles", ["url"])

    op.create_table(
        "wa_images",
        sa.Column("wa_id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("title", sa.String(500), nullable=True),
        sa.Column("url", sa.String(1000), nullable=True),
        sa.Column("player_visible", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("raw", postgresql.JSONB(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "characters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column(
            "player_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "main_article_id",
            sa.String(36),
            sa.ForeignKey("wa_articles.wa_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_characters_player_id", "characters", ["player_id"])

    op.create_table(
        "character_articles",
        sa.Column(
            "character_id",
            sa.Integer(),
            sa.ForeignKey("characters.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "article_id",
            sa.String(36),
            sa.ForeignKey("wa_articles.wa_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_character_articles_article_id", "character_articles", ["article_id"])

    op.create_table(
        "sync_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("full", sa.Boolean(), nullable=False),
        sa.Column("trigger", sa.String(10), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_count", sa.Integer(), nullable=False),
        sa.Column("updated_count", sa.Integer(), nullable=False),
        sa.Column("unchanged_count", sa.Integer(), nullable=False),
        sa.Column("removed_count", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'success', 'failed')", name="ck_sync_runs_status"
        ),
    )


def downgrade() -> None:
    op.drop_table("sync_runs")
    op.drop_index("ix_character_articles_article_id", table_name="character_articles")
    op.drop_table("character_articles")
    op.drop_index("ix_characters_player_id", table_name="characters")
    op.drop_table("characters")
    op.drop_table("wa_images")
    op.drop_index("ix_wa_articles_url", table_name="wa_articles")
    op.drop_index("ix_wa_articles_template_type", table_name="wa_articles")
    op.drop_index("ix_wa_articles_search_vector", table_name="wa_articles")
    op.drop_table("wa_articles")
    op.drop_index("ix_invitations_user_id", table_name="invitations")
    op.drop_table("invitations")
    op.drop_table("users")
