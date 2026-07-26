from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_detail_fields_dealers"
down_revision = "0004_brand_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dealers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_name", sa.String(256), nullable=True),
        sa.Column("ratings_stars", sa.Float(), nullable=True),
        sa.Column("ratings_count", sa.Integer(), nullable=True),
        sa.Column("recommend_percentage", sa.Integer(), nullable=True),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
    )

    op.add_column("listings", sa.Column("had_accident", sa.Boolean(), nullable=True))
    op.add_column("listings", sa.Column("has_full_service_history", sa.Boolean(), nullable=True))
    op.add_column("listings", sa.Column("gears", sa.Integer(), nullable=True))
    op.add_column("listings", sa.Column("drive_train", sa.String(64), nullable=True))
    op.add_column("listings", sa.Column("cylinders", sa.Integer(), nullable=True))
    op.add_column("listings", sa.Column("weight_kg", sa.Integer(), nullable=True))
    op.add_column("listings", sa.Column("co2_emissions_g_km", sa.Float(), nullable=True))
    op.add_column("listings", sa.Column("fuel_consumption_combined", sa.Float(), nullable=True))
    op.add_column("listings", sa.Column("fuel_consumption_urban", sa.Float(), nullable=True))
    op.add_column("listings", sa.Column("fuel_consumption_extra_urban", sa.Float(), nullable=True))
    op.add_column("listings", sa.Column("emission_class", sa.String(32), nullable=True))
    op.add_column("listings", sa.Column("upholstery", sa.String(64), nullable=True))
    op.add_column("listings", sa.Column("upholstery_color", sa.String(64), nullable=True))
    op.add_column("listings", sa.Column("is_conditional_price", sa.Boolean(), nullable=True))
    op.add_column("listings", sa.Column("interaction_count", sa.Integer(), nullable=True))
    op.add_column("listings", sa.Column("favorites_count", sa.Integer(), nullable=True))
    op.add_column("listings", sa.Column("new_driver_suitable", sa.Boolean(), nullable=True))
    op.add_column(
        "listings",
        sa.Column("dealer_id", sa.Integer(), sa.ForeignKey("dealers.id"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("listings", "dealer_id")
    op.drop_column("listings", "new_driver_suitable")
    op.drop_column("listings", "favorites_count")
    op.drop_column("listings", "interaction_count")
    op.drop_column("listings", "is_conditional_price")
    op.drop_column("listings", "upholstery_color")
    op.drop_column("listings", "upholstery")
    op.drop_column("listings", "emission_class")
    op.drop_column("listings", "fuel_consumption_extra_urban")
    op.drop_column("listings", "fuel_consumption_urban")
    op.drop_column("listings", "fuel_consumption_combined")
    op.drop_column("listings", "co2_emissions_g_km")
    op.drop_column("listings", "weight_kg")
    op.drop_column("listings", "cylinders")
    op.drop_column("listings", "drive_train")
    op.drop_column("listings", "gears")
    op.drop_column("listings", "has_full_service_history")
    op.drop_column("listings", "had_accident")
    op.drop_table("dealers")
