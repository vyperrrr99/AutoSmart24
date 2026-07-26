import datetime as dt

import pytest

from autosmart24.db.models import Dealer, Listing


def _base_listing(listing_id: str = "abc-123") -> Listing:
    now = dt.datetime.utcnow()
    return Listing(
        id=listing_id, brand="Fiat", url="https://www.autoscout24.it/annunci/abc-123",
        first_seen_at=now, last_seen_at=now, last_checked_at=now, status="active",
        detail_scraped=True,
    )


def test_dealer_round_trips(db_session):
    db_session.add(
        Dealer(
            id=46936034, company_name="Puntocar di Tarantino Andrea - Bricherasio",
            ratings_stars=5, ratings_count=25, recommend_percentage=92,
            synced_at=dt.datetime.utcnow(),
        )
    )
    db_session.commit()

    row = db_session.get(Dealer, 46936034)
    assert row is not None
    assert row.company_name == "Puntocar di Tarantino Andrea - Bricherasio"
    assert row.ratings_stars == 5
    assert row.ratings_count == 25
    assert row.recommend_percentage == 92


def test_listing_new_detail_fields_round_trip(db_session):
    db_session.add(
        Dealer(id=1, company_name="Test Dealer", ratings_stars=4.5, ratings_count=10,
               recommend_percentage=80, synced_at=dt.datetime.utcnow())
    )
    listing = _base_listing()
    listing.had_accident = False
    listing.has_full_service_history = True
    listing.gears = 6
    listing.drive_train = "Anteriore"
    listing.cylinders = 3
    listing.weight_kg = 1159
    listing.co2_emissions_g_km = 109.0
    listing.fuel_consumption_combined = 5.4
    listing.fuel_consumption_urban = 6.1
    listing.fuel_consumption_extra_urban = 4.8
    listing.emission_class = "Euro 6d"
    listing.upholstery = "Altro"
    listing.upholstery_color = "Nero"
    listing.is_conditional_price = True
    listing.interaction_count = 10670
    listing.favorites_count = 193
    listing.new_driver_suitable = True
    listing.dealer_id = 1
    db_session.add(listing)
    db_session.commit()

    row = db_session.get(Listing, "abc-123")
    assert row.had_accident is False
    assert row.has_full_service_history is True
    assert row.gears == 6
    assert row.drive_train == "Anteriore"
    assert row.cylinders == 3
    assert row.weight_kg == 1159
    assert row.co2_emissions_g_km == 109.0
    assert row.fuel_consumption_combined == 5.4
    assert row.emission_class == "Euro 6d"
    assert row.upholstery == "Altro"
    assert row.upholstery_color == "Nero"
    assert row.is_conditional_price is True
    assert row.interaction_count == 10670
    assert row.favorites_count == 193
    assert row.new_driver_suitable is True
    assert row.dealer_id == 1


def test_listing_new_fields_default_to_null(db_session):
    db_session.add(_base_listing("def-456"))
    db_session.commit()

    row = db_session.get(Listing, "def-456")
    assert row.had_accident is None
    assert row.gears is None
    assert row.dealer_id is None
