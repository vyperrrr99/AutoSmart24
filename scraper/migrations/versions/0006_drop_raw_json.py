from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006_drop_raw_json"
down_revision = "0005_detail_fields_dealers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("listings", "raw_detail")
    op.drop_column("listings", "raw_snippet")


def downgrade() -> None:
    op.add_column("listings", sa.Column("raw_snippet", JSONB(), nullable=True))
    op.add_column("listings", sa.Column("raw_detail", JSONB(), nullable=True))
