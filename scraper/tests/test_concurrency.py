import threading
import time

import pytest

from autosmart24.scraping.concurrency import run_worker_pool
from autosmart24.scraping.http_client import BlockedError, RateLimitedClient


def _client_factory() -> RateLimitedClient:
    return RateLimitedClient(min_delay_seconds=0, max_delay_seconds=0, sleep_fn=lambda _: None)


def test_run_worker_pool_processes_all_jobs_and_yields_all_results():
    def worker_fn(job, client):
        return [job * 2]

    results = sorted(
        run_worker_pool(list(range(10)), worker_fn, _client_factory, concurrency=3, session_refresh_requests=100)
    )
    assert results == [i * 2 for i in range(10)]


def test_run_worker_pool_worker_fn_can_return_multiple_results_per_job():
    def worker_fn(job, client):
        return [job, job * 10]

    results = sorted(
        run_worker_pool([1, 2, 3], worker_fn, _client_factory, concurrency=2, session_refresh_requests=100)
    )
    assert results == sorted([1, 10, 2, 20, 3, 30])


def test_run_worker_pool_handles_empty_job_list_without_starting_threads():
    created = []

    def factory():
        created.append(1)
        return _client_factory()

    def worker_fn(job, client):
        raise AssertionError("must not be called")

    assert list(run_worker_pool([], worker_fn, factory, concurrency=4, session_refresh_requests=100)) == []
    assert created == []


def test_run_worker_pool_stops_and_raises_on_blocked_error():
    call_count = {"n": 0}
    lock = threading.Lock()

    def worker_fn(job, client):
        with lock:
            call_count["n"] += 1
        if job == 5:
            raise BlockedError(403, "https://example.test/blocked")
        return [job]

    with pytest.raises(BlockedError):
        list(run_worker_pool(list(range(20)), worker_fn, _client_factory, concurrency=1, session_refresh_requests=100))

    assert call_count["n"] == 6


def test_run_worker_pool_reraises_non_blocked_exceptions_instead_of_swallowing_them():
    """A worker raising anything other than BlockedError must surface to the
    caller. Swallowing it would silently truncate the job list while the caller
    sees a normal completion -- silent data loss."""
    def worker_fn(job, client):
        if job == 2:
            raise ValueError("boom")
        return [job]

    with pytest.raises(ValueError, match="boom"):
        list(run_worker_pool(list(range(5)), worker_fn, _client_factory, concurrency=1, session_refresh_requests=100))


def test_run_worker_pool_prefers_blocked_error_when_several_workers_fail():
    def worker_fn(job, client):
        if job == 0:
            raise BlockedError(429, "https://example.test/limited")
        raise ValueError("secondary failure")

    with pytest.raises(BlockedError):
        list(run_worker_pool(list(range(4)), worker_fn, _client_factory, concurrency=1, session_refresh_requests=100))


def test_run_worker_pool_creates_fresh_client_after_session_refresh_threshold():
    created_clients = []

    def factory():
        c = _client_factory()
        created_clients.append(c)
        return c

    def worker_fn(job, client):
        return [job]

    list(run_worker_pool(list(range(5)), worker_fn, factory, concurrency=1, session_refresh_requests=2))

    assert len(created_clients) == 3


def test_run_worker_pool_stops_workers_when_consumer_abandons_the_generator():
    """Abandoning the generator must not leave threads hammering the site with
    nobody consuming the results."""
    finished_jobs: list[int] = []
    lock = threading.Lock()

    def worker_fn(job, client):
        # A real per-job cost is what makes this test meaningful: without it the
        # workers drain all 50 jobs before the consumer can abandon the
        # generator, and the assertion below would pass vacuously.
        time.sleep(0.02)
        with lock:
            finished_jobs.append(job)
        return [job]

    gen = run_worker_pool(list(range(50)), worker_fn, _client_factory, concurrency=2, session_refresh_requests=100)
    next(gen)
    gen.close()

    with lock:
        completed = len(finished_jobs)
    assert completed < 50


def test_run_worker_pool_runs_jobs_concurrently():
    barrier = threading.Barrier(4, timeout=5)

    def worker_fn(job, client):
        barrier.wait()
        return [job]

    results = sorted(
        run_worker_pool(list(range(4)), worker_fn, _client_factory, concurrency=4, session_refresh_requests=100)
    )
    assert results == [0, 1, 2, 3]


def test_run_worker_pool_treats_non_positive_concurrency_as_one():
    def worker_fn(job, client):
        return [job]

    results = sorted(
        run_worker_pool(list(range(3)), worker_fn, _client_factory, concurrency=0, session_refresh_requests=100)
    )
    assert results == [0, 1, 2]
