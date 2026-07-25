from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_widen_province"
down_revision = "0002_widen_cross_reference_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "listings",
        "province",
        existing_type=sa.String(8),
        type_=sa.String(64),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "listings",
        "province",
        existing_type=sa.String(64),
        type_=sa.String(8),
        existing_nullable=True,
    )
