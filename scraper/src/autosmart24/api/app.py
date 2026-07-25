from __future__ import annotations

import datetime as dt
import logging
import os
import time

from autosmart24.api.main import create_app
from autosmart24.config import MVP_BRANDS
from autosmart24.db.models import ScrapeEvent
from autosmart24.db.session import make_engine, make_session_factory
from autosmart24.run_manager import run_brand_sweep
from autosmart24.scheduler import BrandRunGuard, BrandScheduler
from autosmart24.scraping.http_client import make_client
from autosmart24.scraping.rate_control import BlockRateTracker

logger = logging.getLogger(__name__)

INTERVAL_DAYS = float(os.environ.get("SCRAPE_INTERVAL_DAYS", "4"))
MIN_DELAY_SECONDS = float(os.environ.get("SCRAPE_MIN_DELAY_SECONDS", "3"))
MAX_DELAY_SECONDS = float(os.environ.get("SCRAPE_MAX_DELAY_SECONDS", "8"))
CONCURRENCY = max(1, int(os.environ.get("SCRAPE_CONCURRENCY", "6")))
MAX_LISTING_AGE_YEARS = int(os.environ.get("SCRAPE_MAX_LISTING_AGE_YEARS", "5"))
SESSION_REFRESH_REQUESTS = max(1, int(os.environ.get("SCRAPE_SESSION_REFRESH_REQUESTS", "30")))

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


def _year_from() -> int:
    return dt.date.today().year - MAX_LISTING_AGE_YEARS


scheduler = BrandScheduler()
run_guard = BrandRunGuard()


def _run_fn(brand):
    if not run_guard.try_acquire(brand.slug):
        logger.warning("Skipping sweep for brand %s: a sweep is already in progress", brand.slug)
        return
    try:
        session = session_factory()
        try:
            run_brand_sweep(
                session, _client_factory, brand,
                concurrency=CONCURRENCY,
                year_from=_year_from(),
                session_refresh_requests=SESSION_REFRESH_REQUESTS,
            )
        finally:
            session.close()
    finally:
        run_guard.release(brand.slug)


def _run_now_fn(brand):
    scheduler.scheduler.add_job(_run_fn, args=[brand], trigger="date", id=f"manual-{brand.slug}-{int(time.time())}")


app = create_app(session_factory=session_factory, scheduler=scheduler, run_now_fn=_run_now_fn)


@app.on_event("startup")
def _start_scheduler():
    for brand in MVP_BRANDS:
        scheduler.schedule_brand(brand, interval_days=INTERVAL_DAYS, run_fn=_run_fn)
    scheduler.start()


@app.on_event("shutdown")
def _stop_scheduler():
    scheduler.shutdown()
