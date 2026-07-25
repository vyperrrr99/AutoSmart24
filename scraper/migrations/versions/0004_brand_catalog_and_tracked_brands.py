from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_brand_tables"
down_revision = "0003_widen_province"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "brand_catalog",
        sa.Column("make_id", sa.Integer(), primary_key=True),
        sa.Column("display_name", sa.String(64), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "tracked_brands",
        sa.Column("make_id", sa.Integer(), sa.ForeignKey("brand_catalog.make_id"), primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column("display_name", sa.String(64), nullable=False),
        sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("year_from_years", sa.Integer(), nullable=True),
        sa.Column("schedule_day_of_week", sa.String(3), nullable=True),
        sa.Column("schedule_hour", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("schedule_minute", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("tracked_brands")
    op.drop_table("brand_catalog")
