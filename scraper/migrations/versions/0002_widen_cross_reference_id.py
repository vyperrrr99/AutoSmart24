from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_widen_cross_reference_id"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "listings",
        "cross_reference_id",
        existing_type=sa.String(32),
        type_=sa.String(128),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "listings",
        "cross_reference_id",
        existing_type=sa.String(128),
        type_=sa.String(32),
        existing_nullable=True,
    )
