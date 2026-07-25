import datetime as dt

import pytest

from autosmart24.db.models import BrandCatalog, TrackedBrand


def test_brand_catalog_round_trips(db_session):
    db_session.add(BrandCatalog(make_id=28, display_name="Fiat", slug="fiat", synced_at=dt.datetime.utcnow()))
    db_session.commit()

    row = db_session.get(BrandCatalog, 28)
    assert row is not None
    assert row.display_name == "Fiat"
    assert row.slug == "fiat"


def test_tracked_brand_round_trips(db_session):
    db_session.add(BrandCatalog(make_id=28, display_name="Fiat", slug="fiat", synced_at=dt.datetime.utcnow()))
    db_session.commit()

    db_session.add(
        TrackedBrand(
            make_id=28, slug="fiat", display_name="Fiat", paused=False,
            year_from_years=5, schedule_day_of_week=None, schedule_hour=3, schedule_minute=0,
            created_at=dt.datetime.utcnow(),
        )
    )
    db_session.commit()

    row = db_session.get(TrackedBrand, 28)
    assert row is not None
    assert row.year_from_years == 5
    assert row.schedule_day_of_week is None
    assert row.schedule_hour == 3
    assert row.paused is False


def test_tracked_brand_slug_must_be_unique(db_session):
    db_session.add(BrandCatalog(make_id=28, display_name="Fiat", slug="fiat", synced_at=dt.datetime.utcnow()))
    db_session.add(BrandCatalog(make_id=29, display_name="Fiat Professional", slug="fiat", synced_at=dt.datetime.utcnow()))
    db_session.commit()

    db_session.add(
        TrackedBrand(
            make_id=28, slug="fiat", display_name="Fiat", paused=False,
            year_from_years=None, schedule_day_of_week=None, schedule_hour=3, schedule_minute=0,
            created_at=dt.datetime.utcnow(),
        )
    )
    db_session.commit()

    db_session.add(
        TrackedBrand(
            make_id=29, slug="fiat", display_name="Fiat Professional", paused=False,
            year_from_years=None, schedule_day_of_week=None, schedule_hour=3, schedule_minute=0,
            created_at=dt.datetime.utcnow(),
        )
    )
    with pytest.raises(Exception):
        db_session.commit()
