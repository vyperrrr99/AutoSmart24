import datetime as dt

from sqlalchemy.orm import sessionmaker

from autosmart24.config import BrandConfig
from autosmart24.db.models import ScrapeRun
from autosmart24.run_manager import run_brand_sweep
from autosmart24.scraping.detail_queue import DetailResult
from autosmart24.scraping.http_client import RateLimitedClient

BRAND = BrandConfig(slug="fiat", make_id=28, display_name="Fiat")


def _client() -> RateLimitedClient:
    return RateLimitedClient(min_delay_seconds=0, max_delay_seconds=0, sleep_fn=lambda _: None)


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
    return DetailResult(sold=False, data=_fake_detail_data(url))


def _snippet(listing_id: str, price: int) -> dict:
    return {
        "id": listing_id, "cross_reference_id": listing_id, "brand": "Fiat",
        "model": "Panda", "model_group": "Panda", "variant": None,
        "motor_type_name": "1.0", "version_input": None, "transmission": "Manuale",
        "fuel": "Benzina", "first_registration": dt.date(2020, 1, 1), "mileage_km": 50000,
        "seller_type": "Dealer", "seller_company_name": "Test Dealer",
        "city": "Roma - Roma - RM", "zip_code": "00100", "price": price,
        "url": f"https://www.autoscout24.it/annunci/{listing_id}",
    }


def test_run_records_search_phase_progress_before_the_crawl_ends(db_session):
    """The run row must expose partial progress mid-sweep: this is what the
    dashboard polls. Before this change listings_seen stayed 0 until the end."""
    observed = {}

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        yield _snippet("a-1", 1000)
        yield _snippet("a-2", 2000)

        other = sessionmaker(bind=db_session.bind)()
        try:
            row = other.query(ScrapeRun).one()
            observed["phase"] = row.phase
            observed["listings_seen"] = row.listings_seen
            observed["new_listings"] = row.new_listings
        finally:
            other.close()

        yield _snippet("a-3", 3000)

    run_brand_sweep(
        db_session, _client, BRAND, crawl_fn=fake_crawl, batch_size=2,
        fetch_detail_fn=_noop_fetch_detail,
    )

    assert observed["phase"] == "search"
    assert observed["listings_seen"] == 2
    assert observed["new_listings"] == 2
