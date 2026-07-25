from __future__ import annotations

import threading

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
        self.scheduler = scheduler or BackgroundScheduler()

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
            trigger=CronTrigger(day_of_week=day_of_week, hour=hour, minute=minute),
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
