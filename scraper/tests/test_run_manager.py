import datetime as dt

import pytest
from sqlalchemy.orm import sessionmaker

from autosmart24.config import BrandConfig
from autosmart24.db.models import Listing, PriceHistory, ScrapeEvent, ScrapeRun
from autosmart24.run_manager import process_detail_backlog, run_brand_sweep
from autosmart24.scraping.detail_queue import DetailResult
from autosmart24.scraping.http_client import BlockedError, RateLimitedClient

BRAND = BrandConfig(slug="fiat", make_id=28, display_name="Fiat")


def _client() -> RateLimitedClient:
    return RateLimitedClient(min_delay_seconds=0, max_delay_seconds=0, sleep_fn=lambda _: None)


def _fake_snippet(listing_id: str, price: int) -> dict:
    return {
        "id": listing_id,
        "cross_reference_id": listing_id,
        "brand": "Fiat",
        "model": "Panda",
        "model_group": "Panda",
        "variant": None,
        "motor_type_name": "1.0",
        "version_input": None,
        "transmission": "Manuale",
        "fuel": "Benzina",
        "first_registration": dt.date(2020, 1, 1),
        "mileage_km": 50000,
        "seller_type": "Dealer",
        "seller_company_name": "Test Dealer",
        "city": "Roma - Roma - RM",
        "zip_code": "00100",
        "price": price,
        "url": f"https://www.autoscout24.it/annunci/{listing_id}",
        "raw_snippet": {"id": listing_id},
    }


def _existing_listing(listing_id: str, price: int, detail_scraped: bool = True) -> Listing:
    now = dt.datetime.utcnow()
    return Listing(
        id=listing_id, brand="Fiat", price=price, url=f"https://www.autoscout24.it/annunci/{listing_id}",
        first_seen_at=now, last_seen_at=now, last_checked_at=now, status="active", detail_scraped=detail_scraped,
    )


def test_run_brand_sweep_records_new_listing(db_session):
    def fake_crawl(client, brand_slug, make_id):
        yield _fake_snippet("new-1", 15000)

    run = run_brand_sweep(db_session, _client(), BRAND, crawl_fn=fake_crawl)

    assert run.status == "success"
    assert run.new_listings == 1
    listing = db_session.get(Listing, "new-1")
    assert listing is not None
    assert listing.status == "active"
    assert listing.price == 15000
    history = db_session.query(PriceHistory).filter_by(listing_id="new-1").all()
    assert len(history) == 1


def test_run_brand_sweep_detects_price_change(db_session):
    db_session.add(_existing_listing("existing-1", 10000))
    db_session.commit()

    def fake_crawl(client, brand_slug, make_id):
        yield _fake_snippet("existing-1", 12000)

    run = run_brand_sweep(db_session, _client(), BRAND, crawl_fn=fake_crawl)

    assert run.price_changes == 1
    listing = db_session.get(Listing, "existing-1")
    assert listing.price == 12000
    prices = [h.price for h in db_session.query(PriceHistory).filter_by(listing_id="existing-1").all()]
    assert 12000 in prices


def test_run_brand_sweep_confirms_sold_when_detail_confirms(db_session):
    db_session.add(_existing_listing("missing-1", 10000))
    db_session.commit()

    def fake_crawl(client, brand_slug, make_id):
        return iter(())

    def fake_fetch_detail(client, url):
        return DetailResult(sold=True)

    run = run_brand_sweep(db_session, _client(), BRAND, crawl_fn=fake_crawl, fetch_detail_fn=fake_fetch_detail)

    assert run.sold_detected == 1
    listing = db_session.get(Listing, "missing-1")
    assert listing.status == "sold"
    assert listing.sold_at is not None


def test_run_brand_sweep_keeps_active_when_detail_still_active(db_session):
    db_session.add(_existing_listing("anomaly-1", 10000))
    db_session.commit()

    def fake_crawl(client, brand_slug, make_id):
        return iter(())

    def fake_fetch_detail(client, url):
        return DetailResult(sold=False, data={"source_status": "Active"})

    run = run_brand_sweep(db_session, _client(), BRAND, crawl_fn=fake_crawl, fetch_detail_fn=fake_fetch_detail)

    assert run.sold_detected == 0
    listing = db_session.get(Listing, "anomaly-1")
    assert listing.status == "active"
    events = db_session.query(ScrapeEvent).filter_by(brand="Fiat").all()
    assert any(e.level == "warning" for e in events)
    assert run.errors_count == 1


def test_run_brand_sweep_marks_blocked_on_blocked_error(db_session):
    def fake_crawl(client, brand_slug, make_id):
        yield _fake_snippet("x-1", 1000)
        raise BlockedError(403, "https://www.autoscout24.it/lst/fiat")

    run = run_brand_sweep(db_session, _client(), BRAND, crawl_fn=fake_crawl)

    assert run.status == "blocked"
    assert db_session.get(Listing, "x-1") is None


def test_run_brand_sweep_enriches_pending_detail_backlog(db_session):
    db_session.add(_existing_listing("pending-1", 10000, detail_scraped=False))
    db_session.commit()

    def fake_crawl(client, brand_slug, make_id):
        yield _fake_snippet("pending-1", 10000)

    def fake_fetch_detail(client, url):
        return DetailResult(
            sold=False,
            data={
                "price": 10500, "power_kw": 74, "power_cv": 101, "displacement_ccm": 1199,
                "body_type": "Berlina", "body_color": None, "num_seats": 5, "num_doors": 5,
                "num_previous_owners": None, "province": "TO", "latitude": 44.8, "longitude": 7.3,
                "vat_exposed": False, "price_evaluation_category": 1, "price_evaluation_median": 16100,
                "created_at_source": dt.datetime.utcnow(), "raw_detail": {"id": "pending-1"},
            },
        )

    run_brand_sweep(db_session, _client(), BRAND, crawl_fn=fake_crawl, fetch_detail_fn=fake_fetch_detail)

    listing = db_session.get(Listing, "pending-1")
    assert listing.detail_scraped is True
    assert listing.power_kw == 74
    assert listing.price == 10500


def test_run_brand_sweep_excludes_same_run_new_listings_from_backlog(db_session):
    called_with: list[str] = []

    def fake_crawl(client, brand_slug, make_id):
        yield _fake_snippet("brand-new-1", 9000)

    def fake_fetch_detail(client, url):
        called_with.append(url)
        return DetailResult(sold=True)

    run_brand_sweep(db_session, _client(), BRAND, crawl_fn=fake_crawl, fetch_detail_fn=fake_fetch_detail)

    listing = db_session.get(Listing, "brand-new-1")
    assert listing is not None
    assert listing.detail_scraped is False
    assert listing.status == "active"
    assert listing.url not in called_with


def test_run_brand_sweep_marks_blocked_and_stops_on_block_during_missing_ids_loop(db_session):
    db_session.add(_existing_listing("missing-a", 10000))
    db_session.add(_existing_listing("missing-b", 20000))
    db_session.commit()

    call_count = {"n": 0}

    def fake_crawl(client, brand_slug, make_id):
        return iter(())

    def fake_fetch_detail(client, url):
        call_count["n"] += 1
        raise BlockedError(403, url)

    run = run_brand_sweep(db_session, _client(), BRAND, crawl_fn=fake_crawl, fetch_detail_fn=fake_fetch_detail)

    assert run.status == "blocked"
    assert call_count["n"] == 1


def test_run_brand_sweep_marks_blocked_on_block_during_detail_backlog(db_session):
    db_session.add(_existing_listing("pending-blocked", 10000, detail_scraped=False))
    db_session.commit()

    def fake_crawl(client, brand_slug, make_id):
        yield _fake_snippet("pending-blocked", 10000)

    def fake_fetch_detail(client, url):
        raise BlockedError(403, url)

    run = run_brand_sweep(db_session, _client(), BRAND, crawl_fn=fake_crawl, fetch_detail_fn=fake_fetch_detail)

    assert run.status == "blocked"


def test_run_brand_sweep_errors_count_reflects_anomalies(db_session):
    db_session.add(_existing_listing("anomaly-a", 10000))
    db_session.add(_existing_listing("anomaly-b", 20000))
    db_session.commit()

    def fake_crawl(client, brand_slug, make_id):
        return iter(())

    def fake_fetch_detail(client, url):
        return DetailResult(sold=False, data={"source_status": "Active"})

    run = run_brand_sweep(db_session, _client(), BRAND, crawl_fn=fake_crawl, fetch_detail_fn=fake_fetch_detail)

    assert run.errors_count == 2


def test_process_detail_backlog_returns_sold_count(db_session):
    db_session.add(_existing_listing("backlog-sold-1", 10000, detail_scraped=False))
    db_session.commit()
    run = ScrapeRun(brand="Fiat", started_at=dt.datetime.utcnow(), status="running")
    db_session.add(run)
    db_session.flush()

    def fake_fetch_detail(client, url):
        return DetailResult(sold=True)

    sold = process_detail_backlog(db_session, _client(), BRAND, run, fetch_detail_fn=fake_fetch_detail)

    assert sold == 1


def test_run_brand_sweep_counts_backlog_confirmed_sold_in_sold_detected(db_session):
    # Listing is still present in the current sweep (so it does NOT go through the
    # missing_ids/sold-confirmation loop) but hasn't had its detail page scraped yet,
    # so it is picked up by the detail backlog pass, where the detail page reveals
    # it as sold.
    db_session.add(_existing_listing("backlog-sold-2", 10000, detail_scraped=False))
    db_session.commit()

    def fake_crawl(client, brand_slug, make_id):
        yield _fake_snippet("backlog-sold-2", 10000)

    def fake_fetch_detail(client, url):
        return DetailResult(sold=True)

    run = run_brand_sweep(db_session, _client(), BRAND, crawl_fn=fake_crawl, fetch_detail_fn=fake_fetch_detail)

    assert run.sold_detected == 1
    listing = db_session.get(Listing, "backlog-sold-2")
    assert listing.status == "sold"


def test_run_brand_sweep_commits_scrape_run_before_crawling(db_session):
    """The ScrapeRun row (status='running') must be visible to another DB
    connection as soon as the sweep starts, not only once the whole sweep
    finishes. We assert this from inside the crawl generator itself, before
    it has yielded anything, using a second session on the same engine."""

    def fake_crawl(client, brand_slug, make_id):
        other = sessionmaker(bind=db_session.bind)()
        try:
            run = other.query(ScrapeRun).filter_by(brand="Fiat").one()
            assert run.status == "running"
        finally:
            other.close()
        yield _fake_snippet("early-visible-1", 5000)

    run = run_brand_sweep(db_session, _client(), BRAND, crawl_fn=fake_crawl)

    assert run.status == "success"


def test_run_brand_sweep_commits_each_batch_incrementally(db_session):
    """With a small batch_size, earlier batches must be committed (and thus
    visible via another session) before the crawl generator has finished
    yielding all listings."""

    def fake_crawl(client, brand_slug, make_id):
        yield _fake_snippet("batch-1", 1000)
        yield _fake_snippet("batch-2", 2000)

        other = sessionmaker(bind=db_session.bind)()
        try:
            ids = {row.id for row in other.query(Listing).filter_by(brand="Fiat").all()}
            assert ids == {"batch-1", "batch-2"}
        finally:
            other.close()

        yield _fake_snippet("batch-3", 3000)
        yield _fake_snippet("batch-4", 4000)
        yield _fake_snippet("batch-5", 5000)

    run = run_brand_sweep(db_session, _client(), BRAND, crawl_fn=fake_crawl, batch_size=2)

    assert run.status == "success"
    assert run.new_listings == 5
    all_ids = {row.id for row in db_session.query(Listing).filter_by(brand="Fiat").all()}
    assert all_ids == {"batch-1", "batch-2", "batch-3", "batch-4", "batch-5"}


def test_listing_accepts_cross_reference_id_longer_than_32_chars(db_session):
    """cross_reference_id is opaque, dealer/site-supplied data with no length
    guarantee from us; the column was widened from VARCHAR(32) to
    VARCHAR(128) after a real dealer id exceeded 32 chars and crashed a
    production sweep. This guards against the ORM model regressing back to
    a too-narrow column."""
    now = dt.datetime.utcnow()
    long_id = "x" * 100
    assert len(long_id) > 32

    db_session.add(
        Listing(
            id="cross-ref-long-1",
            cross_reference_id=long_id,
            brand="Fiat",
            price=15000,
            url="https://www.autoscout24.it/annunci/cross-ref-long-1",
            first_seen_at=now,
            last_seen_at=now,
            last_checked_at=now,
            status="active",
            detail_scraped=False,
        )
    )
    db_session.commit()

    fetched = db_session.get(Listing, "cross-ref-long-1")
    assert fetched.cross_reference_id == long_id


def test_run_brand_sweep_marks_error_and_preserves_partial_state_on_unexpected_exception(db_session):
    """An unexpected (non-BlockedError) exception raised mid-sweep -- after
    at least one batch has already been committed -- must not leave the
    ScrapeRun stuck at status='running' forever (a "zombie" run,
    indistinguishable on the dashboard from a legitimately long-running
    sweep). It should be marked status='error', with partial counters and
    the already-committed listings preserved, and the original exception
    must still propagate for operator visibility in logs."""

    def fake_crawl(client, brand_slug, make_id):
        yield _fake_snippet("safe-1", 1000)
        yield _fake_snippet("safe-2", 2000)
        raise ValueError("boom - unexpected crawler failure")

    with pytest.raises(ValueError, match="boom"):
        run_brand_sweep(db_session, _client(), BRAND, crawl_fn=fake_crawl, batch_size=2)

    run = db_session.query(ScrapeRun).filter_by(brand="Fiat").one()
    assert run.status == "error"
    assert run.finished_at is not None
    assert run.listings_seen == 2
    assert run.new_listings == 2
    assert run.errors_count == 1

    surviving_ids = {row.id for row in db_session.query(Listing).filter_by(brand="Fiat").all()}
    assert surviving_ids == {"safe-1", "safe-2"}

    events = db_session.query(ScrapeEvent).filter_by(brand="Fiat").all()
    assert any(e.level == "error" and "boom" in e.message for e in events)


def test_run_brand_sweep_preserves_committed_batches_on_block(db_session):
    """A BlockedError raised mid-crawl (after at least one full batch has
    already been committed) must leave that batch's listings durably in the
    DB and reflected in the run's partial counters, instead of discarding
    the whole sweep."""

    def fake_crawl(client, brand_slug, make_id):
        yield _fake_snippet("survivor-1", 1000)
        yield _fake_snippet("survivor-2", 2000)
        raise BlockedError(403, "https://www.autoscout24.it/lst/fiat")

    run = run_brand_sweep(db_session, _client(), BRAND, crawl_fn=fake_crawl, batch_size=2)

    assert run.status == "blocked"
    assert run.new_listings == 2
    assert run.listings_seen == 2
    surviving_ids = {row.id for row in db_session.query(Listing).filter_by(brand="Fiat").all()}
    assert surviving_ids == {"survivor-1", "survivor-2"}
