"""Battle map library: battles reference a map and can be ended or resumed

Revision ID: 0027
Revises: 0026
"""

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "battle_maps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("cols", sa.Integer(), nullable=False),
        sa.Column("rows", sa.Integer(), nullable=False),
        sa.Column("image", sa.LargeBinary(), nullable=False),
        sa.Column("image_type", sa.String(50), nullable=False),
        sa.Column("image_sha256", sa.String(64), nullable=False),
        sa.Column("thumbnail", sa.LargeBinary(), nullable=True),
        sa.Column("source_sha256", sa.String(64), nullable=True, unique=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("cols > 0 AND rows > 0", name="ck_battle_maps_grid"),
    )
    op.add_column(
        "battles",
        sa.Column(
            "map_id",
            sa.Integer(),
            sa.ForeignKey("battle_maps.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.create_index("ix_battles_map_id", "battles", ["map_id"])
    op.add_column(
        "battles",
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.add_column("battles", sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE battles SET started_at = created_at")
    op.execute(
        """
        INSERT INTO battle_maps (name, cols, rows, image, image_type, image_sha256, source_sha256)
        SELECT name, cols, rows, image, image_type, image_sha256, image_sha256
        FROM battles
        WHERE image IS NOT NULL AND image_type IS NOT NULL AND image_sha256 IS NOT NULL
        ON CONFLICT DO NOTHING
        """
    )
    op.execute(
        "UPDATE battles SET map_id = battle_maps.id FROM battle_maps "
        "WHERE battle_maps.source_sha256 = battles.image_sha256"
    )
    for column in ("image", "image_type", "image_sha256", "cols", "rows"):
        op.drop_column("battles", column)


def downgrade() -> None:
    op.add_column("battles", sa.Column("cols", sa.Integer(), nullable=True))
    op.add_column("battles", sa.Column("rows", sa.Integer(), nullable=True))
    op.add_column("battles", sa.Column("image", sa.LargeBinary(), nullable=True))
    op.add_column("battles", sa.Column("image_type", sa.String(50), nullable=True))
    op.add_column("battles", sa.Column("image_sha256", sa.String(64), nullable=True))
    op.execute(
        "UPDATE battles SET cols = m.cols, rows = m.rows, image = m.image, "
        "image_type = m.image_type, image_sha256 = m.image_sha256 "
        "FROM battle_maps AS m WHERE m.id = battles.map_id"
    )
    op.execute("UPDATE battles SET cols = 1, rows = 1 WHERE cols IS NULL")
    op.alter_column("battles", "cols", nullable=False)
    op.alter_column("battles", "rows", nullable=False)
    op.create_check_constraint("ck_battles_grid", "battles", "cols > 0 AND rows > 0")
    op.drop_index("ix_battles_map_id", table_name="battles")
    op.drop_column("battles", "ended_at")
    op.drop_column("battles", "started_at")
    op.drop_column("battles", "map_id")
    op.drop_table("battle_maps")
