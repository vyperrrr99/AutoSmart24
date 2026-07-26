# HISTORICAL: this script populated the detail-fields/dealers schema added in
# migration 0005 from the raw_detail JSON that existed at the time. It cannot
# run anymore after migration 0006 dropped that column -- kept only as a
# record of how the one-time backfill was performed.
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from autosmart24.db.dealers import upsert_dealer
from autosmart24.db.models import Listing
from autosmart24.scraping.detail_mapper import map_detail_listing

logger = logging.getLogger(__name__)


def backfill_detail_fields(session: Session, batch_size: int = 500) -> int:
    """One-time migration: populate the new structured detail-page columns
    (and the dealers table) for every already-enriched listing, by re-reading
    the raw_detail JSON already stored in the database -- no new HTTP
    requests. Must run to completion and be verified BEFORE raw_detail is
    ever dropped (see the design spec's binding migration order)."""
    now = dt.datetime.utcnow()
    processed = 0
    last_id = ""

    while True:
        rows = session.execute(
            select(Listing)
            .where(Listing.detail_scraped.is_(True), Listing.raw_detail.is_not(None), Listing.id > last_id)
            .order_by(Listing.id)
            .limit(batch_size)
        ).scalars().all()
        if not rows:
            break

        for row in rows:
            mapped = map_detail_listing(row.raw_detail)
            row.had_accident = mapped["had_accident"]
            row.has_full_service_history = mapped["has_full_service_history"]
            row.gears = mapped["gears"]
            row.drive_train = mapped["drive_train"]
            row.cylinders = mapped["cylinders"]
            row.weight_kg = mapped["weight_kg"]
            row.co2_emissions_g_km = mapped["co2_emissions_g_km"]
            row.fuel_consumption_combined = mapped["fuel_consumption_combined"]
            row.fuel_consumption_urban = mapped["fuel_consumption_urban"]
            row.fuel_consumption_extra_urban = mapped["fuel_consumption_extra_urban"]
            row.emission_class = mapped["emission_class"]
            row.upholstery = mapped["upholstery"]
            row.upholstery_color = mapped["upholstery_color"]
            row.is_conditional_price = mapped["is_conditional_price"]
            row.interaction_count = mapped["interaction_count"]
            row.favorites_count = mapped["favorites_count"]
            row.new_driver_suitable = mapped["new_driver_suitable"]
            row.dealer_id = upsert_dealer(session, mapped["dealer"], now)
            processed += 1
            last_id = row.id

        session.commit()
        logger.info("Backfilled %d listings so far (last id=%s)", processed, last_id)

    return processed


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from autosmart24.db.session import make_engine, make_session_factory

    engine = make_engine()
    session_factory = make_session_factory(engine)
    session = session_factory()
    try:
        total = backfill_detail_fields(session)
        print(f"Backfill complete: {total} listings processed.")
    finally:
        session.close()
