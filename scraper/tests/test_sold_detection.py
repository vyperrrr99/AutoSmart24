import datetime as dt

from autosmart24.config import BrandConfig
from autosmart24.db.models import Listing, ScrapeEvent, ScrapeRun
from autosmart24.run_manager import process_detail_backlog, run_brand_sweep
from autosmart24.scraping.detail_queue import DetailResult
from autosmart24.scraping.http_client import RateLimitedClient

BRAND = BrandConfig(slug="fiat", make_id=28, display_name="Fiat")


def _client() -> RateLimitedClient:
    return RateLimitedClient(min_delay_seconds=0, max_delay_seconds=0, sleep_fn=lambda _: None)


def _listing(listing_id: str, *, brand: str = "Fiat", detail_scraped: bool = False) -> Listing:
    now = dt.datetime(2026, 7, 28, 9, 0, 0)
    return Listing(
        id=listing_id, brand=brand, url=f"https://www.autoscout24.it/annunci/{listing_id}",
        first_seen_at=now, last_seen_at=now, last_checked_at=now,
        status="active", detail_scraped=detail_scraped, price=10000,
        first_registration=dt.date(2020, 1, 1),
    )


def _snippet(listing_id: str, price: int = 10000) -> dict:
    return {
        "id": listing_id, "cross_reference_id": listing_id, "brand": "Fiat",
        "model": "Panda", "model_group": "Panda", "variant": None,
        "motor_type_name": "1.0", "version_input": None, "transmission": "Manuale",
        "fuel": "Benzina", "first_registration": dt.date(2020, 1, 1), "mileage_km": 50000,
        "seller_type": "Dealer", "seller_company_name": "Test Dealer",
        "city": "Roma - Roma - RM", "zip_code": "00100", "price": price,
        "url": f"https://www.autoscout24.it/annunci/{listing_id}",
    }


def test_enrichment_does_not_sell_a_listing_seen_alive_in_the_same_sweep(db_session):
    """The Lancia incident: 139 listings were seen alive in the search results,
    then their detail pages answered 410 during enrichment and every one was
    marked sold. All were still live on the site. A detail-page removal cannot
    outweigh a search-listing sighting made minutes earlier."""
    db_session.add(_listing("seen-alive-1"))
    db_session.commit()

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        yield _snippet("seen-alive-1")

    def fake_detail(client, url):
        return DetailResult(sold=True)

    run = run_brand_sweep(db_session, _client, BRAND, crawl_fn=fake_crawl, fetch_detail_fn=fake_detail)

    listing = db_session.get(Listing, "seen-alive-1")
    assert listing.status == "active", "una pagina che risponde rimossa non deve battere un avvistamento nella ricerca"
    assert listing.sold_at is None
    assert run.sold_detected == 0


def test_enrichment_keeps_the_listing_in_the_backlog_for_a_later_retry(db_session):
    """detail_scraped must stay false: the listing was never actually enriched,
    so marking it done would turn a false sale into permanently missing data."""
    db_session.add(_listing("retry-me-1"))
    db_session.commit()
    run = ScrapeRun(brand="Fiat", started_at=dt.datetime.utcnow(), status="running")
    db_session.add(run)
    db_session.flush()

    def fake_detail(client, url):
        return DetailResult(sold=True)

    process_detail_backlog(db_session, _client, BRAND, run, fetch_detail_fn=fake_detail)

    listing = db_session.get(Listing, "retry-me-1")
    assert listing.detail_scraped is False
    assert listing.status == "active"


def test_enrichment_records_the_anomaly_as_an_event(db_session):
    db_session.add(_listing("anomaly-1"))
    db_session.commit()
    run = ScrapeRun(brand="Fiat", started_at=dt.datetime.utcnow(), status="running")
    db_session.add(run)
    db_session.flush()

    def fake_detail(client, url):
        return DetailResult(sold=True)

    process_detail_backlog(db_session, _client, BRAND, run, fetch_detail_fn=fake_detail)

    events = db_session.query(ScrapeEvent).filter_by(level="warning").all()
    assert any("anomaly-1" in e.message for e in events)
