import datetime as dt

from autosmart24.config import BrandConfig
from autosmart24.db.models import Listing, PriceHistory, ScrapeEvent
from autosmart24.run_manager import run_brand_sweep
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
