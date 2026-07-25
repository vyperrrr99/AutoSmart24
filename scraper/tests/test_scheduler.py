from apscheduler.schedulers.background import BackgroundScheduler

from autosmart24.config import BrandConfig
from autosmart24.scheduler import BrandRunGuard, BrandScheduler

BRAND = BrandConfig(slug="fiat", make_id=28, display_name="Fiat")


def test_schedule_brand_registers_job():
    scheduler = BrandScheduler(BackgroundScheduler())
    scheduler.schedule_brand(BRAND, run_fn=lambda brand: None, hour=3, minute=0)

    job = scheduler.scheduler.get_job("fiat")
    assert job is not None
    assert scheduler.is_paused("fiat") is False


def test_pause_and_resume_brand():
    scheduler = BrandScheduler(BackgroundScheduler())
    scheduler.schedule_brand(BRAND, run_fn=lambda brand: None, hour=3, minute=0)

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


def test_schedule_brand_with_no_day_of_week_is_unrestricted():
    scheduler = BrandScheduler(BackgroundScheduler())
    scheduler.schedule_brand(BRAND, run_fn=lambda brand: None, day_of_week=None, hour=3, minute=0)

    job = scheduler.scheduler.get_job("fiat")
    day_field = next(f for f in job.trigger.fields if f.name == "day_of_week")
    assert day_field.is_default is True


def test_schedule_brand_with_day_of_week_restricts_to_that_day():
    scheduler = BrandScheduler(BackgroundScheduler())
    scheduler.schedule_brand(BRAND, run_fn=lambda brand: None, day_of_week="mon", hour=3, minute=0)

    job = scheduler.scheduler.get_job("fiat")
    day_field = next(f for f in job.trigger.fields if f.name == "day_of_week")
    assert day_field.is_default is False


def test_schedule_brand_replaces_existing_job_for_the_same_brand():
    # Must run against a *started* scheduler: APScheduler queues add_job()
    # into _pending_jobs until start() runs, and replace_existing dedup only
    # happens on that deferred write-through. Production only ever
    # re-schedules an existing brand from an API call, i.e. against a live
    # scheduler -- this reproduces that path, where a non-started scheduler
    # would silently pass while testing nothing.
    scheduler = BrandScheduler(BackgroundScheduler())
    scheduler.scheduler.start(paused=True)
    try:
        scheduler.schedule_brand(BRAND, run_fn=lambda brand: None, day_of_week=None, hour=3, minute=0)
        scheduler.schedule_brand(BRAND, run_fn=lambda brand: None, day_of_week="mon", hour=4, minute=30)

        jobs = [j for j in scheduler.scheduler.get_jobs() if j.id == "fiat"]
        assert len(jobs) == 1
        job = jobs[0]
        assert job.trigger.fields[job.trigger.FIELD_NAMES.index("hour")].expressions[0].first == 4
    finally:
        scheduler.shutdown()


def test_remove_brand_job_removes_an_existing_job():
    scheduler = BrandScheduler(BackgroundScheduler())
    scheduler.schedule_brand(BRAND, run_fn=lambda brand: None, hour=3, minute=0)

    scheduler.remove_brand_job("fiat")

    assert scheduler.scheduler.get_job("fiat") is None


def test_remove_brand_job_is_a_no_op_for_an_unknown_brand():
    scheduler = BrandScheduler(BackgroundScheduler())

    scheduler.remove_brand_job("does-not-exist")  # must not raise
