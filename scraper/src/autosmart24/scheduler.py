from __future__ import annotations

import threading

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from autosmart24.config import BrandConfig


class BrandRunGuard:
    """Thread-safe guard preventing concurrent sweeps of the same brand.

    APScheduler's per-job single-instance protection only applies within a
    single job id. A manual "run now" job (a distinct job id per invocation)
    can otherwise execute concurrently with the recurring scheduled job for
    the same brand, or with another manual trigger. This guard tracks
    in-progress brands independently of job ids.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running: set[str] = set()

    def try_acquire(self, brand_slug: str) -> bool:
        with self._lock:
            if brand_slug in self._running:
                return False
            self._running.add(brand_slug)
            return True

    def release(self, brand_slug: str) -> None:
        with self._lock:
            self._running.discard(brand_slug)


class BrandScheduler:
    def __init__(self, scheduler: BackgroundScheduler | None = None):
        # APScheduler's default pool runs 10 jobs at once. With every brand
        # sharing the 03:00 trigger that means up to 10 concurrent sweeps,
        # each opening SCRAPE_CONCURRENCY workers of its own -- roughly 60
        # parallel requests to autoscout24. One worker makes the queue
        # serial, keeping outbound concurrency at exactly one sweep's worth.
        #
        # misfire_grace_time must be None ("run it no matter how late", per
        # APScheduler's docs). The misfire check runs in the worker thread at
        # EXECUTION time (apscheduler.executors.base.run_job), comparing now()
        # against the job's captured run_time -- not at submission time. With
        # a single worker, a brand queued behind other sweeps can wait many
        # hours before that worker frees up (a single sweep has taken ~2h
        # measured), so any finite grace window -- even one chosen to be
        # generous, like the 3600s this used to be -- will already have
        # elapsed by execution time and the job is silently discarded: no
        # run row, no event, nothing on the dashboard.
        #
        # The timezone is stated explicitly because the container runs UTC
        # while the operator configuring a brand thinks in Italian local time.
        # Left to the default, a brand set to 22:00 would sweep at midnight --
        # and the error would not even be a constant one to learn around: it
        # would change by an hour at each daylight-saving switch. Naming the
        # zone keeps a configured hour meaning that hour all year.
        #
        # This does NOT affect the timestamps written to the database, which
        # stay naive UTC via datetime.utcnow() throughout the project.
        self.scheduler = scheduler or BackgroundScheduler(
            timezone="Europe/Rome",
            executors={"default": ThreadPoolExecutor(max_workers=1)},
            job_defaults={"misfire_grace_time": None, "max_instances": 1},
        )

    def schedule_brand(
        self,
        brand: BrandConfig,
        run_fn,
        day_of_week: str | None = None,
        hour: int = 3,
        minute: int = 0,
    ) -> None:
        self.scheduler.add_job(
            run_fn,
            # The timezone must be handed to the trigger, not left to the
            # scheduler: CronTrigger is built here, before add_job, and its
            # constructor captures the PROCESS timezone (UTC in the container)
            # rather than inheriting the scheduler's. Setting the scheduler's
            # zone alone looks right and silently schedules two hours late.
            trigger=CronTrigger(
                day_of_week=day_of_week, hour=hour, minute=minute,
                timezone=self.scheduler.timezone,
            ),
            id=brand.slug,
            replace_existing=True,
            args=[brand],
        )

    def remove_brand_job(self, brand_slug: str) -> None:
        if self.scheduler.get_job(brand_slug) is not None:
            self.scheduler.remove_job(brand_slug)

    def pause_brand(self, brand_slug: str) -> None:
        # A tracked brand without a live job (not yet scheduled, or removed)
        # should still be able to record its paused state; that state then
        # applies correctly whenever the job is (re)created. Silently
        # succeeding here is right, matching remove_brand_job's guard below.
        if self.scheduler.get_job(brand_slug) is not None:
            self.scheduler.pause_job(brand_slug)

    def resume_brand(self, brand_slug: str) -> None:
        if self.scheduler.get_job(brand_slug) is not None:
            self.scheduler.resume_job(brand_slug)

    def is_paused(self, brand_slug: str) -> bool:
        job = self.scheduler.get_job(brand_slug)
        if job is None:
            return False
        try:
            return job.next_run_time is None
        except AttributeError:
            return False

    def start(self) -> None:
        self.scheduler.start()

    def shutdown(self) -> None:
        self.scheduler.shutdown(wait=False)
