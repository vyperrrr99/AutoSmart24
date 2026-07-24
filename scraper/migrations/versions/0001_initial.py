from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "listings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("cross_reference_id", sa.String(32), nullable=True),
        sa.Column("brand", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("model_group", sa.String(128), nullable=True),
        sa.Column("variant", sa.String(128), nullable=True),
        sa.Column("motor_type_name", sa.String(128), nullable=True),
        sa.Column("version_input", sa.String(256), nullable=True),
        sa.Column("transmission", sa.String(64), nullable=True),
        sa.Column("fuel", sa.String(64), nullable=True),
        sa.Column("first_registration", sa.Date(), nullable=True),
        sa.Column("mileage_km", sa.Integer(), nullable=True),
        sa.Column("power_kw", sa.Integer(), nullable=True),
        sa.Column("power_cv", sa.Integer(), nullable=True),
        sa.Column("displacement_ccm", sa.Integer(), nullable=True),
        sa.Column("body_type", sa.String(64), nullable=True),
        sa.Column("body_color", sa.String(64), nullable=True),
        sa.Column("num_seats", sa.Integer(), nullable=True),
        sa.Column("num_doors", sa.Integer(), nullable=True),
        sa.Column("num_previous_owners", sa.Integer(), nullable=True),
        sa.Column("seller_type", sa.String(32), nullable=True),
        sa.Column("seller_company_name", sa.String(256), nullable=True),
        sa.Column("city", sa.String(256), nullable=True),
        sa.Column("province", sa.String(8), nullable=True),
        sa.Column("zip_code", sa.String(16), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("price", sa.Integer(), nullable=True),
        sa.Column("vat_exposed", sa.Boolean(), nullable=True),
        sa.Column("price_evaluation_category", sa.Integer(), nullable=True),
        sa.Column("price_evaluation_median", sa.Integer(), nullable=True),
        sa.Column("url", sa.String(1024), nullable=False),
        sa.Column("created_at_source", sa.DateTime(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("sold_at", sa.DateTime(), nullable=True),
        sa.Column("detail_scraped", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("raw_snippet", JSONB(), nullable=True),
        sa.Column("raw_detail", JSONB(), nullable=True),
    )
    op.create_index("ix_listings_brand", "listings", ["brand"])

    op.create_table(
        "price_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("listing_id", sa.String(36), sa.ForeignKey("listings.id"), nullable=False),
        sa.Column("price", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_price_history_listing_id", "price_history", ["listing_id"])

    op.create_table(
        "scrape_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("brand", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("listings_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("new_listings", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("price_changes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sold_detected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("errors_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_scrape_runs_brand", "scrape_runs", ["brand"])

    op.create_table(
        "scrape_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("scrape_runs.id"), nullable=True),
        sa.Column("brand", sa.String(64), nullable=True),
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("message", sa.String(2048), nullable=False),
        sa.Column("url", sa.String(1024), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_scrape_events_run_id", "scrape_events", ["run_id"])
    op.create_index("ix_scrape_events_brand", "scrape_events", ["brand"])


def downgrade() -> None:
    op.drop_table("scrape_events")
    op.drop_table("scrape_runs")
    op.drop_table("price_history")
    op.drop_table("listings")
