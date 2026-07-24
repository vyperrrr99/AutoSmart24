from apscheduler.schedulers.background import BackgroundScheduler

from autosmart24.config import BrandConfig
from autosmart24.scheduler import BrandRunGuard, BrandScheduler

BRAND = BrandConfig(slug="fiat", make_id=28, display_name="Fiat")


def test_schedule_brand_registers_job():
    scheduler = BrandScheduler(BackgroundScheduler())
    scheduler.schedule_brand(BRAND, interval_days=4, run_fn=lambda brand: None)

    job = scheduler.scheduler.get_job("fiat")
    assert job is not None
    assert scheduler.is_paused("fiat") is False


def test_pause_and_resume_brand():
    scheduler = BrandScheduler(BackgroundScheduler())
    scheduler.schedule_brand(BRAND, interval_days=4, run_fn=lambda brand: None)

    scheduler.pause_brand("fiat")
    assert scheduler.is_paused("fiat") is True

    scheduler.resume_brand("fiat")
    assert scheduler.is_paused("fiat") is False


def test_brand_run_guard_acquires_when_free():
    guard = BrandRunGuard()

    assert guard.try_acquire("fiat") is True


def test_brand_run_guard_rejects_when_already_held():
    guard = BrandRunGuard()
    guard.try_acquire("fiat")

    assert guard.try_acquire("fiat") is False


def test_brand_run_guard_allows_reacquire_after_release():
    guard = BrandRunGuard()
    guard.try_acquire("fiat")

    guard.release("fiat")

    assert guard.try_acquire("fiat") is True


def test_brand_run_guard_tracks_brands_independently():
    guard = BrandRunGuard()
    guard.try_acquire("fiat")

    assert guard.try_acquire("bmw") is True
