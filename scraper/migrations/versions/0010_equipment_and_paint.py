from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010_equipment_and_paint"
down_revision = "0009_duplicate_of"
branch_labels = None
depends_on = None

# The nine a car expert picked out of the 142 options the market publishes.
OPTIONS = (
    "has_sunroof",
    "has_panoramic_roof",
    "has_leather_interior",
    "has_heated_seats",
    "has_electric_seats",
    "has_parking_camera",
    "has_full_led_headlights",
    "has_led_headlights",
    "has_alloy_wheels",
)


def upgrade() -> None:
    # The whole equipment list, raw. It is not what the BI queries -- the
    # booleans below are -- but without it every later change of mind costs a
    # full re-scrape: raw_detail was dropped in 0006, and a sold listing can
    # never be read again at all. Keeping it makes a tenth option a migration.
    op.add_column("listings", sa.Column("equipment", postgresql.JSONB(), nullable=True))
    op.create_index("ix_listings_equipment", "listings", ["equipment"],
                    postgresql_using="gin")

    # Paint. `body_color` already held the generic English name; these two are
    # the finish ("Metallizzato") and the manufacturer's own name for it
    # ("Verde Salvia Metallizzato"), which is what a special colour is worth.
    op.add_column("listings", sa.Column("paint_type", sa.String(32), nullable=True))
    op.add_column("listings", sa.Column("body_color_original", sa.String(128), nullable=True))

    # Nullable on purpose, and NULL is not False: a listing whose detail page
    # was never read has no equipment list, and claiming "no sunroof" about a
    # car nobody looked at is indistinguishable downstream from having looked.
    for column in OPTIONS:
        op.add_column("listings", sa.Column(column, sa.Boolean(), nullable=True))


def downgrade() -> None:
    for column in OPTIONS:
        op.drop_column("listings", column)
    op.drop_column("listings", "body_color_original")
    op.drop_column("listings", "paint_type")
    op.drop_index("ix_listings_equipment", table_name="listings")
    op.drop_column("listings", "equipment")
