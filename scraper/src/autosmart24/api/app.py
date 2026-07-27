from __future__ import annotations

import datetime as dt
import logging
import os
import time

from sqlalchemy import select

from autosmart24.api.main import create_app
from autosmart24.config import BrandConfig, MVP_BRANDS
from autosmart24.db.models import BrandCatalog, ScrapeEvent, TrackedBrand
from autosmart24.db.session import make_engine, make_session_factory
from autosmart24.queue_control import QueueController
from autosmart24.run_manager import run_brand_sweep
from autosmart24.scheduler import BrandRunGuard, BrandScheduler
from autosmart24.scraping.brand_catalog import fetch_brand_catalog
from autosmart24.scraping.http_client import make_client
from autosmart24.scraping.rate_control import BlockRateTracker

logger = logging.getLogger(__name__)

MIN_DELAY_SECONDS = float(os.environ.get("SCRAPE_MIN_DELAY_SECONDS", "3"))
MAX_DELAY_SECONDS = float(os.environ.get("SCRAPE_MAX_DELAY_SECONDS", "8"))
CONCURRENCY = max(1, int(os.environ.get("SCRAPE_CONCURRENCY", "6")))
SESSION_REFRESH_REQUESTS = max(1, int(os.environ.get("SCRAPE_SESSION_REFRESH_REQUESTS", "30")))
# Used only to seed the initial per-brand year filter on first startup (see
# _seed_tracked_brands_if_empty) -- after that, each brand's own
# year_from_years column in tracked_brands is authoritative, editable from
# the dashboard.
SEED_MAX_LISTING_AGE_YEARS = int(os.environ.get("SCRAPE_MAX_LISTING_AGE_YEARS", "5"))

engine = make_engine()
session_factory = make_session_factory(engine)


def _on_backoff_change(multiplier: float) -> None:
    """Surface adaptive-backoff transitions on the dashboard, which is this
    project's only monitoring channel."""
    if multiplier > 1.0:
        message = f"Adaptive backoff engaged: request delays multiplied by {multiplier}"
    else:
        message = "Adaptive backoff released: request delays back to normal"
    logger.warning(message)
    session = session_factory()
    try:
        session.add(
            ScrapeEvent(
                run_id=None, brand=None, level="warning",
                message=message, url=None, created_at=dt.datetime.utcnow(),
            )
        )
        session.commit()
    except Exception:
        logger.exception("Failed to record backoff event")
        session.rollback()
    finally:
        session.close()


rate_controller = BlockRateTracker(on_backoff_change=_on_backoff_change)


def _client_factory():
    return make_client(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS, rate_controller=rate_controller)


scheduler = BrandScheduler()
run_guard = BrandRunGuard()
queue_controller = QueueController()


def _run_fn(brand: BrandConfig) -> None:
    if queue_controller.is_halted():
        # Exit before opening a client: with the queue halted after a block,
        # every request we skip is one that would deepen the block.
        state = queue_controller.state()
        logger.warning("Skipping sweep for brand %s: queue halted (%s)", brand.slug, state.reason)
        session = session_factory()
        try:
            session.add(
                ScrapeEvent(
                    run_id=None, brand=brand.display_name, level="warning",
                    message=f"Run saltata: coda ferma ({state.reason})",
                    url=None, created_at=dt.datetime.utcnow(),
                )
            )
            session.commit()
        finally:
            session.close()
        return

    if not run_guard.try_acquire(brand.slug):
        logger.warning("Skipping sweep for brand %s: a sweep is already in progress", brand.slug)
        return
    try:
        session = session_factory()
        try:
            tracked = session.execute(
                select(TrackedBrand).where(TrackedBrand.slug == brand.slug)
            ).scalar_one_or_none()
            year_from = None
            if tracked is not None and tracked.year_from_years is not None:
                year_from = dt.date.today().year - tracked.year_from_years
            run = run_brand_sweep(
                session, _client_factory, brand,
                concurrency=CONCURRENCY, year_from=year_from, session_refresh_requests=SESSION_REFRESH_REQUESTS,
            )
            if run is not None and run.status == "blocked":
                queue_controller.halt(f"blocco rilevato su {brand.display_name}")
        finally:
            session.close()
    finally:
        run_guard.release(brand.slug)


def _run_now_fn(brand: BrandConfig) -> None:
    scheduler.scheduler.add_job(_run_fn, args=[brand], trigger="date", id=f"manual-{brand.slug}-{int(time.time())}")


def _refresh_catalog_fn():
    client = _client_factory()
    try:
        return fetch_brand_catalog(client)
    finally:
        client.close()


app = create_app(
    session_factory=session_factory,
    scheduler=scheduler,
    run_now_fn=_run_now_fn,
    run_fn=_run_fn,
    refresh_catalog_fn=_refresh_catalog_fn,
    queue_controller=queue_controller,
)


def _seed_tracked_brands_if_empty(session) -> None:
    """Preserve today's 5-brand behavior on first startup after this feature
    ships. Daily-at-03:00 is not equivalent to the old SCRAPE_INTERVAL_DAYS=4
    -- interval-days and day/hour scheduling are different paradigms with no
    faithful conversion -- this is a disclosed, one-time default the user can
    change immediately from the dashboard."""
    already_seeded = session.execute(select(TrackedBrand.make_id)).first()
    if already_seeded is not None:
        return
    now = dt.datetime.utcnow()
    for brand in MVP_BRANDS:
        if session.get(BrandCatalog, brand.make_id) is None:
            session.add(
                BrandCatalog(make_id=brand.make_id, display_name=brand.display_name, slug=brand.slug, synced_at=now)
            )
        session.add(
            TrackedBrand(
                make_id=brand.make_id, slug=brand.slug, display_name=brand.display_name,
                paused=False, year_from_years=SEED_MAX_LISTING_AGE_YEARS,
                schedule_day_of_week=None, schedule_hour=3, schedule_minute=0, created_at=now,
            )
        )
    session.commit()


@app.on_event("startup")
def _start_scheduler():
    session = session_factory()
    try:
        _seed_tracked_brands_if_empty(session)
        rows = session.execute(select(TrackedBrand)).scalars().all()
        for row in rows:
            brand = BrandConfig(slug=row.slug, make_id=row.make_id, display_name=row.display_name)
            scheduler.schedule_brand(
                brand, run_fn=_run_fn,
                day_of_week=row.schedule_day_of_week, hour=row.schedule_hour, minute=row.schedule_minute,
            )
            if row.paused:
                scheduler.pause_brand(row.slug)
    finally:
        session.close()
    scheduler.start()


@app.on_event("shutdown")
def _stop_scheduler():
    scheduler.shutdown()
