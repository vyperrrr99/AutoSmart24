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


# --- _start_scheduler --------------------------------------------------
#
# The tests below drive the FastAPI startup hook directly. It seeds
# tracked_brands (a no-op here since we pre-populate the table ourselves),
# schedules one APScheduler job per row using that row's own
# day_of_week/hour/minute, applies each row's paused flag, and finally calls
# scheduler.start() -- which spawns a real background thread. Each test
# MUST shut that scheduler down in a finally block, or the thread leaks into
# later tests in the same process.


def _seed_two_brands_with_distinct_schedules(session):
    import datetime as dt

    from autosmart24.db.models import BrandCatalog, TrackedBrand

    now = dt.datetime.utcnow()
    session.add(BrandCatalog(make_id=101, display_name="Alpha", slug="alpha", synced_at=now))
    session.add(BrandCatalog(make_id=102, display_name="Beta", slug="beta", synced_at=now))
    session.add(
        TrackedBrand(
            make_id=101, slug="alpha", display_name="Alpha", paused=False,
            year_from_years=5, schedule_day_of_week=None, schedule_hour=3, schedule_minute=0,
            created_at=now,
        )
    )
    session.add(
        TrackedBrand(
            make_id=102, slug="beta", display_name="Beta", paused=False,
            year_from_years=5, schedule_day_of_week="mon", schedule_hour=7, schedule_minute=30,
            created_at=now,
        )
    )
    session.commit()


def _hour_of(job):
    return job.trigger.fields[job.trigger.FIELD_NAMES.index("hour")].expressions[0].first


def test_start_scheduler_schedules_one_job_per_tracked_row_with_its_own_schedule(imported_app_module):
    from autosmart24.db.session import init_db

    module = imported_app_module
    init_db(module.engine)
    session = module.session_factory()
    try:
        _seed_two_brands_with_distinct_schedules(session)
    finally:
        session.close()

    try:
        module._start_scheduler()

        alpha_job = module.scheduler.scheduler.get_job("alpha")
        beta_job = module.scheduler.scheduler.get_job("beta")
        assert alpha_job is not None
        assert beta_job is not None
        assert _hour_of(alpha_job) == 3
        assert _hour_of(beta_job) == 7
    finally:
        module.scheduler.shutdown()


def test_start_scheduler_restores_paused_state_from_tracked_brands(imported_app_module):
    import datetime as dt

    from autosmart24.db.models import BrandCatalog, TrackedBrand
    from autosmart24.db.session import init_db

    module = imported_app_module
    init_db(module.engine)
    session = module.session_factory()
    try:
        now = dt.datetime.utcnow()
        session.add(BrandCatalog(make_id=201, display_name="Paused Co", slug="paused-brand", synced_at=now))
        session.add(BrandCatalog(make_id=202, display_name="Active Co", slug="active-brand", synced_at=now))
        session.add(
            TrackedBrand(
                make_id=201, slug="paused-brand", display_name="Paused Co", paused=True,
                year_from_years=5, schedule_day_of_week=None, schedule_hour=3, schedule_minute=0,
                created_at=now,
            )
        )
        session.add(
            TrackedBrand(
                make_id=202, slug="active-brand", display_name="Active Co", paused=False,
                year_from_years=5, schedule_day_of_week=None, schedule_hour=4, schedule_minute=0,
                created_at=now,
            )
        )
        session.commit()
    finally:
        session.close()

    try:
        module._start_scheduler()

        assert module.scheduler.is_paused("paused-brand") is True
        assert module.scheduler.is_paused("active-brand") is False
    finally:
        module.scheduler.shutdown()


def test_start_scheduler_does_not_create_duplicate_jobs(imported_app_module):
    from autosmart24.db.session import init_db

    module = imported_app_module
    init_db(module.engine)
    session = module.session_factory()
    try:
        _seed_two_brands_with_distinct_schedules(session)
        from autosmart24.db.models import TrackedBrand
        row_count = len(session.execute(select(TrackedBrand)).scalars().all())
    finally:
        session.close()

    try:
        module._start_scheduler()

        assert len(module.scheduler.scheduler.get_jobs()) == row_count
    finally:
        module.scheduler.shutdown()
