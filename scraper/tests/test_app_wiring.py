"""Smoke tests for the production entrypoint `autosmart24.api.app`.

`api/app.py` builds a real SQLAlchemy engine at *module import time*, so it
cannot be imported like a normal module in the test suite -- doing so would
either require a live DATABASE_URL or blow up. We work around this by
pointing DATABASE_URL at an in-memory SQLite database before import and
importing the module fresh via importlib, popping it back out of
sys.modules afterwards so no other test in the suite (which may run in a
different order) observes a module that was built against this test's
environment variables.

These tests exist because api/app.py previously had zero coverage: it kept
passing a `RateLimitedClient` *instance* to `run_brand_sweep` for four tasks
after the function was changed to expect a zero-argument *client factory*
callable, which broke every production sweep with
`TypeError: 'RateLimitedClient' object is not callable`. The
test_client_factory_is_a_factory_not_an_instance test below is written
specifically to catch that regression again.
"""

from __future__ import annotations

import importlib
import sys

import pytest
from sqlalchemy import select

MODULE_NAME = "autosmart24.api.app"


@pytest.fixture()
def imported_app_module(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("SCRAPE_MAX_LISTING_AGE_YEARS", "5")

    # Force a fresh execution of the module body (which reads the env vars
    # above) rather than reusing a cached import from elsewhere.
    previous = sys.modules.pop(MODULE_NAME, None)
    module = importlib.import_module(MODULE_NAME)
    try:
        yield module
    finally:
        sys.modules.pop(MODULE_NAME, None)
        if previous is not None:
            sys.modules[MODULE_NAME] = previous


def test_app_module_imports_successfully(imported_app_module):
    assert imported_app_module.app is not None


def test_client_factory_is_a_factory_not_an_instance(imported_app_module):
    from autosmart24.scraping.http_client import RateLimitedClient

    factory = imported_app_module._client_factory
    assert callable(factory)

    # This is the precise regression that broke production: at HEAD before
    # this fix, `_client_factory` would have been a `RateLimitedClient`
    # instance (no `__call__` method), so calling it here would raise
    # `TypeError: 'RateLimitedClient' object is not callable` -- the same
    # error `run_brand_sweep` hit the moment a sweep started.
    client = factory()
    try:
        assert isinstance(client, RateLimitedClient)
    finally:
        client.close()

    # Each call must build an independent client, not return a shared
    # singleton -- that's the whole point of passing a factory instead of
    # an instance to run_brand_sweep.
    other = factory()
    try:
        assert other is not client
    finally:
        other.close()


def test_seeds_tracked_brands_from_mvp_brands_on_first_startup(imported_app_module):
    from autosmart24.db.models import TrackedBrand
    from autosmart24.db.session import init_db

    module = imported_app_module
    init_db(module.engine)
    session = module.session_factory()
    try:
        module._seed_tracked_brands_if_empty(session)
        rows = session.execute(select(TrackedBrand)).scalars().all()
        assert {row.slug for row in rows} == {"fiat", "volkswagen", "bmw", "audi", "mercedes-benz"}
        assert all(row.year_from_years == module.SEED_MAX_LISTING_AGE_YEARS for row in rows)

        module._seed_tracked_brands_if_empty(session)  # must be idempotent
        rows_again = session.execute(select(TrackedBrand)).scalars().all()
        assert len(rows_again) == 5
    finally:
        session.close()


def test_seed_is_skipped_when_tracked_brands_already_populated(imported_app_module):
    import datetime as dt

    from autosmart24.db.models import BrandCatalog, TrackedBrand
    from autosmart24.db.session import init_db

    module = imported_app_module
    init_db(module.engine)
    session = module.session_factory()
    try:
        now = dt.datetime.utcnow()
        session.add(BrandCatalog(make_id=999, display_name="Custom", slug="custom", synced_at=now))
        session.add(
            TrackedBrand(
                make_id=999, slug="custom", display_name="Custom", paused=False,
                year_from_years=None, schedule_day_of_week=None, schedule_hour=3, schedule_minute=0,
                created_at=now,
            )
        )
        session.commit()

        module._seed_tracked_brands_if_empty(session)

        rows = session.execute(select(TrackedBrand)).scalars().all()
        assert {row.slug for row in rows} == {"custom"}  # MVP_BRANDS was NOT seeded on top
    finally:
        session.close()
