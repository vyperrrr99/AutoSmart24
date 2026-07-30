import datetime as dt
import threading
import time

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


def test_default_scheduler_runs_one_job_at_a_time():
    """With 25 brands sharing the 03:00 trigger, APScheduler's default pool
    would run 10 concurrently -- around 60 parallel HTTP requests once each
    run opens its own worker pool. The queue must be serial."""
    scheduler = BrandScheduler()
    concurrent = []
    peak = []
    lock = threading.Lock()
    done = threading.Event()

    def slow_job(brand):
        with lock:
            concurrent.append(1)
            peak.append(len(concurrent))
        time.sleep(0.2)
        with lock:
            concurrent.pop()
            if len(peak) == 3:
                done.set()

    scheduler.scheduler.start()
    try:
        for i in range(3):
            scheduler.scheduler.add_job(slow_job, id=f"brand-{i}", args=[None])
        done.wait(timeout=10)
    finally:
        scheduler.shutdown()

    assert max(peak) == 1


def test_default_scheduler_runs_a_job_queued_far_behind_a_long_running_one():
    """A job waiting behind a long-running one must not be discarded.

    The misfire check runs in the worker thread at EXECUTION time, comparing
    now() against the job's run_time captured when it became due -- not
    against how long it has been queued. We cannot literally sleep an hour
    in a test, so instead this reproduces an hours-long wait directly: the
    second job is given a next_run_time far enough in the past that, by the
    time the single worker frees up (however soon that is), the gap between
    "now" and that run_time already exceeds a 3600s grace window. A finite
    misfire_grace_time (e.g. the old 3600s) discards it; None ("run it no
    matter how late") runs it anyway.

    This must FAIL if BrandScheduler reverts to misfire_grace_time=3600 and
    PASS with misfire_grace_time=None.
    """
    scheduler = BrandScheduler()
    ran: list[str] = []
    first_started = threading.Event()
    release_first = threading.Event()

    def first_job(brand):
        first_started.set()
        release_first.wait(timeout=10)
        ran.append("first")

    def second_job(brand):
        ran.append("second")

    scheduler.scheduler.start()
    try:
        scheduler.scheduler.add_job(first_job, id="first", args=[None])
        assert first_started.wait(timeout=10), "first job never started"

        # Far enough in the past that a 3600s grace window has already
        # elapsed by the time the single worker gets to it, no matter how
        # quickly that happens.
        overdue_run_time = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=5)
        scheduler.scheduler.add_job(second_job, id="second", args=[None], next_run_time=overdue_run_time)

        release_first.set()

        deadline = time.time() + 10
        while "second" not in ran and time.time() < deadline:
            time.sleep(0.05)
    finally:
        scheduler.shutdown()

    assert ran == ["first", "second"]


def test_scheduler_interprets_hours_as_italian_local_time():
    """The container runs UTC while the operator thinks in Italian local time.

    Without an explicit timezone APScheduler uses the process's own -- UTC in
    the container -- so a brand configured for 22:00 would sweep at midnight,
    and the error would change size at the daylight-saving switch rather than
    staying a constant offset anyone could learn to allow for.
    """
    scheduler = BrandScheduler()
    assert str(scheduler.scheduler.timezone) == "Europe/Rome"


def test_a_brand_scheduled_at_22_fires_at_22_italian_time():
    """Asserts the consequence, not the setting: a constant is easy to satisfy
    by accident, a computed next-run time is not."""
    scheduler = BrandScheduler()
    scheduler.schedule_brand(BRAND, lambda b: None, hour=22, minute=0)
    job = scheduler.scheduler.get_job(BRAND.slug)

    next_run = job.trigger.get_next_fire_time(None, dt.datetime(2026, 7, 31, 12, 0, tzinfo=dt.timezone.utc))
    assert next_run.hour == 22, f"expected 22:00 Italian time, got {next_run}"
    # Summer: Italy is UTC+2, so 22:00 local is 20:00 UTC.
    assert next_run.utcoffset() == dt.timedelta(hours=2)

    # Winter: the same configured hour must still mean 22:00 locally, which is
    # 21:00 UTC. A fixed UTC hour would silently drift here.
    winter = job.trigger.get_next_fire_time(None, dt.datetime(2026, 1, 15, 12, 0, tzinfo=dt.timezone.utc))
    assert winter.hour == 22, f"expected 22:00 Italian time in winter, got {winter}"
    assert winter.utcoffset() == dt.timedelta(hours=1)
