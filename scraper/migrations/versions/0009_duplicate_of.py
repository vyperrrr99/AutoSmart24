from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_duplicate_of"
down_revision = "0008_removal_reason"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Several AutoScout seller identities can publish the same car: Autohero
    # exposes one catalogue through nine, so 9,834 of its 12,798 listings are
    # copies. The copies point here at the one listing to count; NULL means
    # this row IS the one to count, so any query that ignores the column keeps
    # the behaviour it had before the column existed.
    op.add_column("listings", sa.Column("duplicate_of", sa.String(36), nullable=True))
    op.create_index("ix_listings_duplicate_of", "listings", ["duplicate_of"])


def downgrade() -> None:
    op.drop_index("ix_listings_duplicate_of", table_name="listings")
    op.drop_column("listings", "duplicate_of")
