import datetime as dt
import json
from pathlib import Path

import pytest

from autosmart24.db.backfill_detail_fields import backfill_detail_fields
from autosmart24.db.models import Dealer, Listing
from autosmart24.scraping.next_data import extract_next_data

FIXTURES = Path(__file__).parent / "fixtures"


def _real_listing_details() -> dict:
    html = (FIXTURES / "detail_fiat_grande_panda.html").read_text(encoding="utf-8")
    data = extract_next_data(html)
    return data["props"]["pageProps"]["listingDetails"]


def _enriched_listing(listing_id: str, raw_detail: dict, detail_scraped: bool = True) -> Listing:
    now = dt.datetime.utcnow()
    return Listing(
        id=listing_id, brand="Fiat", url=f"https://www.autoscout24.it/annunci/{listing_id}",
        first_seen_at=now, last_seen_at=now, last_checked_at=now, status="active",
        detail_scraped=detail_scraped, raw_detail=raw_detail,
    )


def test_backfill_populates_new_fields_from_stored_raw_detail(db_session):
    ld = _real_listing_details()
    db_session.add(_enriched_listing("bf-1", ld))
    db_session.commit()

    processed = backfill_detail_fields(db_session)

    assert processed == 1
    row = db_session.get(Listing, "bf-1")
    assert row.had_accident is False
    assert row.gears == 6
    assert row.drive_train == "Anteriore"
    assert row.weight_kg == 1159
    assert row.emission_class == "Euro 6d"
    assert row.is_conditional_price is True
    assert row.interaction_count == 10670
    assert row.new_driver_suitable is True


def test_backfill_upserts_the_dealer_and_links_the_listing(db_session):
    ld = _real_listing_details()
    db_session.add(_enriched_listing("bf-2", ld))
    db_session.commit()

    backfill_detail_fields(db_session)

    row = db_session.get(Listing, "bf-2")
    assert row.dealer_id == 46936034
    dealer = db_session.get(Dealer, 46936034)
    assert dealer is not None
    assert dealer.company_name == "Puntocar di Tarantino Andrea - Bricherasio"


def test_backfill_skips_listings_without_raw_detail(db_session):
    now = dt.datetime.utcnow()
    db_session.add(Listing(
        id="bf-no-detail", brand="Fiat", url="https://www.autoscout24.it/annunci/bf-no-detail",
        first_seen_at=now, last_seen_at=now, last_checked_at=now, status="active",
        detail_scraped=False, raw_detail=None,
    ))
    db_session.commit()

    processed = backfill_detail_fields(db_session)

    assert processed == 0
    row = db_session.get(Listing, "bf-no-detail")
    assert row.gears is None


def test_backfill_is_idempotent(db_session):
    ld = _real_listing_details()
    db_session.add(_enriched_listing("bf-3", ld))
    db_session.commit()

    first = backfill_detail_fields(db_session)
    second = backfill_detail_fields(db_session)

    assert first == 1
    assert second == 1  # re-processes the same row; must not error or duplicate
    dealers = db_session.query(Dealer).filter_by(id=46936034).all()
    assert len(dealers) == 1


def test_backfill_paginates_across_multiple_batches(db_session):
    ld = _real_listing_details()
    for suffix in ("a", "b", "c"):
        listing_id = f"bf-page-{suffix}"
        raw = dict(ld)
        raw["id"] = listing_id
        db_session.add(_enriched_listing(listing_id, raw))
    db_session.commit()

    processed = backfill_detail_fields(db_session, batch_size=1)

    assert processed == 3
    for suffix in ("a", "b", "c"):
        row = db_session.get(Listing, f"bf-page-{suffix}")
        assert row.gears == 6
