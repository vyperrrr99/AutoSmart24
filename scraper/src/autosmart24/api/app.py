from __future__ import annotations

import logging
import os
import time

from autosmart24.api.main import create_app
from autosmart24.config import MVP_BRANDS
from autosmart24.db.session import make_engine, make_session_factory
from autosmart24.run_manager import run_brand_sweep
from autosmart24.scheduler import BrandRunGuard, BrandScheduler
from autosmart24.scraping.http_client import RateLimitedClient

logger = logging.getLogger(__name__)

INTERVAL_DAYS = float(os.environ.get("SCRAPE_INTERVAL_DAYS", "4"))
MIN_DELAY_SECONDS = float(os.environ.get("SCRAPE_MIN_DELAY_SECONDS", "3"))
MAX_DELAY_SECONDS = float(os.environ.get("SCRAPE_MAX_DELAY_SECONDS", "8"))

engine = make_engine()
session_factory = make_session_factory(engine)
client = RateLimitedClient(min_delay_seconds=MIN_DELAY_SECONDS, max_delay_seconds=MAX_DELAY_SECONDS)
scheduler = BrandScheduler()
run_guard = BrandRunGuard()


def _run_fn(brand):
    if not run_guard.try_acquire(brand.slug):
        logger.warning("Skipping sweep for brand %s: a sweep is already in progress", brand.slug)
        return
    try:
        session = session_factory()
        try:
            run_brand_sweep(session, client, brand)
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
