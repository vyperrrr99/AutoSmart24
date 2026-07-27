from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_run_progress"
down_revision = "0006_drop_raw_json"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("scrape_runs", sa.Column("phase", sa.String(16), nullable=True))
    op.add_column("scrape_runs", sa.Column("search_finished_at", sa.DateTime(), nullable=True))
    op.add_column("scrape_runs", sa.Column("search_total", sa.Integer(), nullable=True))
    op.add_column("scrape_runs", sa.Column("detail_total", sa.Integer(), nullable=True))
    # server_default backfills the rows already in production; it is dropped
    # afterwards so the application-side default is the only source of truth.
    op.add_column(
        "scrape_runs",
        sa.Column("detail_enriched", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column("scrape_runs", "detail_enriched", server_default=None)


def downgrade() -> None:
    op.drop_column("scrape_runs", "detail_enriched")
    op.drop_column("scrape_runs", "detail_total")
    op.drop_column("scrape_runs", "search_total")
    op.drop_column("scrape_runs", "search_finished_at")
    op.drop_column("scrape_runs", "phase")
