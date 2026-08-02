from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_removal_reason"
down_revision = "0007_run_progress"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Records WHY a listing stopped being active when the reason was not a
    # sale: a duplicate dropped, an ad republished under a new id, or a dealer
    # closing for August. Nullable, so every existing row keeps meaning what it
    # already meant.
    op.add_column("listings", sa.Column("removal_reason", sa.String(32), nullable=True))
    # The reclassification looks up a dealer's live stock on every run.
    op.create_index("ix_listings_dealer_status", "listings", ["dealer_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_listings_dealer_status", table_name="listings")
    op.drop_column("listings", "removal_reason")
