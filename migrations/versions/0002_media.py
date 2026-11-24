"""Hosted media

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "media_assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "wa_article_id",
            sa.String(36),
            sa.ForeignKey("wa_articles.wa_id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("banner_section", sa.String(30), nullable=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("role IN ('portrait', 'map', 'sheet')", name="ck_media_assets_role"),
        sa.UniqueConstraint("wa_article_id", "role", name="uq_media_assets_article_role"),
        sa.UniqueConstraint("banner_section", name="uq_media_assets_banner_section"),
    )
    op.create_index("ix_media_assets_wa_article_id", "media_assets", ["wa_article_id"])
    op.create_index("ix_media_assets_sha256", "media_assets", ["sha256"])

    op.create_table(
        "media_variants",
        sa.Column(
            "asset_id",
            sa.Integer(),
            sa.ForeignKey("media_assets.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("name", sa.String(10), primary_key=True),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("gzip_data", sa.LargeBinary(), nullable=True),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("etag", sa.String(64), nullable=False),
        sa.CheckConstraint("name IN ('full', 'thumb')", name="ck_media_variants_name"),
    )


def downgrade() -> None:
    op.drop_table("media_variants")
    op.drop_index("ix_media_assets_sha256", table_name="media_assets")
    op.drop_index("ix_media_assets_wa_article_id", table_name="media_assets")
    op.drop_table("media_assets")
