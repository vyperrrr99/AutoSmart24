import datetime as dt
import json

import httpx
import pytest
import respx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from autosmart24.config import BrandConfig
from autosmart24.db.models import Dealer, Listing, PriceHistory, ScrapeEvent, ScrapeRun
from autosmart24.run_manager import process_detail_backlog, run_brand_sweep
from autosmart24.scraping.detail_queue import DetailResult
from autosmart24.scraping.http_client import BlockedError, RateLimitedClient
from autosmart24.scraping.search_query import build_search_url

BRAND = BrandConfig(slug="fiat", make_id=28, display_name="Fiat")


def _next_data_html(page_props: dict) -> str:
    payload = {"props": {"pageProps": page_props}}
    return f'<html><body><script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script></body></html>'


def _fake_listing(listing_id: str, price: int) -> dict:
    return {
        "id": listing_id,
        "crossReferenceId": listing_id,
        "url": f"/annunci/{listing_id}",
        "price": {"priceRaw": price},
        "vehicle": {
            "make": "Fiat",
            "model": "Panda",
            "modelGroup": "Panda",
            "variant": None,
            "motorTypeName": "1.0",
            "modelVersionInput": None,
            "transmission": "Manuale",
            "fuel": "Benzina",
        },
        "location": {"city": "Roma - Roma - RM", "zip": "00100"},
        "seller": {"type": "Dealer", "companyName": "Test Dealer"},
        "tracking": {"firstRegistration": "01-2020", "mileage": "50000"},
    }


def _client() -> RateLimitedClient:
    return RateLimitedClient(min_delay_seconds=0, max_delay_seconds=0, sleep_fn=lambda _: None)


def _fake_snippet(listing_id: str, price: int, brand: str = "Fiat") -> dict:
    return {
        "id": listing_id,
        "cross_reference_id": listing_id,
        "brand": brand,
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
    }


def _existing_listing(
    listing_id: str,
    price: int,
    detail_scraped: bool = True,
    status: str = "active",
    sold_at: dt.datetime | None = None,
) -> Listing:
    now = dt.datetime.utcnow()
    return Listing(
        id=listing_id, brand="Fiat", price=price, url=f"https://www.autoscout24.it/annunci/{listing_id}",
        first_seen_at=now, last_seen_at=now, last_checked_at=now, status=status, sold_at=sold_at,
        detail_scraped=detail_scraped,
    )


def _fake_detail_data(listing_id: str) -> dict:
    return {
        "price": None, "power_kw": None, "power_cv": None, "displacement_ccm": None,
        "body_type": None, "body_color": None, "num_seats": None, "num_doors": None,
        "num_previous_owners": None, "province": None, "latitude": None, "longitude": None,
        "vat_exposed": None, "price_evaluation_category": None, "price_evaluation_median": None,
        "created_at_source": None,
        "had_accident": None, "has_full_service_history": None, "gears": None, "drive_train": None,
        "cylinders": None, "weight_kg": None, "co2_emissions_g_km": None,
        "fuel_consumption_combined": None, "fuel_consumption_urban": None, "fuel_consumption_extra_urban": None,
        "emission_class": None, "upholstery": None, "upholstery_color": None,
        "is_conditional_price": None, "interaction_count": None, "favorites_count": None,
        "new_driver_suitable": None, "dealer": None,
    }


def _noop_fetch_detail(client, url):
    """Detail fetch that always reports 'still active' and enriches nothing.
    Used by tests that reach the detail phase but do not assert on its results --
    without it those tests would issue real network requests."""
    return DetailResult(sold=False, data=_fake_detail_data(url))


def test_run_brand_sweep_records_new_listing(db_session):
    def fake_crawl(client, brand_slug, make_id, **kwargs):
        yield _fake_snippet("new-1", 15000)

    run = run_brand_sweep(db_session, _client, BRAND, crawl_fn=fake_crawl, fetch_detail_fn=_noop_fetch_detail)

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

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        yield _fake_snippet("existing-1", 12000)

    run = run_brand_sweep(db_session, _client, BRAND, crawl_fn=fake_crawl)

    assert run.price_changes == 1
    listing = db_session.get(Listing, "existing-1")
    assert listing.price == 12000
    prices = [h.price for h in db_session.query(PriceHistory).filter_by(listing_id="existing-1").all()]
    assert 12000 in prices


def test_run_brand_sweep_relists_a_previously_sold_listing_that_reappears(db_session):
    """Live incident: a listing marked status='sold' by an earlier run's
    sold-confirmation logic reappears, still active, in a later sweep's
    crawl results. diff_sweep's active_db_prices is scoped to status='active'
    rows only, so this listing is invisible to it and lands in diff.new_ids --
    but its primary key already exists in the table, so treating it as a
    fresh INSERT crashes with a UniqueViolation (reproducing the real
    psycopg.errors.UniqueViolation seen in production). It must instead be
    updated back to active in place, and must NOT be counted as a new
    listing."""
    sold_at = dt.datetime.utcnow() - dt.timedelta(days=3)
    db_session.add(_existing_listing("relist-1", 10000, status="sold", sold_at=sold_at))
    db_session.commit()

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        yield _fake_snippet("relist-1", 11000)

    run = run_brand_sweep(db_session, _client, BRAND, crawl_fn=fake_crawl, fetch_detail_fn=_noop_fetch_detail)

    assert run.status == "success"
    assert run.new_listings == 0

    listing = db_session.get(Listing, "relist-1")
    assert listing.status == "active"
    assert listing.sold_at is None
    assert listing.price == 11000

    prices = [h.price for h in db_session.query(PriceHistory).filter_by(listing_id="relist-1").all()]
    assert 11000 in prices


def test_run_brand_sweep_confirms_sold_when_detail_confirms(db_session):
    db_session.add(_existing_listing("missing-1", 10000))
    db_session.commit()

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        return iter(())

    def fake_fetch_detail(client, url):
        return DetailResult(sold=True)

    run = run_brand_sweep(db_session, _client, BRAND, crawl_fn=fake_crawl, fetch_detail_fn=fake_fetch_detail)

    assert run.sold_detected == 1
    listing = db_session.get(Listing, "missing-1")
    assert listing.status == "sold"
    assert listing.sold_at is not None


def test_run_brand_sweep_keeps_active_when_detail_still_active(db_session):
    db_session.add(_existing_listing("anomaly-1", 10000))
    db_session.commit()

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        return iter(())

    def fake_fetch_detail(client, url):
        return DetailResult(sold=False, data={"source_status": "Active"})

    run = run_brand_sweep(db_session, _client, BRAND, crawl_fn=fake_crawl, fetch_detail_fn=fake_fetch_detail)

    assert run.sold_detected == 0
    listing = db_session.get(Listing, "anomaly-1")
    assert listing.status == "active"
    events = db_session.query(ScrapeEvent).filter_by(brand="Fiat").all()
    assert any(e.level == "warning" for e in events)
    assert run.errors_count == 1


def test_run_brand_sweep_marks_blocked_on_blocked_error(db_session):
    def fake_crawl(client, brand_slug, make_id, **kwargs):
        yield _fake_snippet("x-1", 1000)
        raise BlockedError(403, "https://www.autoscout24.it/lst/fiat")

    run = run_brand_sweep(db_session, _client, BRAND, crawl_fn=fake_crawl)

    assert run.status == "blocked"
    assert db_session.get(Listing, "x-1") is None


def test_run_brand_sweep_enriches_pending_detail_backlog(db_session):
    db_session.add(_existing_listing("pending-1", 10000, detail_scraped=False))
    db_session.commit()

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        yield _fake_snippet("pending-1", 10000)

    def fake_fetch_detail(client, url):
        return DetailResult(
            sold=False,
            data={
                "price": 10500, "power_kw": 74, "power_cv": 101, "displacement_ccm": 1199,
                "body_type": "Berlina", "body_color": None, "num_seats": 5, "num_doors": 5,
                "num_previous_owners": None, "province": "TO", "latitude": 44.8, "longitude": 7.3,
                "vat_exposed": False, "price_evaluation_category": 1, "price_evaluation_median": 16100,
                "created_at_source": dt.datetime.utcnow(),
                "had_accident": None, "has_full_service_history": None, "gears": 6, "drive_train": "Anteriore",
                "cylinders": 3, "weight_kg": 1159, "co2_emissions_g_km": None,
                "fuel_consumption_combined": None, "fuel_consumption_urban": None, "fuel_consumption_extra_urban": None,
                "emission_class": "Euro 6d", "upholstery": "Altro", "upholstery_color": None,
                "is_conditional_price": True, "interaction_count": 500, "favorites_count": 20,
                "new_driver_suitable": True, "dealer": None,
            },
        )

    run_brand_sweep(db_session, _client, BRAND, crawl_fn=fake_crawl, fetch_detail_fn=fake_fetch_detail)

    listing = db_session.get(Listing, "pending-1")
    assert listing.detail_scraped is True
    assert listing.power_kw == 74
    assert listing.price == 10500


def test_run_brand_sweep_fetches_detail_for_listings_new_in_this_same_sweep(db_session):
    """Binding requirement: a listing not already in the DB must have its detail
    page fetched during the very sweep that discovers it, not the next one."""
    fetched_urls: list[str] = []

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        yield _fake_snippet("brand-new-1", 9000)

    def fake_fetch_detail(client, url):
        fetched_urls.append(url)
        return DetailResult(sold=False, data=_fake_detail_data("brand-new-1"))

    run_brand_sweep(db_session, _client, BRAND, crawl_fn=fake_crawl, fetch_detail_fn=fake_fetch_detail)

    listing = db_session.get(Listing, "brand-new-1")
    assert listing.detail_scraped is True
    assert listing.url in fetched_urls


def test_run_brand_sweep_marks_blocked_and_stops_on_block_during_missing_ids_loop(db_session):
    db_session.add(_existing_listing("missing-a", 10000))
    db_session.add(_existing_listing("missing-b", 20000))
    db_session.commit()

    call_count = {"n": 0}

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        return iter(())

    def fake_fetch_detail(client, url):
        call_count["n"] += 1
        raise BlockedError(403, url)

    run = run_brand_sweep(db_session, _client, BRAND, crawl_fn=fake_crawl, fetch_detail_fn=fake_fetch_detail)

    assert run.status == "blocked"
    assert call_count["n"] == 1


def test_run_brand_sweep_marks_blocked_on_block_during_detail_backlog(db_session):
    db_session.add(_existing_listing("pending-blocked", 10000, detail_scraped=False))
    db_session.commit()

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        yield _fake_snippet("pending-blocked", 10000)

    def fake_fetch_detail(client, url):
        raise BlockedError(403, url)

    run = run_brand_sweep(db_session, _client, BRAND, crawl_fn=fake_crawl, fetch_detail_fn=fake_fetch_detail)

    assert run.status == "blocked"
    events = db_session.query(ScrapeEvent).filter_by(brand="Fiat").all()
    assert any(e.level == "blocked" for e in events)
    assert any(e.level == "info" and e.message.startswith("Detail backlog page:") for e in events)


def test_run_brand_sweep_errors_count_reflects_anomalies(db_session):
    db_session.add(_existing_listing("anomaly-a", 10000))
    db_session.add(_existing_listing("anomaly-b", 20000))
    db_session.commit()

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        return iter(())

    def fake_fetch_detail(client, url):
        return DetailResult(sold=False, data={"source_status": "Active"})

    run = run_brand_sweep(db_session, _client, BRAND, crawl_fn=fake_crawl, fetch_detail_fn=fake_fetch_detail)

    assert run.errors_count == 2


def test_process_detail_backlog_reports_removals_without_selling(db_session):
    """A detail page reporting removal no longer sells the listing: it is in the
    backlog because the search results just showed it alive. The count returned
    is diagnostic only."""
    db_session.add(_existing_listing("backlog-sold-1", 10000, detail_scraped=False))
    db_session.commit()
    run = ScrapeRun(brand="Fiat", started_at=dt.datetime.utcnow(), status="running")
    db_session.add(run)
    db_session.flush()

    def fake_fetch_detail(client, url):
        return DetailResult(sold=True)

    reported = process_detail_backlog(db_session, _client, BRAND, run, fetch_detail_fn=fake_fetch_detail)

    assert reported == 1
    listing = db_session.get(Listing, "backlog-sold-1")
    assert listing.status == "active"
    assert listing.detail_scraped is False


def test_run_brand_sweep_does_not_count_backlog_removals_as_sales(db_session):
    """The listing IS present in the current sweep, so it never reaches the
    missing_ids path; the backlog pass sees its detail page report a removal.
    Before 2026-07-28 that marked it sold, which produced 139 false sales in a
    single Lancia run. It must now stay active."""
    db_session.add(_existing_listing("backlog-sold-2", 10000, detail_scraped=False))
    db_session.commit()

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        yield _fake_snippet("backlog-sold-2", 10000)

    def fake_fetch_detail(client, url):
        return DetailResult(sold=True)

    run = run_brand_sweep(db_session, _client, BRAND, crawl_fn=fake_crawl, fetch_detail_fn=fake_fetch_detail)

    assert run.sold_detected == 0
    listing = db_session.get(Listing, "backlog-sold-2")
    assert listing.status == "active"


def test_run_brand_sweep_commits_scrape_run_before_crawling(db_session):
    """The ScrapeRun row (status='running') must be visible to another DB
    connection as soon as the sweep starts, not only once the whole sweep
    finishes. We assert this from inside the crawl generator itself, before
    it has yielded anything, using a second session on the same engine."""

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        other = sessionmaker(bind=db_session.bind)()
        try:
            run = other.query(ScrapeRun).filter_by(brand="Fiat").one()
            assert run.status == "running"
        finally:
            other.close()
        yield _fake_snippet("early-visible-1", 5000)

    run = run_brand_sweep(db_session, _client, BRAND, crawl_fn=fake_crawl, fetch_detail_fn=_noop_fetch_detail)

    assert run.status == "success"


def test_run_brand_sweep_commits_each_batch_incrementally(db_session):
    """With a small batch_size, earlier batches must be committed (and thus
    visible via another session) before the crawl generator has finished
    yielding all listings."""

    def fake_crawl(client, brand_slug, make_id, **kwargs):
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

    run = run_brand_sweep(db_session, _client, BRAND, crawl_fn=fake_crawl, batch_size=2, fetch_detail_fn=_noop_fetch_detail)

    assert run.status == "success"
    assert run.new_listings == 5
    all_ids = {row.id for row in db_session.query(Listing).filter_by(brand="Fiat").all()}
    assert all_ids == {"batch-1", "batch-2", "batch-3", "batch-4", "batch-5"}


def test_run_brand_sweep_survives_a_batch_whose_commit_fails(db_session, monkeypatch):
    """Id reuse is the write failure we know about, not the only one possible.
    A malformed value or a future schema change must cost its batch, not the
    28,000 listings around it."""
    calls = {"n": 0}
    real_commit = db_session.commit

    def flaky_commit():
        calls["n"] += 1
        if calls["n"] == 2:
            raise IntegrityError("boom", None, Exception("synthetic"))
        return real_commit()

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        for i in range(10):
            yield _fake_snippet(f"item-{i}", 1000 + i)

    monkeypatch.setattr(db_session, "commit", flaky_commit)
    run = run_brand_sweep(
        db_session, _client, BRAND, crawl_fn=fake_crawl,
        fetch_detail_fn=_noop_fetch_detail,
        concurrency=1, batch_size=5,
    )
    monkeypatch.undo()

    assert run.status in ("success", "partial"), "the sweep must reach the end"
    assert run.finished_at is not None
    messages = [e.message for e in db_session.query(ScrapeEvent).all()]
    assert any("lotto" in m.lower() for m in messages)


def test_a_dropped_batch_does_not_send_its_listings_down_the_missing_path(db_session, monkeypatch):
    """The listings of a dropped batch were genuinely on the site. Letting them
    fall into missing_ids would hand them to the one code path that can declare
    a sale -- reopening, from a new direction, the hole closed on 29/07."""
    db_session.add(_existing_listing("item-0", 1000, detail_scraped=True))
    db_session.commit()

    calls = {"n": 0}
    real_commit = db_session.commit

    def flaky_commit():
        calls["n"] += 1
        if calls["n"] == 2:
            raise IntegrityError("boom", None, Exception("synthetic"))
        return real_commit()

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        for i in range(10):
            yield _fake_snippet(f"item-{i}", 1000 + i)

    # run_worker_pool isolates a non-BlockedError raised by a job (see
    # concurrency.py): it never escapes to fail this test on its own, it just
    # drops that job silently. So the check that matters is not "did this
    # raise" but "was item-0 ever handed to the missing-listing fetch" --
    # recorded here and asserted below.
    checked_ids: list[str] = []

    def fetch_detail_fn(client, url):
        checked_ids.append(url.rsplit("/", 1)[-1])
        raise AssertionError("a listing seen on the site must not be checked as missing")

    monkeypatch.setattr(db_session, "commit", flaky_commit)
    run = run_brand_sweep(
        db_session, _client, BRAND, crawl_fn=fake_crawl,
        fetch_detail_fn=fetch_detail_fn, concurrency=1, batch_size=5,
    )
    monkeypatch.undo()

    assert run.status in ("success", "partial"), "the sweep must reach the end"
    assert run.finished_at is not None
    assert "item-0" not in checked_ids, "a listing seen on the site must not be checked as missing"
    assert db_session.get(Listing, "item-0").status == "active"


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


def test_listing_accepts_province_longer_than_8_chars(db_session):
    """province was VARCHAR(8), sized assuming a 2-letter "sigla" (e.g.
    "TO", "MI"). A real production sweep crashed with
    StringDataRightTruncation when a provincial-capital listing's
    "Comune - Provincia - Sigla" city string had no separate short sigla
    segment, so parts[-1] was a full province name (e.g. "Campobasso", 10
    chars) instead. The column was widened to VARCHAR(64). This guards
    against the ORM model regressing back to a too-narrow column.

    Note: SQLite (used for this test DB) does not enforce VARCHAR(n)
    length, so this test cannot go RED against the old String(8) column --
    same caveat as the cross_reference_id precedent above. The
    authoritative proof is the migration applied and verified against the
    real Postgres instance."""
    now = dt.datetime.utcnow()
    long_province = "x" * 20
    assert len(long_province) > 8

    db_session.add(
        Listing(
            id="province-long-1",
            province=long_province,
            brand="Fiat",
            price=15000,
            url="https://www.autoscout24.it/annunci/province-long-1",
            first_seen_at=now,
            last_seen_at=now,
            last_checked_at=now,
            status="active",
            detail_scraped=False,
        )
    )
    db_session.commit()

    fetched = db_session.get(Listing, "province-long-1")
    assert fetched.province == long_province


def test_run_brand_sweep_marks_error_and_preserves_partial_state_on_unexpected_exception(db_session):
    """An unexpected (non-BlockedError) exception raised mid-sweep -- after
    at least one batch has already been committed -- must not leave the
    ScrapeRun stuck at status='running' forever (a "zombie" run,
    indistinguishable on the dashboard from a legitimately long-running
    sweep). It should be marked status='error', with partial counters and
    the already-committed listings preserved, and the original exception
    must still propagate for operator visibility in logs."""

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        yield _fake_snippet("safe-1", 1000)
        yield _fake_snippet("safe-2", 2000)
        raise ValueError("boom - unexpected crawler failure")

    with pytest.raises(ValueError, match="boom"):
        run_brand_sweep(db_session, _client, BRAND, crawl_fn=fake_crawl, batch_size=2)

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

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        yield _fake_snippet("survivor-1", 1000)
        yield _fake_snippet("survivor-2", 2000)
        raise BlockedError(403, "https://www.autoscout24.it/lst/fiat")

    run = run_brand_sweep(db_session, _client, BRAND, crawl_fn=fake_crawl, batch_size=2)

    assert run.status == "blocked"
    assert run.new_listings == 2
    assert run.listings_seen == 2
    surviving_ids = {row.id for row in db_session.query(Listing).filter_by(brand="Fiat").all()}
    assert surviving_ids == {"survivor-1", "survivor-2"}


def test_run_brand_sweep_threads_year_from_concurrency_and_session_refresh_requests_to_crawl_fn(db_session):
    received = {}

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        received.update(kwargs)
        return iter([])

    run_brand_sweep(
        db_session, _client, BRAND, crawl_fn=fake_crawl,
        year_from=2021, concurrency=4, session_refresh_requests=17,
    )

    assert received["year_from"] == 2021
    assert received["concurrency"] == 4
    assert received["session_refresh_requests"] == 17


@respx.mock
def test_run_brand_sweep_works_end_to_end_with_the_real_crawl_brand(db_session):
    """Exercises the real (default) crawl_fn=crawl_brand, unlike every other
    test in this module which injects a fake. This is the only test that goes
    through the genuine run_brand_sweep -> crawl_brand -> run_worker_pool ->
    RateLimitedClient path, so a future signature drift between run_manager.py
    and crawler.py/concurrency.py (like the one that broke api/app.py's
    production call site) fails here instead of only in production."""
    discovery_page_props = {
        "numberOfResults": 1,
        "numberOfPages": 1,
        "listings": [_fake_listing("e2e-discovery-1", 1000)],
        "taxonomy": {"models": {str(BRAND.make_id): [{"value": 1746, "label": "Panda"}]}},
    }
    model_page_props = {
        "numberOfResults": 1,
        "numberOfPages": 1,
        "listings": [_fake_listing("e2e-1", 12345)],
    }

    discovery_url = build_search_url(BRAND.slug, page=1, make_id=BRAND.make_id)
    model_url = build_search_url(BRAND.slug, page=1, make_id=BRAND.make_id, model_id=1746)

    respx.get(discovery_url).mock(return_value=httpx.Response(200, text=_next_data_html(discovery_page_props)))
    respx.get(model_url).mock(return_value=httpx.Response(200, text=_next_data_html(model_page_props)))

    run = run_brand_sweep(db_session, _client, BRAND, fetch_detail_fn=_noop_fetch_detail)

    assert run.status == "success"
    assert run.new_listings == 1
    listing = db_session.get(Listing, "e2e-1")
    assert listing is not None
    assert listing.price == 12345


def test_process_detail_backlog_processes_every_pending_listing_across_db_pages(db_session):
    run = ScrapeRun(brand="Fiat", started_at=dt.datetime.utcnow(), status="running")
    db_session.add(run)
    db_session.flush()
    for i in range(7):
        db_session.add(_existing_listing(f"pending-{i}", 1000 + i, detail_scraped=False))
    db_session.commit()

    def fake_fetch_detail(client, url):
        return DetailResult(sold=False, data=_fake_detail_data(url))

    process_detail_backlog(
        db_session, _client, BRAND, run,
        concurrency=3, db_page_size=3, fetch_detail_fn=fake_fetch_detail,
    )

    rows = db_session.query(Listing).filter_by(brand="Fiat").all()
    assert len(rows) == 7
    assert all(row.detail_scraped for row in rows)


def test_process_detail_backlog_terminates_when_a_listing_cannot_be_processed(db_session):
    """A permanently failing detail page must not trap the paging loop in an
    infinite retry that hammers the site.

    Superseded 29/07: the pool used to make any non-BlockedError fatal, so
    the poison listing killed this call outright and that was what stopped
    the loop. Now that job is isolated by run_worker_pool itself -- it never
    reaches ``handled``, so the existing park-unreported-rows logic below
    excludes it from the next page query. The call now terminates by
    finishing normally, not by raising.
    """
    run = ScrapeRun(brand="Fiat", started_at=dt.datetime.utcnow(), status="running")
    db_session.add(run)
    db_session.flush()
    db_session.add(_existing_listing("poison-1", 1000, detail_scraped=False))
    db_session.commit()

    def failing_fetch_detail(client, url):
        raise ValueError("permanently broken detail page")

    total = process_detail_backlog(
        db_session, _client, BRAND, run, db_page_size=1, fetch_detail_fn=failing_fetch_detail
    )

    assert total == 0
    assert db_session.get(Listing, "poison-1").detail_scraped is False


def test_run_brand_sweep_ignores_active_listings_older_than_the_year_floor(db_session):
    """Listings registered before the floor no longer appear in searches, so they
    must not be mistaken for 'missing' and sold-confirmed on every run."""
    old = _existing_listing("old-1", 3000)
    old.first_registration = dt.date(2005, 6, 1)
    db_session.add(old)
    db_session.commit()

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        return iter([])

    def exploding_fetch_detail(client, url):
        raise AssertionError(f"out-of-floor listing must not be detail-fetched: {url}")

    run = run_brand_sweep(
        db_session, _client, BRAND, crawl_fn=fake_crawl,
        fetch_detail_fn=exploding_fetch_detail, year_from=2021,
    )

    assert run.status == "success"
    assert run.sold_detected == 0
    assert run.errors_count == 0
    assert db_session.get(Listing, "old-1").status == "active"


def test_process_detail_backlog_parks_a_row_the_pool_reports_nothing_for(db_session, monkeypatch):
    """Termination guarantee: if the worker pool completes normally but
    silently reports no result for a job (as opposed to raising, which the
    older test in this module already covers), that row must be parked in
    failed_ids for the rest of this call so the LIMIT-ed backlog query does
    not keep re-selecting it forever. We substitute run_worker_pool itself
    with a stub that runs the real per-job worker for every job except one,
    whose result it drops -- exactly the "pool completes normally, reports
    nothing" scenario the raising test cannot exercise."""
    run = ScrapeRun(brand="Fiat", started_at=dt.datetime.utcnow(), status="running")
    db_session.add(run)
    db_session.flush()
    db_session.add(_existing_listing("silent-1", 1000, detail_scraped=False))
    db_session.add(_existing_listing("silent-2", 2000, detail_scraped=False))
    db_session.commit()

    call_count = {"n": 0}

    def flaky_pool(jobs, worker_fn, client_factory, concurrency, session_refresh_requests):
        call_count["n"] += 1
        if call_count["n"] > 5:
            # If process_detail_backlog is still re-querying after this many
            # pages for a single permanently-unreported row, it is looping
            # instead of parking the row -- fail fast rather than hanging.
            raise AssertionError(
                "process_detail_backlog kept re-querying instead of parking "
                "the row the pool never reported on"
            )
        client = client_factory()
        try:
            for job in jobs:
                listing_id, _url = job
                if listing_id == "silent-1":
                    continue  # pool completes normally but reports nothing for this job
                for item in worker_fn(job, client):
                    yield item
        finally:
            client.close()

    monkeypatch.setattr("autosmart24.run_manager.run_worker_pool", flaky_pool)

    def fake_fetch_detail(client, url):
        return DetailResult(sold=False, data=_fake_detail_data(url))

    total_sold = process_detail_backlog(
        db_session, _client, BRAND, run, db_page_size=10, fetch_detail_fn=fake_fetch_detail,
    )

    assert total_sold == 0
    assert db_session.get(Listing, "silent-1").detail_scraped is False
    assert db_session.get(Listing, "silent-2").detail_scraped is True


def test_process_detail_backlog_processes_null_first_registration_pending_listing(db_session):
    """The backlog query's floor predicate must keep NULL first_registration
    rows in scope (unknown registration date must never be silently treated
    as out-of-floor)."""
    run = ScrapeRun(brand="Fiat", started_at=dt.datetime.utcnow(), status="running")
    db_session.add(run)
    db_session.flush()
    listing = _existing_listing("null-reg-pending-1", 1000, detail_scraped=False)
    listing.first_registration = None
    db_session.add(listing)
    db_session.commit()

    def fake_fetch_detail(client, url):
        return DetailResult(sold=False, data=_fake_detail_data(url))

    process_detail_backlog(
        db_session, _client, BRAND, run, fetch_detail_fn=fake_fetch_detail, year_from=2021,
    )

    assert db_session.get(Listing, "null-reg-pending-1").detail_scraped is True


def test_run_brand_sweep_keeps_null_registration_listings_in_scope_with_year_floor(db_session):
    """The active-inventory query's floor predicate must likewise keep NULL
    first_registration rows in scope: dropping them would make such listings
    invisible to the sweep forever, never sold-confirmed even after they
    vanish from search results."""
    listing = _existing_listing("null-reg-active-1", 3000)
    listing.first_registration = None
    db_session.add(listing)
    db_session.commit()

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        return iter([])

    def fake_fetch_detail(client, url):
        return DetailResult(sold=True)

    run = run_brand_sweep(
        db_session, _client, BRAND, crawl_fn=fake_crawl,
        fetch_detail_fn=fake_fetch_detail, year_from=2021,
    )

    assert run.sold_detected == 1
    assert db_session.get(Listing, "null-reg-active-1").status == "sold"


def test_process_detail_backlog_respects_year_floor_for_pending_listings(db_session):
    """The backlog query's year-floor filter (untested for the backlog half
    even though the active-inventory half was covered) must exclude pending
    listings registered before the floor."""
    run = ScrapeRun(brand="Fiat", started_at=dt.datetime.utcnow(), status="running")
    db_session.add(run)
    db_session.flush()
    old_pending = _existing_listing("old-pending-1", 2000, detail_scraped=False)
    old_pending.first_registration = dt.date(2005, 6, 1)
    db_session.add(old_pending)
    db_session.commit()

    def exploding_fetch_detail(client, url):
        raise AssertionError(f"out-of-floor pending listing must not be detail-fetched: {url}")

    total_sold = process_detail_backlog(
        db_session, _client, BRAND, run, fetch_detail_fn=exploding_fetch_detail, year_from=2021,
    )

    assert total_sold == 0
    assert db_session.get(Listing, "old-pending-1").detail_scraped is False


def test_process_detail_backlog_includes_listing_registered_exactly_at_the_year_floor(db_session):
    """Boundary check: a listing registered in January of the floor year
    itself must remain in scope (>= floor, not > floor)."""
    run = ScrapeRun(brand="Fiat", started_at=dt.datetime.utcnow(), status="running")
    db_session.add(run)
    db_session.flush()
    boundary = _existing_listing("floor-boundary-1", 1500, detail_scraped=False)
    boundary.first_registration = dt.date(2021, 1, 15)
    db_session.add(boundary)
    db_session.commit()

    def fake_fetch_detail(client, url):
        return DetailResult(sold=False, data=_fake_detail_data(url))

    process_detail_backlog(
        db_session, _client, BRAND, run, fetch_detail_fn=fake_fetch_detail, year_from=2021,
    )

    assert db_session.get(Listing, "floor-boundary-1").detail_scraped is True


def test_process_detail_backlog_commits_each_page_before_the_next(db_session, monkeypatch):
    """A completed detail-backlog page must be durably committed before the
    next page starts, not merely flushed within the still-open transaction.

    Technique: on this StaticPool-shared-connection test engine, opening a
    second session bound to the same engine and closing it issues an
    implicit ROLLBACK on the one shared physical connection -- which wipes
    out any work the original session had only flushed (still part of the
    same open transaction) but leaves already-committed work untouched
    (verified empirically against both the real and a flush()-only mutant).
    This only manifests when the disruptive second session runs on the SAME
    thread as db_session, so -- as gap 1's test does for a different reason
    -- run_worker_pool is substituted with a same-thread stub that still
    runs the real per-job worker, keeping every session touch on this one
    thread instead of the real thread pool's worker threads.

    We disrupt on the first job of page 2 and then make that job raise, so
    process_detail_backlog aborts immediately instead of re-querying and
    silently re-processing (and thus re-flushing) the very rows the
    disruption just rolled back -- which would otherwise mask the mutant by
    healing it on the next iteration."""
    run = ScrapeRun(brand="Fiat", started_at=dt.datetime.utcnow(), status="running")
    db_session.add(run)
    db_session.flush()
    base = dt.datetime.utcnow()
    for i in range(3):
        row = _existing_listing(f"durable-{i}", 1000 + i, detail_scraped=False)
        row.first_seen_at = base + dt.timedelta(seconds=i)
        db_session.add(row)
    db_session.commit()

    def same_thread_pool(jobs, worker_fn, client_factory, concurrency, session_refresh_requests):
        client = client_factory()
        try:
            for job in jobs:
                for item in worker_fn(job, client):
                    yield item
        finally:
            client.close()

    monkeypatch.setattr("autosmart24.run_manager.run_worker_pool", same_thread_pool)

    def fake_fetch_detail(client, url):
        listing_id = url.rsplit("/", 1)[-1]
        if listing_id == "durable-2":
            # First (and only) job of the second page: touch a totally
            # separate session on the same engine, then blow up so
            # process_detail_backlog cannot re-query and paper over any
            # damage this just did to page 1's not-yet-committed work.
            other = sessionmaker(bind=db_session.bind)()
            other.query(Listing).all()
            other.close()
            raise ValueError("boom - simulated permanently broken detail page")
        return DetailResult(sold=False, data=_fake_detail_data(listing_id))

    with pytest.raises(ValueError, match="boom"):
        process_detail_backlog(
            db_session, _client, BRAND, run,
            db_page_size=2, concurrency=1, fetch_detail_fn=fake_fetch_detail,
        )

    # Bypass db_session's identity map (which would otherwise keep serving
    # stale in-memory object state) and force a genuine reload from the
    # database for the final check.
    db_session.expire_all()
    scraped_by_id = {
        row.id: row.detail_scraped
        for row in db_session.query(Listing).filter_by(brand="Fiat").all()
    }
    assert scraped_by_id["durable-0"] is True
    assert scraped_by_id["durable-1"] is True


def test_run_brand_sweep_skips_detail_backlog_when_already_blocked(db_session):
    """If the sold-confirmation (missing-ids) phase already got the run
    blocked, the detail backlog pass must not run afterwards -- entering it
    would open a fresh client against a site that just returned 403/429."""
    db_session.add(_existing_listing("missing-blocked-1", 10000))
    db_session.add(_existing_listing("pending-should-not-run", 5000, detail_scraped=False))
    db_session.commit()

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        # "Sees" pending-should-not-run again this sweep so it is excluded from
        # missing_ids, while leaving it detail_scraped=False -- still eligible
        # for the backlog pass, which must never be reached.
        yield _fake_snippet("pending-should-not-run", 5000)

    fetch_calls: list[str] = []

    def fake_fetch_detail(client, url):
        fetch_calls.append(url)
        raise BlockedError(403, url)

    run = run_brand_sweep(db_session, _client, BRAND, crawl_fn=fake_crawl, fetch_detail_fn=fake_fetch_detail)

    assert run.status == "blocked"
    assert fetch_calls == ["https://www.autoscout24.it/annunci/missing-blocked-1"]
    events = db_session.query(ScrapeEvent).filter_by(brand="Fiat").all()
    assert not any(e.level == "info" and e.message.startswith("Detail backlog page:") for e in events)


def test_process_detail_backlog_persists_new_structured_fields(db_session):
    db_session.add(_existing_listing("detail-fields-1", 10000, detail_scraped=False))
    db_session.commit()
    run = ScrapeRun(brand="Fiat", started_at=dt.datetime.utcnow(), status="running")
    db_session.add(run)
    db_session.flush()

    def fake_fetch_detail(client, url):
        data = _fake_detail_data("detail-fields-1")
        data.update({
            "had_accident": False, "has_full_service_history": True, "gears": 6,
            "drive_train": "Anteriore", "cylinders": 3, "weight_kg": 1159,
            "co2_emissions_g_km": 109.0, "fuel_consumption_combined": 5.4,
            "emission_class": "Euro 6d", "upholstery": "Altro", "upholstery_color": "Nero",
            "is_conditional_price": True, "interaction_count": 10670, "favorites_count": 193,
            "new_driver_suitable": True, "dealer": None,
        })
        return DetailResult(sold=False, data=data)

    process_detail_backlog(db_session, _client, BRAND, run, fetch_detail_fn=fake_fetch_detail)

    listing = db_session.get(Listing, "detail-fields-1")
    assert listing.had_accident is False
    assert listing.has_full_service_history is True
    assert listing.gears == 6
    assert listing.drive_train == "Anteriore"
    assert listing.cylinders == 3
    assert listing.weight_kg == 1159
    assert listing.co2_emissions_g_km == 109.0
    assert listing.fuel_consumption_combined == 5.4
    assert listing.emission_class == "Euro 6d"
    assert listing.upholstery == "Altro"
    assert listing.upholstery_color == "Nero"
    assert listing.is_conditional_price is True
    assert listing.interaction_count == 10670
    assert listing.favorites_count == 193
    assert listing.new_driver_suitable is True
    assert listing.dealer_id is None


def test_run_brand_sweep_excludes_sold_candidates_from_detail_backlog(db_session):
    """Finding 1 regression (final review, 2026-07-28): a listing that looked
    removed on the first missing-ids pass is still status='active' and
    detail_scraped=False until the confirmation pass writes it minutes later.
    Without exclude_ids, process_detail_backlog's own query ('active' AND
    NOT detail_scraped) picks the same candidate up in between and fetches
    its detail page a spurious third time -- and, in the id-reuse case,
    would overwrite the row with a different car's data. Only two fetches
    (first pass + confirmation) must occur."""
    db_session.add(_existing_listing("cand-1", 10000, detail_scraped=False))
    db_session.commit()

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        return iter(())  # cand-1 never appears in search results this sweep

    fetch_calls: list[str] = []

    def fake_fetch_detail(client, url):
        fetch_calls.append(url)
        return DetailResult(sold=True)

    run = run_brand_sweep(db_session, _client, BRAND, crawl_fn=fake_crawl, fetch_detail_fn=fake_fetch_detail)

    cand_url = "https://www.autoscout24.it/annunci/cand-1"
    assert fetch_calls.count(cand_url) == 2
    assert run.sold_detected == 1


def test_run_brand_sweep_logs_confirmed_sale_as_explicit_removal(db_session):
    """Finding 2: a confirmed sale must be logged, and an explicit removal
    (result.sold=True, e.g. 404/410) must be distinguishable in that log
    from a brand-mismatch inference."""
    db_session.add(_existing_listing("sold-explicit-1", 10000))
    db_session.commit()

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        return iter(())

    def fake_fetch_detail(client, url):
        return DetailResult(sold=True)

    run = run_brand_sweep(db_session, _client, BRAND, crawl_fn=fake_crawl, fetch_detail_fn=fake_fetch_detail)

    assert run.sold_detected == 1
    events = db_session.query(ScrapeEvent).filter_by(brand="Fiat").all()
    sold_events = [e for e in events if "sold-explicit-1" in e.message and "sold" in e.message.lower()]
    assert len(sold_events) == 1
    assert "brand mismatch" not in sold_events[0].message.lower()


def test_run_brand_sweep_logs_confirmed_sale_as_brand_mismatch(db_session):
    """Finding 2: the other ground for a confirmed sale -- looks_removed
    inferring removal from a brand mismatch rather than an explicit
    result.sold -- must be logged distinctly so a systematic brand-parsing
    drift shows up as a spike of brand-mismatch sales before it becomes 139
    rows, per the reviewer's stated exposure."""
    db_session.add(_existing_listing("sold-mismatch-1", 10000))
    db_session.commit()

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        return iter(())

    def fake_fetch_detail(client, url):
        return DetailResult(sold=False, data={"brand": "Lancia"})

    run = run_brand_sweep(db_session, _client, BRAND, crawl_fn=fake_crawl, fetch_detail_fn=fake_fetch_detail)

    assert run.sold_detected == 1
    events = db_session.query(ScrapeEvent).filter_by(brand="Fiat").all()
    sold_events = [e for e in events if "sold-mismatch-1" in e.message and "sold" in e.message.lower()]
    assert len(sold_events) == 1
    assert "brand mismatch" in sold_events[0].message.lower()


def test_run_brand_sweep_backlog_removed_reports_increment_errors_count(db_session):
    """Finding 3: a repeat of the id-reuse incident -- the backlog's detail
    fetch reporting 'removed' for listings the search just showed present --
    must leave a visible trace in run.errors_count, the dashboard's only
    monitoring channel, instead of finishing status='success',
    errors_count=0 as if nothing happened."""
    db_session.add(_existing_listing("backlog-removed-1", 10000, detail_scraped=False))
    db_session.commit()

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        yield _fake_snippet("backlog-removed-1", 10000)

    def fake_fetch_detail(client, url):
        return DetailResult(sold=True)

    run = run_brand_sweep(db_session, _client, BRAND, crawl_fn=fake_crawl, fetch_detail_fn=fake_fetch_detail)

    assert run.status == "success"
    assert run.errors_count > 0


def test_process_detail_backlog_upserts_dealer_and_links_listing(db_session):
    db_session.add(_existing_listing("detail-dealer-1", 10000, detail_scraped=False))
    db_session.commit()
    run = ScrapeRun(brand="Fiat", started_at=dt.datetime.utcnow(), status="running")
    db_session.add(run)
    db_session.flush()

    def fake_fetch_detail(client, url):
        data = _fake_detail_data("detail-dealer-1")
        data["dealer"] = {
            "id": 555, "company_name": "Test Dealer Srl",
            "ratings_stars": 4.5, "ratings_count": 20, "recommend_percentage": 85,
        }
        return DetailResult(sold=False, data=data)

    process_detail_backlog(db_session, _client, BRAND, run, fetch_detail_fn=fake_fetch_detail)

    listing = db_session.get(Listing, "detail-dealer-1")
    assert listing.dealer_id == 555
    dealer = db_session.get(Dealer, 555)
    assert dealer is not None
    assert dealer.company_name == "Test Dealer Srl"
    assert dealer.ratings_stars == 4.5


def test_run_brand_sweep_skips_a_listing_id_that_belongs_to_another_brand(db_session):
    """AutoScout24 reassigns the id of a withdrawn ad to an unrelated car, and
    the new car can belong to a different brand. The existing-id guard used to
    be scoped to the brand being swept, so the sweep took the INSERT path and
    died on listings_pkey -- taking 28,000 healthy listings with it. Audi hit
    this five times on the same id across two machines.
    """
    now = dt.datetime(2026, 7, 1)
    db_session.add(
        Listing(
            id="reused-1", brand="Mercedes-Benz", status="sold", url="https://x/old",
            price=10000, first_seen_at=now, last_seen_at=now,
            last_checked_at=now, detail_scraped=True,
        )
    )
    db_session.commit()

    audi = BrandConfig(slug="audi", make_id=9, display_name="Audi")

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        yield _fake_snippet("reused-1", 20000, brand="Audi")
        yield _fake_snippet("fresh-1", 21000, brand="Audi")

    run = run_brand_sweep(
        db_session, _client, audi, crawl_fn=fake_crawl,
        fetch_detail_fn=_noop_fetch_detail, concurrency=1,
    )

    # The sweep must reach the end, not just avoid crashing.
    assert run.status in ("success", "partial")
    assert run.finished_at is not None
    # The other listing in the same batch is unaffected.
    assert db_session.get(Listing, "fresh-1") is not None
    # The pre-existing row keeps its own brand: overwriting it would attribute
    # one car's price history to another.
    stale = db_session.get(Listing, "reused-1")
    assert stale.brand == "Mercedes-Benz"
    assert stale.status == "sold"
    # Nothing was written to the DB for "reused-1" -- it must not inflate the
    # count of genuine new listings alongside "fresh-1".
    assert run.new_listings == 1


def test_run_brand_sweep_records_each_id_reuse_it_meets(db_session):
    """Frequency was estimated from a single observed case. Logging every
    occurrence is what turns it into a measurement."""
    now = dt.datetime(2026, 7, 1)
    db_session.add(
        Listing(
            id="reused-1", brand="Mercedes-Benz", status="sold", url="https://x/old",
            price=10000, first_seen_at=now, last_seen_at=now,
            last_checked_at=now, detail_scraped=True,
        )
    )
    db_session.commit()

    audi = BrandConfig(slug="audi", make_id=9, display_name="Audi")

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        yield _fake_snippet("reused-1", 20000, brand="Audi")

    run = run_brand_sweep(
        db_session, _client, audi, crawl_fn=fake_crawl,
        fetch_detail_fn=_noop_fetch_detail, concurrency=1,
    )

    messages = [e.message for e in db_session.query(ScrapeEvent).all()]
    assert any("reused-1" in m and "Mercedes-Benz" in m for m in messages)
    assert run.errors_count >= 1


def test_run_brand_sweep_still_relists_a_reappearing_listing_of_the_same_brand(db_session):
    """The global lookup must not break the existing relist path: an id that
    comes back under its OWN brand is a relist, not a reuse."""
    now = dt.datetime(2026, 7, 1)
    db_session.add(
        Listing(
            id="back-1", brand="Fiat", status="sold", url="https://x/back",
            price=9000, first_seen_at=now, last_seen_at=now,
            last_checked_at=now, detail_scraped=True,
            sold_at=dt.datetime(2026, 7, 5),
        )
    )
    db_session.commit()

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        yield _fake_snippet("back-1", 9500, brand="Fiat")

    run_brand_sweep(
        db_session, _client, BRAND, crawl_fn=fake_crawl,
        fetch_detail_fn=_noop_fetch_detail, concurrency=1,
    )

    row = db_session.get(Listing, "back-1")
    assert row.status == "active"
    assert row.sold_at is None
