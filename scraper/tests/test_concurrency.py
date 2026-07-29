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


def test_run_worker_pool_isolates_non_blocked_exceptions_instead_of_swallowing_the_run():
    """A worker raising anything other than BlockedError must not truncate the
    job list. Superseded 29/07: this used to be fatal (silent data loss via a
    killed run); now the failing job is isolated and the rest complete."""
    def worker_fn(job, client):
        if job == 2:
            raise ValueError("boom")
        return [job]

    assert sorted(
        run_worker_pool(list(range(5)), worker_fn, _client_factory, concurrency=1, session_refresh_requests=100)
    ) == [0, 1, 3, 4]


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
    """Abandoning the generator must stop the workers, not leave them fetching
    with nobody consuming the results.

    Asserting only on the count at close() time would be vacuous: barely any
    jobs finish in that instant whether or not the workers were stopped. The
    discriminating check is that the count does not move afterwards.
    """
    finished_jobs: list[int] = []
    lock = threading.Lock()

    def worker_fn(job, client):
        time.sleep(0.02)
        with lock:
            finished_jobs.append(job)
        return [job]

    gen = run_worker_pool(list(range(50)), worker_fn, _client_factory, concurrency=2, session_refresh_requests=100)
    next(gen)
    gen.close()

    with lock:
        completed_at_close = len(finished_jobs)

    # 50 jobs across 2 workers at 20ms each need ~0.5s to drain. Wait longer
    # than that: if the workers really stopped, the count cannot move.
    time.sleep(0.8)
    with lock:
        completed_later = len(finished_jobs)

    assert completed_later == completed_at_close
    assert completed_later < 50


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


def test_run_worker_pool_reraises_client_factory_failure_on_first_client():
    """A factory failure must not silently produce an empty, successful result."""
    def failing_factory():
        raise RuntimeError("cannot build client")

    def worker_fn(job, client):
        return [job]

    with pytest.raises(RuntimeError, match="cannot build client"):
        list(run_worker_pool(list(range(5)), worker_fn, failing_factory, concurrency=1, session_refresh_requests=100))


def test_run_worker_pool_reraises_client_factory_failure_on_session_refresh():
    """The refresh-path factory call is just as silent a failure point as the first."""
    calls = {"n": 0}

    def flaky_factory():
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("refresh failed")
        return _client_factory()

    def worker_fn(job, client):
        return [job]

    with pytest.raises(RuntimeError, match="refresh failed"):
        list(run_worker_pool(list(range(10)), worker_fn, flaky_factory, concurrency=1, session_refresh_requests=2))


def test_run_worker_pool_isolates_a_failing_job_and_still_yields_the_others():
    """One unreachable page must cost that page, not the whole brand.

    Written against the pre-fix code this FAILS: the pool drained the queue on
    the first exception and re-raised, so the surviving jobs never arrived.
    """
    def worker_fn(job, client):
        if job == 3:
            raise TimeoutError("timed out")
        return [job * 2]

    failures: list = []
    results = sorted(
        run_worker_pool(
            list(range(6)), worker_fn, _client_factory,
            concurrency=2, session_refresh_requests=100, failures=failures,
        )
    )

    assert results == [0, 2, 4, 8, 10]
    assert [f.job for f in failures] == [3]
    assert isinstance(failures[0].error, TimeoutError)


def test_run_worker_pool_still_aborts_everything_on_a_block():
    """A block is the one case where continuing makes things worse."""
    started = threading.Event()

    def worker_fn(job, client):
        started.set()
        raise BlockedError(403, f"https://example.test/{job}")

    failures: list = []
    with pytest.raises(BlockedError):
        list(
            run_worker_pool(
                list(range(20)), worker_fn, _client_factory,
                concurrency=1, session_refresh_requests=100, failures=failures,
            )
        )
    assert started.is_set()
    assert failures == []


def test_run_worker_pool_without_a_failures_list_still_isolates_the_job():
    """Callers that infer failure from missing results need no accumulator."""
    def worker_fn(job, client):
        if job == 1:
            raise ValueError("boom")
        return [job]

    assert sorted(
        run_worker_pool(
            [0, 1, 2], worker_fn, _client_factory,
            concurrency=1, session_refresh_requests=100,
        )
    ) == [0, 2]


def test_run_worker_pool_when_every_job_fails_yields_nothing_and_records_one_failure_each():
    """Central to the 29/07 isolation change, and nothing asserted it directly:
    a worker whose every job raises a non-BlockedError exception must not
    deadlock or crash the pool. It must still terminate cleanly, yield no
    results, and report exactly one JobFailure per job -- not fewer (lost
    failures) and not a fatal error escaping the pool."""
    def worker_fn(job, client):
        raise ValueError(f"boom-{job}")

    failures: list = []
    results = list(
        run_worker_pool(
            list(range(6)), worker_fn, _client_factory,
            concurrency=3, session_refresh_requests=100, failures=failures,
        )
    )

    assert results == []
    assert sorted(f.job for f in failures) == list(range(6))
    assert all(isinstance(f.error, ValueError) for f in failures)


def test_run_worker_pool_client_factory_failure_is_still_fatal():
    """A factory that cannot build a client is systemic, not a bad page."""
    def factory():
        raise RuntimeError("no client")

    def worker_fn(job, client):
        raise AssertionError("must not be called")

    with pytest.raises(RuntimeError):
        list(run_worker_pool([1, 2], worker_fn, factory, concurrency=1, session_refresh_requests=100))
