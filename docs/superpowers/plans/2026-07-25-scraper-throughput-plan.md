# Scraper Throughput: Concurrency, Year Filter, Light Camouflage — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the artificial 50-listing/run cap on detail-page enrichment, add configurable in-process thread concurrency for both the search and detail scraping phases, add a configurable registration-year floor to shrink scan volume, and add light anti-fingerprinting (User-Agent rotation, periodic session reset, adaptive backoff) — all within the existing single Python process, no new hosts or processes.

**Architecture:** A new generic `run_worker_pool` utility (`scraping/concurrency.py`) drains a list of jobs with N thread workers, each owning its own `RateLimitedClient` (created via a `client_factory`, refreshed every K requests), streaming results back to the caller as they complete, stopping all workers and re-raising on any worker exception. `crawler.py`'s `crawl_brand` becomes two-phase (parallel model discovery/probing, then parallel page fetching) built on this utility; `run_manager.py`'s detail backlog and sold-confirmation loops reuse the same utility instead of sequential single-client loops.

**Tech Stack:** Same as the existing project (Python 3.12, httpx, SQLAlchemy, pytest + respx) — no new dependencies. Concurrency uses a hand-rolled `queue.Queue` + `threading.Thread` pool (not `concurrent.futures`), for full control over per-worker session refresh and clean job-draining on stop.

## Global Constraints

- Search-query splitting criterion must NEVER be price — only model (`mmmv` param) and registration year (`fregfrom`/`fregto`).
- "Sold" status requires explicit detail-page confirmation — never inferred from absence in a sweep alone.
- **Every listing not already in the DB must have its detail page fetched during the very sweep that discovers it** — not deferred to a later run. (User's binding requirement, and the original spec's Pipeline B "solo annunci nuovi". The pre-existing `exclude_ids=new_ids` behavior violates this and is removed in Task 6.)
- The dashboard is the sole monitoring/notification channel — no email/Telegram.
- MVP brands (fixed): Fiat, Volkswagen, BMW, Audi, Mercedes-Benz.
- Single machine, single IP, **no IP rotation**. Concurrency here means multiple threads in the same process on the same IP — NOT multiple hosts, processes, or proxies.
- Explicitly OUT OF SCOPE: TLS/JA3 impersonation (curl_cffi), Playwright fallback, multi-process/multi-host workers coordinated via DB claim-and-lease.
- All new tunables (`SCRAPE_CONCURRENCY`, `SCRAPE_MAX_LISTING_AGE_YEARS`, `SCRAPE_SESSION_REFRESH_REQUESTS`) must be environment-configurable without code changes.
- **No test may make a real network request.** Every test that reaches a detail-fetch code path must inject `fetch_detail_fn`.
- Base URL: `https://www.autoscout24.it`.

---

## Task 1: Adaptive block-rate tracker (`rate_control.py`)

**Files:**
- Create: `scraper/src/autosmart24/scraping/rate_control.py`
- Create: `scraper/tests/test_rate_control.py`

**Interfaces:**
- Produces: `autosmart24.scraping.rate_control.BlockRateTracker` (class; constructor `BlockRateTracker(window_size: int = 100, threshold: float = 0.02, backoff_multiplier: float = 2.0, on_backoff_change: Callable[[float], None] | None = None)`; methods `record_success() -> None`, `record_blocked() -> None`, `delay_multiplier() -> float`) — consumed by `http_client.py` (Task 2) and `api/app.py` (Task 7).

The `on_backoff_change` callback is edge-triggered (fired only when the multiplier actually changes, not on every request) and is invoked **outside** the internal lock, so a slow callback (e.g. a DB write) cannot block scraping threads. Design §6 requires a `ScrapeEvent` warning when backoff engages; this callback is the hook that makes it possible.

- [ ] **Step 1: Write the failing tests**

`scraper/tests/test_rate_control.py`:

```python
from autosmart24.scraping.rate_control import BlockRateTracker


def test_block_rate_tracker_starts_at_normal_rate():
    tracker = BlockRateTracker()
    assert tracker.delay_multiplier() == 1.0


def test_block_rate_tracker_backs_off_when_threshold_exceeded():
    tracker = BlockRateTracker(window_size=10, threshold=0.2, backoff_multiplier=2.0)
    for _ in range(7):
        tracker.record_success()
    for _ in range(3):
        tracker.record_blocked()
    assert tracker.delay_multiplier() == 2.0


def test_block_rate_tracker_stays_normal_exactly_at_threshold():
    tracker = BlockRateTracker(window_size=10, threshold=0.2, backoff_multiplier=2.0)
    for _ in range(8):
        tracker.record_success()
    for _ in range(2):
        tracker.record_blocked()
    assert tracker.delay_multiplier() == 1.0


def test_block_rate_tracker_recovers_when_rate_drops():
    tracker = BlockRateTracker(window_size=10, threshold=0.2, backoff_multiplier=2.0)
    for _ in range(3):
        tracker.record_blocked()
    assert tracker.delay_multiplier() == 2.0
    for _ in range(10):
        tracker.record_success()
    assert tracker.delay_multiplier() == 1.0


def test_block_rate_tracker_window_limits_history():
    tracker = BlockRateTracker(window_size=5, threshold=0.5, backoff_multiplier=2.0)
    for _ in range(5):
        tracker.record_blocked()
    assert tracker.delay_multiplier() == 2.0
    for _ in range(5):
        tracker.record_success()
    assert tracker.delay_multiplier() == 1.0


def test_block_rate_tracker_fires_callback_only_on_transitions():
    seen: list[float] = []
    tracker = BlockRateTracker(
        window_size=10, threshold=0.2, backoff_multiplier=2.0, on_backoff_change=seen.append
    )

    for _ in range(10):
        tracker.record_blocked()
    assert seen == [2.0]

    for _ in range(10):
        tracker.record_success()
    assert seen == [2.0, 1.0]
```

Note the threshold semantics locked in by these tests: backoff engages when the block rate is **strictly greater than** the threshold (design §6: "Se il tasso **supera** una soglia"). `test_block_rate_tracker_stays_normal_exactly_at_threshold` pins the boundary so a future change to `>=` fails loudly instead of silently shifting behavior.

- [ ] **Step 2: Run to confirm failure**

Run: `cd scraper && pytest tests/test_rate_control.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autosmart24.scraping.rate_control'`

- [ ] **Step 3: Implement rate_control.py**

`scraper/src/autosmart24/scraping/rate_control.py`:

```python
from __future__ import annotations

import threading
from collections import deque
from typing import Callable


class BlockRateTracker:
    def __init__(
        self,
        window_size: int = 100,
        threshold: float = 0.02,
        backoff_multiplier: float = 2.0,
        on_backoff_change: Callable[[float], None] | None = None,
    ):
        self._threshold = threshold
        self._backoff_multiplier = backoff_multiplier
        self._on_backoff_change = on_backoff_change
        self._outcomes: deque[bool] = deque(maxlen=window_size)
        self._current_multiplier = 1.0
        self._lock = threading.Lock()

    def _record(self, blocked: bool) -> None:
        with self._lock:
            self._outcomes.append(blocked)
            block_rate = sum(self._outcomes) / len(self._outcomes)
            new_multiplier = self._backoff_multiplier if block_rate > self._threshold else 1.0
            changed = new_multiplier != self._current_multiplier
            self._current_multiplier = new_multiplier

        # Fired outside the lock: the callback may do slow work (e.g. a DB write)
        # and must never block scraping threads recording their outcomes.
        if changed and self._on_backoff_change is not None:
            self._on_backoff_change(new_multiplier)

    def record_success(self) -> None:
        self._record(False)

    def record_blocked(self) -> None:
        self._record(True)

    def delay_multiplier(self) -> float:
        with self._lock:
            return self._current_multiplier
```

- [ ] **Step 4: Run to confirm pass**

Run: `cd scraper && pytest tests/test_rate_control.py -v`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add scraper/src/autosmart24/scraping/rate_control.py scraper/tests/test_rate_control.py
git commit -m "Add thread-safe adaptive block-rate tracker with edge-triggered backoff callback"
```

---

## Task 2: User-Agent rotation, client factory, and rate-controller wiring (`http_client.py`)

**Files:**
- Modify: `scraper/src/autosmart24/scraping/http_client.py`
- Modify: `scraper/tests/test_http_client.py`

**Interfaces:**
- Consumes: `BlockRateTracker` (Task 1).
- Produces: `autosmart24.scraping.http_client.USER_AGENTS: list[str]`, `.make_client(min_delay_seconds: float, max_delay_seconds: float, rate_controller: BlockRateTracker | None = None, sleep_fn: Callable[[float], None] = time.sleep) -> RateLimitedClient`, and an updated `RateLimitedClient` with new fields `user_agent: str` and `rate_controller: BlockRateTracker | None` — consumed by `concurrency.py` (Task 3), `crawler.py` (Task 4), `run_manager.py` (Tasks 5-6), `api/app.py` (Task 7).

- [ ] **Step 1: Read the current file**

Read `scraper/src/autosmart24/scraping/http_client.py` in full before editing. Confirm the current `RateLimitedClient` is a dataclass with a `client: httpx.Client = field(default_factory=...)` and a module-level `USER_AGENT` constant (singular).

Then confirm nothing constructs the client with an explicit `client=` kwarg (the rewrite makes that field `init=False`):

Run: `cd scraper && grep -rn "RateLimitedClient(" src tests`
Expected: several matches, **none** passing `client=`. If any does, stop and report NEEDS_CONTEXT.

- [ ] **Step 2: Write the failing tests**

Add to `scraper/tests/test_http_client.py`, keeping all 4 existing tests and the `_instant_client` helper unchanged. Add these imports to the existing import block at the top (`httpx`, `pytest`, `respx`, and `BlockedError`/`RateLimitedClient` are already imported — add only what's missing):

```python
from autosmart24.scraping.http_client import USER_AGENTS, make_client
from autosmart24.scraping.rate_control import BlockRateTracker
```

Then append these tests:

```python
def test_make_client_picks_a_user_agent_from_the_pool():
    client = make_client(min_delay_seconds=0, max_delay_seconds=0, sleep_fn=lambda _: None)
    try:
        assert client.user_agent in USER_AGENTS
    finally:
        client.close()


def test_make_client_rotates_user_agents_across_many_calls():
    clients = [make_client(0, 0, sleep_fn=lambda _: None) for _ in range(50)]
    try:
        assert len({c.user_agent for c in clients}) > 1
    finally:
        for c in clients:
            c.close()


@respx.mock
def test_get_records_success_on_rate_controller():
    respx.get("https://example.test/ok").mock(return_value=httpx.Response(200, text="ok"))
    tracker = BlockRateTracker()
    client = RateLimitedClient(
        min_delay_seconds=0, max_delay_seconds=0, sleep_fn=lambda _: None, rate_controller=tracker
    )
    client.get("https://example.test/ok")
    assert tracker.delay_multiplier() == 1.0


@respx.mock
def test_get_records_blocked_on_rate_controller_and_applies_backoff_multiplier():
    respx.get("https://example.test/blocked").mock(return_value=httpx.Response(403, text="forbidden"))
    tracker = BlockRateTracker(window_size=10, threshold=0.05, backoff_multiplier=2.0)
    delays: list[float] = []
    client = RateLimitedClient(
        min_delay_seconds=10, max_delay_seconds=10,
        sleep_fn=lambda d: delays.append(d), rate_controller=tracker,
    )
    with pytest.raises(BlockedError):
        client.get("https://example.test/blocked")
    assert tracker.delay_multiplier() == 2.0
    with pytest.raises(BlockedError):
        client.get("https://example.test/blocked")
    assert delays[-1] == 20.0
```

- [ ] **Step 3: Run to confirm failure**

Run: `cd scraper && pytest tests/test_http_client.py -v`
Expected: FAIL with `ImportError: cannot import name 'USER_AGENTS' from 'autosmart24.scraping.http_client'`

- [ ] **Step 4: Rewrite http_client.py**

`scraper/src/autosmart24/scraping/http_client.py` (full replacement):

```python
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Callable

import httpx

from autosmart24.scraping.rate_control import BlockRateTracker

USER_AGENTS: list[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

BLOCK_STATUS_CODES = {403, 429}


class BlockedError(Exception):
    def __init__(self, status_code: int, url: str):
        super().__init__(f"Blocked with status {status_code} fetching {url}")
        self.status_code = status_code
        self.url = url


@dataclass
class RateLimitedClient:
    min_delay_seconds: float = 3.0
    max_delay_seconds: float = 8.0
    user_agent: str = USER_AGENTS[0]
    rate_controller: BlockRateTracker | None = None
    sleep_fn: Callable[[float], None] = field(default=time.sleep)
    client: httpx.Client = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.client = httpx.Client(
            headers={"User-Agent": self.user_agent, "Accept-Language": "it-IT,it;q=0.9"},
            timeout=15.0,
            follow_redirects=True,
        )

    def get(self, url: str) -> httpx.Response:
        multiplier = self.rate_controller.delay_multiplier() if self.rate_controller else 1.0
        delay = random.uniform(self.min_delay_seconds, self.max_delay_seconds) * multiplier
        self.sleep_fn(delay)
        response = self.client.get(url)
        if response.status_code in BLOCK_STATUS_CODES:
            if self.rate_controller:
                self.rate_controller.record_blocked()
            raise BlockedError(response.status_code, url)
        if self.rate_controller:
            self.rate_controller.record_success()
        response.raise_for_status()
        return response

    def close(self) -> None:
        self.client.close()


def make_client(
    min_delay_seconds: float,
    max_delay_seconds: float,
    rate_controller: BlockRateTracker | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> RateLimitedClient:
    return RateLimitedClient(
        min_delay_seconds=min_delay_seconds,
        max_delay_seconds=max_delay_seconds,
        user_agent=random.choice(USER_AGENTS),
        rate_controller=rate_controller,
        sleep_fn=sleep_fn,
    )
```

Two notes on the dataclass: `client` becomes `field(init=False, repr=False)` and is built in `__post_init__`, because it must depend on `self.user_agent` (a `default_factory` cannot reference sibling fields). `user_agent` uses a plain immutable string default (not `default_factory`), which is safe since `str` is immutable.

- [ ] **Step 5: Run to confirm pass**

Run: `cd scraper && pytest tests/test_http_client.py -v`
Expected: `8 passed` (4 pre-existing + 4 new)

- [ ] **Step 6: Commit**

```bash
git add scraper/src/autosmart24/scraping/http_client.py scraper/tests/test_http_client.py
git commit -m "Add User-Agent rotation, client factory, and adaptive-backoff wiring to RateLimitedClient"
```

---

## Task 3: Generic concurrent worker pool (`concurrency.py`)

The core reusable primitive: given a list of jobs and a per-job worker function, runs them across N threads (each with its own `RateLimitedClient` from a factory, refreshed every K jobs), streaming results back as they complete.

**Three failure modes this design must handle correctly** — each one caused a critical defect in an earlier draft of this plan, so they are called out explicitly:

1. **Any worker exception must reach the caller.** Catching only `BlockedError` and letting other exceptions kill the thread silently truncates the job list while the caller sees a normal, successful completion — silent data loss. All exceptions are captured and re-raised after the pool drains (`BlockedError` takes priority when several occur, since it is the actionable one).
2. **Abandoning the generator must stop the workers.** Without a `try/finally`, a `GeneratorExit` (raised when the consumer stops early, e.g. because the caller's own loop body raised) leaves non-daemon threads hammering the site with no consumer. The `finally` sets a stop event, drains the queue, and joins.
3. **`concurrency <= 0` must not silently drop every job.** `max(1, concurrency)` guards a misconfigured `SCRAPE_CONCURRENCY=0`, which would otherwise spawn zero threads and complete instantly with zero results.

**Files:**
- Create: `scraper/src/autosmart24/scraping/concurrency.py`
- Create: `scraper/tests/test_concurrency.py`

**Interfaces:**
- Consumes: `RateLimitedClient`, `BlockedError` (`http_client.py`).
- Produces: `autosmart24.scraping.concurrency.run_worker_pool(jobs: list[JobT], worker_fn: Callable[[JobT, RateLimitedClient], list[ResultT]], client_factory: Callable[[], RateLimitedClient], concurrency: int, session_refresh_requests: int) -> Iterator[ResultT]` — consumed by `crawler.py` (Task 4), `run_manager.py` (Tasks 5-6).

- [ ] **Step 1: Write the failing tests**

`scraper/tests/test_concurrency.py`:

```python
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
        completed_at_close = len(finished_jobs)

    # 50 jobs across 2 workers at 20ms each need ~0.5s to drain. Wait longer
    # than that: if the workers really stopped, the count cannot move.
    # Asserting only on the count at close() time would be vacuous — barely any
    # jobs finish in that instant whether or not the workers were stopped.
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
```

`test_run_worker_pool_runs_jobs_concurrently` uses a `threading.Barrier(4)`: it can only pass if 4 workers are genuinely running at the same time (otherwise the barrier times out and the test fails fast). This is deterministic, unlike asserting on wall-clock start-time spread.

- [ ] **Step 2: Run to confirm failure**

Run: `cd scraper && pytest tests/test_concurrency.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autosmart24.scraping.concurrency'`

- [ ] **Step 3: Implement concurrency.py**

`scraper/src/autosmart24/scraping/concurrency.py`:

```python
from __future__ import annotations

import queue
import threading
from typing import Callable, Iterator, TypeVar

from autosmart24.scraping.http_client import BlockedError, RateLimitedClient

JobT = TypeVar("JobT")
ResultT = TypeVar("ResultT")


def run_worker_pool(
    jobs: list[JobT],
    worker_fn: Callable[[JobT, RateLimitedClient], list[ResultT]],
    client_factory: Callable[[], RateLimitedClient],
    concurrency: int,
    session_refresh_requests: int,
) -> Iterator[ResultT]:
    if not jobs:
        return

    concurrency = max(1, concurrency)
    session_refresh_requests = max(1, session_refresh_requests)

    job_queue: "queue.Queue[JobT]" = queue.Queue()
    for job in jobs:
        job_queue.put(job)

    results: "queue.Queue[object]" = queue.Queue()
    done_marker = object()
    error_holder: list[BaseException] = []
    error_lock = threading.Lock()
    stop = threading.Event()

    def _drain_queue() -> None:
        while True:
            try:
                job_queue.get_nowait()
            except queue.Empty:
                return

    def worker() -> None:
        client: RateLimitedClient | None = None
        try:
            client = client_factory()
            processed = 0
            while not stop.is_set():
                try:
                    job = job_queue.get_nowait()
                except queue.Empty:
                    return
                if processed >= session_refresh_requests:
                    client.close()
                    client = client_factory()
                    processed = 0
                job_results = worker_fn(job, client)
                processed += 1
                for item in job_results:
                    results.put(item)
        except BaseException as exc:
            # Captured and re-raised to the caller. Letting it kill the thread
            # would silently truncate the job list while the caller sees a
            # normal completion. This must cover client_factory() too, not just
            # worker_fn: a factory failure is just as silent.
            with error_lock:
                error_holder.append(exc)
            _drain_queue()
        finally:
            if client is not None:
                client.close()

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(concurrency)]
    for t in threads:
        t.start()

    def _wait_then_signal_done() -> None:
        for t in threads:
            t.join()
        results.put(done_marker)

    watcher = threading.Thread(target=_wait_then_signal_done, daemon=True)
    watcher.start()

    try:
        while True:
            item = results.get()
            if item is done_marker:
                break
            yield item
    finally:
        # Also runs on GeneratorExit when the consumer abandons us: workers must
        # not keep fetching with nobody consuming the results.
        stop.set()
        _drain_queue()
        for t in threads:
            t.join()
        watcher.join()

    if error_holder:
        blocked = [exc for exc in error_holder if isinstance(exc, BlockedError)]
        raise blocked[0] if blocked else error_holder[0]
```

- [ ] **Step 4: Run to confirm pass**

Run: `cd scraper && pytest tests/test_concurrency.py -v`
Expected: `10 passed`

- [ ] **Step 5: Commit**

```bash
git add scraper/src/autosmart24/scraping/concurrency.py scraper/tests/test_concurrency.py
git commit -m "Add thread-pool job runner with per-worker client refresh, error propagation, and clean shutdown"
```

---

## Task 4: Two-phase parallel crawl with year-floor filter (`crawler.py`)

**Files:**
- Modify: `scraper/src/autosmart24/scraping/crawler.py`
- Modify: `scraper/tests/test_crawler.py`

**Interfaces:**
- Consumes: `run_worker_pool` (Task 3), `RateLimitedClient`, `BlockedError` (`http_client.py`).
- Produces: `autosmart24.scraping.crawler.crawl_brand(client_factory: Callable[[], RateLimitedClient], brand_slug: str, make_id: int, year_from: int | None = None, concurrency: int = 1, session_refresh_requests: int = 30) -> Iterator[dict]` (signature change: first param is now a factory, not a client; three new optional params) — consumed by `run_manager.py` (Task 5).
- Also produces: `autosmart24.scraping.crawler.QueryUnit` (dataclass: `model_id: int`, `year_from: int | None`, `year_to: int | None`, `number_of_pages: int`).

- [ ] **Step 1: Read the current file**

Read `scraper/src/autosmart24/scraping/crawler.py` in full — confirm `MIN_YEAR = 1950`, `MAX_YEAR = 2027`, `ModelInfo`, `discover_models`, `_count_for_year_range`, `_iter_listings_from_page` are present.

- [ ] **Step 2: Write the failing tests**

In `scraper/tests/test_crawler.py`, add to the existing imports at the top:

```python
import pytest

from autosmart24.scraping.http_client import BlockedError
```

(`RateLimitedClient` is already imported; do not duplicate it.)

In BOTH existing test functions (`test_crawl_brand_yields_all_listings_across_pages` and `test_crawl_brand_splits_by_year_when_model_exceeds_threshold`), replace this exact two-line block:

```python
    client = RateLimitedClient(min_delay_seconds=0, max_delay_seconds=0, sleep_fn=lambda _: None)
    results = list(crawl_brand(client, "fiat", 28))
```

with:

```python
    def client_factory():
        return RateLimitedClient(min_delay_seconds=0, max_delay_seconds=0, sleep_fn=lambda _: None)

    results = list(crawl_brand(client_factory, "fiat", 28, concurrency=1))
```

Everything else in those two tests (mock setup, assertions) stays unchanged.

Then append these three new tests:

```python
@respx.mock
def test_crawl_brand_applies_year_from_floor():
    discovery_page_props = {
        "numberOfResults": 1,
        "numberOfPages": 1,
        "listings": [_fake_listing("discovery-1", 1000)],
        "taxonomy": {"models": {"28": [{"value": 1746, "label": "Panda"}]}},
    }
    filtered_page_props = {
        "numberOfResults": 1,
        "numberOfPages": 1,
        "listings": [_fake_listing("recent-1", 9000)],
    }

    discovery_url = build_search_url("fiat", page=1, make_id=28)
    filtered_url = build_search_url("fiat", page=1, make_id=28, model_id=1746, year_from=2021)

    respx.get(discovery_url).mock(return_value=httpx.Response(200, text=_next_data_html(discovery_page_props)))
    respx.get(filtered_url).mock(return_value=httpx.Response(200, text=_next_data_html(filtered_page_props)))

    def client_factory():
        return RateLimitedClient(min_delay_seconds=0, max_delay_seconds=0, sleep_fn=lambda _: None)

    results = list(crawl_brand(client_factory, "fiat", 28, year_from=2021, concurrency=1))

    assert {r["id"] for r in results} == {"recent-1"}


@respx.mock
def test_crawl_brand_stops_cleanly_on_blocked_error_during_parallel_fetch():
    discovery_page_props = {
        "numberOfResults": 1,
        "numberOfPages": 1,
        "listings": [_fake_listing("discovery-1", 1000)],
        "taxonomy": {"models": {"28": [{"value": 1746, "label": "Panda"}]}},
    }
    model_page1_props = {
        "numberOfResults": 21,
        "numberOfPages": 2,
        "listings": [_fake_listing(f"p1-{i}", 10000 + i) for i in range(20)],
    }

    discovery_url = build_search_url("fiat", page=1, make_id=28)
    page1_url = build_search_url("fiat", page=1, make_id=28, model_id=1746)
    page2_url = build_search_url("fiat", page=2, make_id=28, model_id=1746)

    respx.get(discovery_url).mock(return_value=httpx.Response(200, text=_next_data_html(discovery_page_props)))
    respx.get(page1_url).mock(return_value=httpx.Response(200, text=_next_data_html(model_page1_props)))
    respx.get(page2_url).mock(return_value=httpx.Response(403, text="forbidden"))

    def client_factory():
        return RateLimitedClient(min_delay_seconds=0, max_delay_seconds=0, sleep_fn=lambda _: None)

    with pytest.raises(BlockedError):
        list(crawl_brand(client_factory, "fiat", 28, concurrency=2))


@respx.mock
def test_crawl_brand_fetches_pages_of_multiple_models_in_parallel():
    discovery_page_props = {
        "numberOfResults": 1,
        "numberOfPages": 1,
        "listings": [],
        "taxonomy": {"models": {"28": [
            {"value": 1746, "label": "Panda"},
            {"value": 1747, "label": "Punto"},
        ]}},
    }
    model_props = {
        "numberOfResults": 21,
        "numberOfPages": 2,
        "listings": [_fake_listing("shared-1", 10000)],
    }

    respx.get(build_search_url("fiat", page=1, make_id=28)).mock(
        return_value=httpx.Response(200, text=_next_data_html(discovery_page_props))
    )
    for model_id in (1746, 1747):
        for page in (1, 2):
            respx.get(build_search_url("fiat", page=page, make_id=28, model_id=model_id)).mock(
                return_value=httpx.Response(200, text=_next_data_html(model_props))
            )

    def client_factory():
        return RateLimitedClient(min_delay_seconds=0, max_delay_seconds=0, sleep_fn=lambda _: None)

    results = list(crawl_brand(client_factory, "fiat", 28, concurrency=2))

    # 2 models x (page 1 + page 2), one listing each
    assert len(results) == 4
```

- [ ] **Step 3: Run to confirm failure**

Run: `cd scraper && pytest tests/test_crawler.py -v`
Expected: FAIL — `crawl_brand` does not yet accept a factory or the new keyword arguments.

- [ ] **Step 4: Rewrite crawler.py**

`scraper/src/autosmart24/scraping/crawler.py` (full replacement):

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterator

from autosmart24.config import MAX_RESULTS_PER_QUERY
from autosmart24.scraping.concurrency import run_worker_pool
from autosmart24.scraping.http_client import RateLimitedClient
from autosmart24.scraping.next_data import extract_next_data
from autosmart24.scraping.search_query import build_search_url
from autosmart24.scraping.snippet_mapper import map_snippet_listing
from autosmart24.scraping.year_split import split_year_ranges

MIN_YEAR = 1950
MAX_YEAR = 2027


@dataclass
class ModelInfo:
    model_id: int
    label: str


@dataclass
class QueryUnit:
    model_id: int
    year_from: int | None
    year_to: int | None
    number_of_pages: int


def fetch_page_data(client: RateLimitedClient, url: str) -> dict:
    response = client.get(url)
    data = extract_next_data(response.text)
    return data["props"]["pageProps"]


def discover_models(client: RateLimitedClient, brand_slug: str, make_id: int) -> list[ModelInfo]:
    url = build_search_url(brand_slug, page=1, make_id=make_id)
    page_props = fetch_page_data(client, url)
    raw_models = page_props["taxonomy"]["models"].get(str(make_id), [])
    return [ModelInfo(model_id=m["value"], label=m["label"]) for m in raw_models]


def _count_for_year_range(
    client: RateLimitedClient, brand_slug: str, make_id: int, model_id: int, year_from: int, year_to: int
) -> int:
    url = build_search_url(
        brand_slug, page=1, make_id=make_id, model_id=model_id, year_from=year_from, year_to=year_to
    )
    return fetch_page_data(client, url)["numberOfResults"]


def _iter_listings_from_page(page_props: dict) -> Iterator[dict]:
    for raw_listing in page_props["listings"]:
        yield map_snippet_listing(raw_listing)


def _discover_model_units(
    model: ModelInfo,
    client: RateLimitedClient,
    brand_slug: str,
    make_id: int,
    year_from: int | None,
) -> list[tuple[QueryUnit, list[dict]]]:
    """Probe one model: learn how many pages it has (page 1's listings are
    returned too, not wasted), splitting by year range if it still exceeds the
    pagination cap even with the year floor applied."""
    probe_url = build_search_url(brand_slug, page=1, make_id=make_id, model_id=model.model_id, year_from=year_from)
    probe_page_props = fetch_page_data(client, probe_url)

    if probe_page_props["numberOfResults"] <= MAX_RESULTS_PER_QUERY:
        unit = QueryUnit(model.model_id, year_from, None, probe_page_props["numberOfPages"])
        return [(unit, list(_iter_listings_from_page(probe_page_props)))]

    floor_year = year_from if year_from is not None else MIN_YEAR
    year_ranges = split_year_ranges(
        lambda yf, yt: _count_for_year_range(client, brand_slug, make_id, model.model_id, yf, yt),
        floor_year,
        MAX_YEAR,
        MAX_RESULTS_PER_QUERY,
    )
    out: list[tuple[QueryUnit, list[dict]]] = []
    for yf, yt in year_ranges:
        sub_url = build_search_url(
            brand_slug, page=1, make_id=make_id, model_id=model.model_id, year_from=yf, year_to=yt
        )
        sub_page_props = fetch_page_data(client, sub_url)
        unit = QueryUnit(model.model_id, yf, yt, sub_page_props["numberOfPages"])
        out.append((unit, list(_iter_listings_from_page(sub_page_props))))
    return out


def crawl_brand(
    client_factory: Callable[[], RateLimitedClient],
    brand_slug: str,
    make_id: int,
    year_from: int | None = None,
    concurrency: int = 1,
    session_refresh_requests: int = 30,
) -> Iterator[dict]:
    bootstrap_client = client_factory()
    try:
        models = discover_models(bootstrap_client, brand_slug, make_id)
    finally:
        bootstrap_client.close()

    def _discovery_worker(model: ModelInfo, client: RateLimitedClient) -> list[tuple[QueryUnit, list[dict]]]:
        return _discover_model_units(model, client, brand_slug, make_id, year_from)

    # Phase 1: probe every model in parallel. This loop fully drains before
    # phase 2 starts, so `units` is complete when the page job list is built.
    units: list[QueryUnit] = []
    for unit, listings in run_worker_pool(
        models, _discovery_worker, client_factory, concurrency, session_refresh_requests
    ):
        units.append(unit)
        yield from listings

    # Phase 2: fetch every remaining page (2..N) of every unit in parallel.
    page_jobs: list[tuple[int, int | None, int | None, int]] = []
    for unit in units:
        for page in range(2, unit.number_of_pages + 1):
            page_jobs.append((unit.model_id, unit.year_from, unit.year_to, page))

    def _page_worker(job: tuple[int, int | None, int | None, int], client: RateLimitedClient) -> list[dict]:
        model_id, yf, yt, page = job
        url = build_search_url(brand_slug, page=page, make_id=make_id, model_id=model_id, year_from=yf, year_to=yt)
        return list(_iter_listings_from_page(fetch_page_data(client, url)))

    yield from run_worker_pool(page_jobs, _page_worker, client_factory, concurrency, session_refresh_requests)
```

- [ ] **Step 5: Run to confirm pass**

Run: `cd scraper && pytest tests/test_crawler.py -v`
Expected: `5 passed` (2 pre-existing, updated + 3 new)

- [ ] **Step 6: Commit**

```bash
git add scraper/src/autosmart24/scraping/crawler.py scraper/tests/test_crawler.py
git commit -m "Rewrite crawl_brand as two-phase parallel discovery+fetch with year-floor filter"
```

---

## Task 5: Plumbing — `client_factory` and concurrency through `run_manager.py` (no behavior change)

Deliberately split from Task 6: this task changes *how* the existing work is done (factory instead of a shared client, worker pool instead of sequential loops) without changing *what* gets done. Every one of the 19 existing tests must still pass with its assertions unchanged. Task 6 then changes behavior on top of a green baseline.

**Files:**
- Modify: `scraper/src/autosmart24/run_manager.py`
- Modify: `scraper/tests/test_run_manager.py`

**Interfaces:**
- Consumes: `run_worker_pool` (Task 3), `crawl_brand` (Task 4, new signature).
- Produces: `autosmart24.run_manager.run_brand_sweep(session, client_factory, brand, crawl_fn=crawl_brand, fetch_detail_fn=fetch_detail, batch_size=NEW_LISTING_COMMIT_BATCH_SIZE, concurrency: int = 1, year_from: int | None = None, session_refresh_requests: int = 30) -> ScrapeRun` and `.process_detail_backlog(session, client_factory, brand, run, concurrency: int = 1, session_refresh_requests: int = 30, batch_size=DETAIL_BATCH_SIZE, fetch_detail_fn=fetch_detail, exclude_ids=frozenset()) -> int` (2nd positional param of both is now a factory).

- [ ] **Step 1: Read the current file**

Read `scraper/src/autosmart24/run_manager.py` in full. Confirm: `DETAIL_BATCH_SIZE = 50`, `NEW_LISTING_COMMIT_BATCH_SIZE = 100`, the `_iter_batches` helper, per-batch `session.commit()` in the crawl loop with accumulators updated *after* the commit, and the `except BlockedError` / `except Exception` structure at the end of `run_brand_sweep`.

- [ ] **Step 2: Migrate the existing tests**

In `scraper/tests/test_run_manager.py`:

**2a.** First verify the count, then substitute. Run: `cd scraper && grep -c "    def fake_crawl(client, brand_slug, make_id):" tests/test_run_manager.py`
Expected: `15`

Then Edit with `replace_all=true`:
- Old: `    def fake_crawl(client, brand_slug, make_id):`
- New: `    def fake_crawl(client, brand_slug, make_id, **kwargs):`

(`**kwargs` absorbs the `year_from=`/`concurrency=`/`session_refresh_requests=` keyword arguments that `run_brand_sweep` now always passes to `crawl_fn`. The first parameter keeps the name `client` for a minimal diff; it now receives the factory, which these fakes never call.)

**2b.** First verify the count, then substitute. Run: `cd scraper && grep -c ", _client(), BRAND" tests/test_run_manager.py`
Expected: `16`

Then Edit with `replace_all=true`:
- Old: `, _client(), BRAND`
- New: `, _client, BRAND`

**Do NOT** use `_client()` alone as the search string: it also matches the helper's own definition on line 15 (`def _client() -> RateLimitedClient:`), and replacing it would produce `def _client -> RateLimitedClient:` — a `SyntaxError` that breaks the entire file. Anchoring on `, _client(), BRAND` matches only the 16 call sites.

The bare `_client` (the function itself, uncalled) already has exactly the `Callable[[], RateLimitedClient]` shape the new `client_factory` parameter expects — no new helper is needed.

**2c.** Add this shared fake-detail helper right after the `_existing_listing` helper near the top of the file. Task 6 needs it, and putting it here keeps Task 6's diff focused:

```python
def _fake_detail_data(listing_id: str) -> dict:
    return {
        "price": None, "power_kw": None, "power_cv": None, "displacement_ccm": None,
        "body_type": None, "body_color": None, "num_seats": None, "num_doors": None,
        "num_previous_owners": None, "province": None, "latitude": None, "longitude": None,
        "vat_exposed": None, "price_evaluation_category": None, "price_evaluation_median": None,
        "created_at_source": None, "raw_detail": {"id": listing_id},
    }


def _noop_fetch_detail(client, url):
    """Detail fetch that always reports 'still active' and enriches nothing.
    Used by tests that reach the detail phase but do not assert on its results —
    without it those tests would issue real network requests."""
    return DetailResult(sold=False, data=_fake_detail_data(url))
```

**2d.** Add this new test at the end of the file:

```python
def test_run_brand_sweep_threads_year_from_and_concurrency_to_crawl_fn(db_session):
    received = {}

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        received.update(kwargs)
        return iter([])

    run_brand_sweep(db_session, _client, BRAND, crawl_fn=fake_crawl, year_from=2021, concurrency=4)

    assert received["year_from"] == 2021
    assert received["concurrency"] == 4
```

- [ ] **Step 3: Run to confirm failure**

Run: `cd scraper && pytest tests/test_run_manager.py -v`
Expected: FAIL — `run_brand_sweep` does not yet accept `year_from=`/`concurrency=`, and the old code treats its 2nd argument as a client instance while the tests now pass a factory function.

- [ ] **Step 4: Update run_manager.py**

Make these four changes to `scraper/src/autosmart24/run_manager.py`. Do not change anything else — no behavior changes in this task.

**4a.** Update the imports block at the top to add `Callable` and `run_worker_pool`:

```python
from __future__ import annotations

import datetime as dt
import itertools
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from autosmart24.config import BrandConfig
from autosmart24.db.models import Listing, PriceHistory, ScrapeEvent, ScrapeRun
from autosmart24.scraping.change_detection import diff_sweep
from autosmart24.scraping.concurrency import run_worker_pool
from autosmart24.scraping.crawler import crawl_brand
from autosmart24.scraping.detail_queue import fetch_detail
from autosmart24.scraping.http_client import BlockedError, RateLimitedClient
```

**4b.** Change `process_detail_backlog`'s signature from its current form to:

```python
def process_detail_backlog(
    session: Session,
    client_factory: Callable[[], RateLimitedClient],
    brand: BrandConfig,
    run: ScrapeRun,
    concurrency: int = 1,
    session_refresh_requests: int = 30,
    batch_size: int = DETAIL_BATCH_SIZE,
    fetch_detail_fn=fetch_detail,
    exclude_ids: set[str] = frozenset(),
) -> int:
```

and replace its sequential `for row in pending:` loop with a worker pool. The body from `if not pending: return 0` onward becomes:

```python
    if not pending:
        return 0

    rows_by_id = {row.id: row for row in pending}
    jobs = [(row.id, row.url) for row in pending]
    enriched = 0
    sold = 0
    now = _now()

    def _detail_worker(job: tuple[str, str], client: RateLimitedClient) -> list[tuple[str, object]]:
        listing_id, url = job
        return [(listing_id, fetch_detail_fn(client, url))]

    try:
        for listing_id, result in run_worker_pool(
            jobs, _detail_worker, client_factory, concurrency, session_refresh_requests
        ):
            row = rows_by_id[listing_id]
            row.last_checked_at = now
            if result.sold:
                row.status = "sold"
                row.sold_at = now
                sold += 1
                continue

            detail = result.data
            if detail["price"] is not None and detail["price"] != row.price:
                row.price = detail["price"]
                session.add(PriceHistory(listing_id=row.id, price=detail["price"], recorded_at=now))

            row.power_kw = detail["power_kw"]
            row.power_cv = detail["power_cv"]
            row.displacement_ccm = detail["displacement_ccm"]
            row.body_type = detail["body_type"]
            row.body_color = detail["body_color"]
            row.num_seats = detail["num_seats"]
            row.num_doors = detail["num_doors"]
            row.num_previous_owners = detail["num_previous_owners"]
            row.province = detail["province"]
            row.latitude = detail["latitude"]
            row.longitude = detail["longitude"]
            row.vat_exposed = detail["vat_exposed"]
            row.price_evaluation_category = detail["price_evaluation_category"]
            row.price_evaluation_median = detail["price_evaluation_median"]
            row.created_at_source = detail["created_at_source"]
            row.raw_detail = detail["raw_detail"]
            row.detail_scraped = True
            enriched += 1
    except BlockedError as exc:
        run.status = "blocked"
        run.errors_count += 1
        _log_event(session, run, "blocked", str(exc), url=exc.url)
        return sold

    _log_event(
        session, run, "info",
        f"Detail backlog batch: enriched {enriched}, confirmed sold {sold} (batch size {len(pending)})",
    )
    return sold
```

(The `pending` query above it, with `.limit(batch_size)`, is unchanged in this task.)

**4c.** Change `run_brand_sweep`'s signature to:

```python
def run_brand_sweep(
    session: Session,
    client_factory: Callable[[], RateLimitedClient],
    brand: BrandConfig,
    crawl_fn=crawl_brand,
    fetch_detail_fn=fetch_detail,
    batch_size: int = NEW_LISTING_COMMIT_BATCH_SIZE,
    concurrency: int = 1,
    year_from: int | None = None,
    session_refresh_requests: int = 30,
) -> ScrapeRun:
```

and update its `crawl_fn` call to pass the new arguments:

```python
        for batch in _iter_batches(
            crawl_fn(
                client_factory, brand.slug, brand.make_id,
                year_from=year_from, concurrency=concurrency,
                session_refresh_requests=session_refresh_requests,
            ),
            batch_size,
        ):
```

**4d.** Replace the sequential `for listing_id in diff.missing_ids:` / `for listing_id in missing_ids:` sold-confirmation loop with a worker pool. Note the URLs are extracted into the job tuples **on the main thread** — worker threads must never touch ORM instances, since the `Session` is not thread-safe:

```python
        missing_ids = set(active_db_prices.keys()) - seen_ids
        now = _now()
        sold_count = 0
        missing_jobs = [(listing_id, active_rows_by_id[listing_id].url) for listing_id in missing_ids]

        def _missing_worker(job: tuple[str, str], client: RateLimitedClient) -> list[tuple[str, object]]:
            listing_id, url = job
            return [(listing_id, fetch_detail_fn(client, url))]

        try:
            for listing_id, result in run_worker_pool(
                missing_jobs, _missing_worker, client_factory, concurrency, session_refresh_requests
            ):
                row = active_rows_by_id[listing_id]
                row.last_checked_at = now
                if result.sold:
                    row.status = "sold"
                    row.sold_at = now
                    sold_count += 1
                else:
                    run.errors_count += 1
                    _log_event(
                        session, run, "warning",
                        f"Listing {listing_id} not found in sweep but still active on detail page",
                        url=row.url,
                    )
        except BlockedError as exc:
            run.status = "blocked"
            run.errors_count += 1
            _log_event(session, run, "blocked", str(exc), url=exc.url)
```

Then update the `process_detail_backlog(...)` call below it to pass the factory and concurrency:

```python
        backlog_sold_count = process_detail_backlog(
            session, client_factory, brand, run,
            concurrency=concurrency, session_refresh_requests=session_refresh_requests,
            fetch_detail_fn=fetch_detail_fn, exclude_ids=new_ids,
        )
```

- [ ] **Step 5: Run the run_manager tests**

Run: `cd scraper && pytest tests/test_run_manager.py -v`
Expected: `18 passed` (17 pre-existing, migrated + 1 new)

- [ ] **Step 6: Run the full backend suite**

Run: `cd scraper && python -m pytest -q`
Expected: all pass. If any test issues a real network request (visible as a hang or a connection error), stop and report — a fake `fetch_detail_fn` is missing somewhere.

- [ ] **Step 7: Commit**

```bash
git add scraper/src/autosmart24/run_manager.py scraper/tests/test_run_manager.py
git commit -m "Thread client_factory and concurrency through run_manager; run detail and sold-confirmation fetches on the worker pool"
```

---

## Task 6: Uncap the detail backlog, fetch details for same-sweep new listings, scope to the year floor

The behavior changes, on top of Task 5's green baseline. Three distinct changes, all in `run_manager.py`:

**A. Uncap the backlog.** `process_detail_backlog` currently handles at most `batch_size` (50) listings per run. It must instead loop until no pending listings remain, committing after each page so progress is durable. A `failed_ids` set guarantees termination: any row the pool did not report on is parked for the remainder of the call, so the `LIMIT`-ed query can never re-select the same unprocessable row forever. Without this, one permanently-failing detail page becomes an infinite loop hammering the site.

**B. Stop excluding same-sweep new listings.** `run_brand_sweep` passes `exclude_ids=new_ids`, so listings discovered in this sweep are skipped by this sweep's backlog — their details are collected only on the *next* run, 4 days later. On a cold brand, sweep 1 inserts ~45k listings and enriches zero. This violates the binding requirement (detail fetched in the sweep that discovers the listing) and inverts the original spec's Pipeline B ("solo annunci nuovi"). Ordering is safe: `process_detail_backlog` runs after the batch loop has committed every new listing, so they are visible to its query.

**C. Scope the sweep to the year floor.** `missing_ids` is computed against *all* active rows for the brand. Once `year_from` narrows the searches, every stored listing registered before the floor stops appearing in sweeps permanently — so each one is treated as "missing", gets a detail fetch, is found still active, and logs a warning plus an `errors_count` increment, on every run forever (~5k wasted requests and ~5k bogus warnings per Fiat run). The active-inventory query and the backlog query must both respect the same floor. Rows with an unknown `first_registration` are kept in scope rather than silently dropped.

**Files:**
- Modify: `scraper/src/autosmart24/run_manager.py`
- Modify: `scraper/tests/test_run_manager.py`

**Interfaces:**
- Produces: `.process_detail_backlog(session, client_factory, brand, run, concurrency=1, session_refresh_requests=30, db_page_size=DETAIL_DB_PAGE_SIZE, fetch_detail_fn=fetch_detail, exclude_ids=frozenset(), year_from: int | None = None) -> int` (the `batch_size` parameter is renamed `db_page_size` — it is now the DB page size of a loop, not a per-run cap — and a `year_from` parameter is added). `run_brand_sweep`'s signature is unchanged from Task 5.

- [ ] **Step 1: Write the failing tests**

In `scraper/tests/test_run_manager.py`:

**1a.** Three existing tests reach the detail phase with new listings and no injected `fetch_detail_fn`. Once the exclusion is removed they would make **real network calls**. Add the fake to each. Apply these three exact single-line substitutions (each occurs once):

- Old: `    run = run_brand_sweep(db_session, _client, BRAND, crawl_fn=fake_crawl)\n\n    assert run.status == "success"\n    assert run.new_listings == 1`
  New: `    run = run_brand_sweep(db_session, _client, BRAND, crawl_fn=fake_crawl, fetch_detail_fn=_noop_fetch_detail)\n\n    assert run.status == "success"\n    assert run.new_listings == 1`

- In `test_run_brand_sweep_commits_scrape_run_before_crawling`, replace `run = run_brand_sweep(db_session, _client, BRAND, crawl_fn=fake_crawl)` with `run = run_brand_sweep(db_session, _client, BRAND, crawl_fn=fake_crawl, fetch_detail_fn=_noop_fetch_detail)`.

- In `test_run_brand_sweep_commits_each_batch_incrementally`, replace `run = run_brand_sweep(db_session, _client, BRAND, crawl_fn=fake_crawl, batch_size=2)` with `run = run_brand_sweep(db_session, _client, BRAND, crawl_fn=fake_crawl, batch_size=2, fetch_detail_fn=_noop_fetch_detail)`.

(The other tests are unaffected: they either already inject `fetch_detail_fn`, or they raise before reaching the detail phase, or their listings already have `detail_scraped=True`.)

**1b.** `test_run_brand_sweep_excludes_same_run_new_listings_from_backlog` encodes the behavior being removed. Delete that entire test function and replace it with:

```python
def test_run_brand_sweep_fetches_detail_for_listings_new_in_this_same_sweep(db_session):
    """Binding requirement: a listing not already in the DB must have its detail
    page fetched during the very sweep that discovers it, not the next one."""
    fetched_urls: list[str] = []

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        yield _fake_snippet("brand-new-1", 9000)

    def fake_fetch_detail(client, url):
        fetched_urls.append(url)
        return DetailResult(sold=False, data=_fake_detail_data("brand-new-1"))

    run_brand_sweep(db_session, _client, BRAND, crawl_fn=fake_crawl, fetch_detail_fn=fake_fetch_detail)

    listing = db_session.get(Listing, "brand-new-1")
    assert listing.detail_scraped is True
    assert listing.url in fetched_urls
```

**1c.** Append these three new tests:

```python
def test_process_detail_backlog_processes_every_pending_listing_across_db_pages(db_session):
    run = ScrapeRun(brand="Fiat", started_at=dt.datetime.utcnow(), status="running")
    db_session.add(run)
    db_session.flush()
    for i in range(7):
        db_session.add(_existing_listing(f"pending-{i}", 1000 + i, detail_scraped=False))
    db_session.commit()

    def fake_fetch_detail(client, url):
        return DetailResult(sold=False, data=_fake_detail_data(url))

    process_detail_backlog(
        db_session, _client, BRAND, run,
        concurrency=3, db_page_size=3, fetch_detail_fn=fake_fetch_detail,
    )

    rows = db_session.query(Listing).filter_by(brand="Fiat").all()
    assert len(rows) == 7
    assert all(row.detail_scraped for row in rows)


def test_process_detail_backlog_terminates_when_a_listing_cannot_be_processed(db_session):
    """A permanently failing detail page must not trap the paging loop in an
    infinite retry that hammers the site."""
    run = ScrapeRun(brand="Fiat", started_at=dt.datetime.utcnow(), status="running")
    db_session.add(run)
    db_session.flush()
    db_session.add(_existing_listing("poison-1", 1000, detail_scraped=False))
    db_session.commit()

    def failing_fetch_detail(client, url):
        raise ValueError("permanently broken detail page")

    with pytest.raises(ValueError):
        process_detail_backlog(
            db_session, _client, BRAND, run, db_page_size=1, fetch_detail_fn=failing_fetch_detail
        )


def test_run_brand_sweep_ignores_active_listings_older_than_the_year_floor(db_session):
    """Listings registered before the floor no longer appear in searches, so they
    must not be mistaken for 'missing' and sold-confirmed on every run."""
    old = _existing_listing("old-1", 3000)
    old.first_registration = dt.date(2005, 6, 1)
    db_session.add(old)
    db_session.commit()

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        return iter([])

    def exploding_fetch_detail(client, url):
        raise AssertionError(f"out-of-floor listing must not be detail-fetched: {url}")

    run = run_brand_sweep(
        db_session, _client, BRAND, crawl_fn=fake_crawl,
        fetch_detail_fn=exploding_fetch_detail, year_from=2021,
    )

    assert run.status == "success"
    assert run.sold_detected == 0
    assert run.errors_count == 0
    assert db_session.get(Listing, "old-1").status == "active"
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd scraper && pytest tests/test_run_manager.py -v`
Expected: FAIL — `process_detail_backlog` does not accept `db_page_size`/`year_from`; the same-sweep-detail test fails (`detail_scraped is False`); the year-floor test fails via the `AssertionError` from `exploding_fetch_detail`.

- [ ] **Step 3: Implement the behavior changes**

Four edits to `scraper/src/autosmart24/run_manager.py`:

**3a.** Rename the constant and add the `or_` import. Change `DETAIL_BATCH_SIZE = 50` to:

```python
DETAIL_DB_PAGE_SIZE = 50
```

and change the SQLAlchemy import line to:

```python
from sqlalchemy import or_, select
```

**3b.** Replace `process_detail_backlog` entirely with the paging version:

```python
def process_detail_backlog(
    session: Session,
    client_factory: Callable[[], RateLimitedClient],
    brand: BrandConfig,
    run: ScrapeRun,
    concurrency: int = 1,
    session_refresh_requests: int = 30,
    db_page_size: int = DETAIL_DB_PAGE_SIZE,
    fetch_detail_fn=fetch_detail,
    exclude_ids: set[str] = frozenset(),
    year_from: int | None = None,
) -> int:
    total_sold = 0
    # Rows the pool did not report on are parked here for the rest of this call,
    # so the LIMIT-ed query can never re-select the same unprocessable row
    # forever. Without this, one permanently-failing detail page becomes an
    # infinite loop hammering the site.
    failed_ids: set[str] = set()

    while True:
        stmt = select(Listing).where(
            Listing.brand == brand.display_name,
            Listing.status == "active",
            Listing.detail_scraped.is_(False),
            Listing.id.notin_(set(exclude_ids) | failed_ids),
        )
        if year_from is not None:
            stmt = stmt.where(
                or_(
                    Listing.first_registration.is_(None),
                    Listing.first_registration >= dt.date(year_from, 1, 1),
                )
            )
        pending = session.execute(
            stmt.order_by(Listing.first_seen_at.asc()).limit(db_page_size)
        ).scalars().all()

        if not pending:
            return total_sold

        rows_by_id = {row.id: row for row in pending}
        jobs = [(row.id, row.url) for row in pending]
        enriched = 0
        sold = 0
        handled: set[str] = set()
        now = _now()

        def _detail_worker(job: tuple[str, str], client: RateLimitedClient) -> list[tuple[str, object]]:
            listing_id, url = job
            return [(listing_id, fetch_detail_fn(client, url))]

        try:
            for listing_id, result in run_worker_pool(
                jobs, _detail_worker, client_factory, concurrency, session_refresh_requests
            ):
                handled.add(listing_id)
                row = rows_by_id[listing_id]
                row.last_checked_at = now
                if result.sold:
                    row.status = "sold"
                    row.sold_at = now
                    sold += 1
                    continue

                detail = result.data
                if detail["price"] is not None and detail["price"] != row.price:
                    row.price = detail["price"]
                    session.add(PriceHistory(listing_id=row.id, price=detail["price"], recorded_at=now))

                row.power_kw = detail["power_kw"]
                row.power_cv = detail["power_cv"]
                row.displacement_ccm = detail["displacement_ccm"]
                row.body_type = detail["body_type"]
                row.body_color = detail["body_color"]
                row.num_seats = detail["num_seats"]
                row.num_doors = detail["num_doors"]
                row.num_previous_owners = detail["num_previous_owners"]
                row.province = detail["province"]
                row.latitude = detail["latitude"]
                row.longitude = detail["longitude"]
                row.vat_exposed = detail["vat_exposed"]
                row.price_evaluation_category = detail["price_evaluation_category"]
                row.price_evaluation_median = detail["price_evaluation_median"]
                row.created_at_source = detail["created_at_source"]
                row.raw_detail = detail["raw_detail"]
                row.detail_scraped = True
                enriched += 1
        except BlockedError as exc:
            run.status = "blocked"
            run.errors_count += 1
            _log_event(session, run, "blocked", str(exc), url=exc.url)
            session.commit()
            return total_sold + sold

        _log_event(
            session, run, "info",
            f"Detail backlog page: enriched {enriched}, confirmed sold {sold} (page size {len(pending)})",
        )
        session.commit()
        total_sold += sold
        failed_ids |= set(rows_by_id) - handled
```

**3c.** In `run_brand_sweep`, scope the active-inventory query to the year floor. Replace the `active_rows = session.execute(...)` statement with:

```python
        active_stmt = select(Listing).where(
            Listing.brand == brand.display_name, Listing.status == "active"
        )
        if year_from is not None:
            # Listings registered before the floor no longer appear in our
            # searches, so they must not be mistaken for "missing" (which would
            # trigger a pointless sold-confirmation fetch on every run, forever).
            active_stmt = active_stmt.where(
                or_(
                    Listing.first_registration.is_(None),
                    Listing.first_registration >= dt.date(year_from, 1, 1),
                )
            )
        active_rows = session.execute(active_stmt).scalars().all()
```

**3d.** Update the `process_detail_backlog(...)` call in `run_brand_sweep`: drop `exclude_ids`, pass `year_from`, and skip it entirely if the sweep is already blocked (opening fresh clients against a site that just returned 403/429 is exactly what the block signal tells us not to do):

```python
        backlog_sold_count = 0
        if run.status != "blocked":
            backlog_sold_count = process_detail_backlog(
                session, client_factory, brand, run,
                concurrency=concurrency, session_refresh_requests=session_refresh_requests,
                fetch_detail_fn=fetch_detail_fn, year_from=year_from,
            )
```

- [ ] **Step 4: Run the run_manager tests**

Run: `cd scraper && pytest tests/test_run_manager.py -v`
Expected: `21 passed` (18 from Task 5, minus the deleted exclusion test, plus its inverted replacement, plus 3 new)

- [ ] **Step 5: Run the full backend suite**

Run: `cd scraper && python -m pytest -q`
Expected: all pass, no hangs (a hang means a test is making a real network call — a missing `fetch_detail_fn` injection).

- [ ] **Step 6: Commit**

```bash
git add scraper/src/autosmart24/run_manager.py scraper/tests/test_run_manager.py
git commit -m "Uncap detail backlog, fetch details for same-sweep new listings, scope sweep to the year floor"
```

---

## Task 7: Wire the new env vars and the client factory (`api/app.py`, `.env.example`)

**Files:**
- Modify: `scraper/src/autosmart24/api/app.py`
- Modify: `.env.example`

**Interfaces:**
- Consumes: `make_client` (Task 2), `BlockRateTracker` (Task 1), `run_brand_sweep` (Tasks 5-6).

- [ ] **Step 1: Read the current file**

Read `scraper/src/autosmart24/api/app.py` in full — confirm it has `run_guard = BrandRunGuard()`, `_run_fn` with the guard acquire/release, `_run_now_fn`, and the two `@app.on_event` handlers.

- [ ] **Step 2: Rewrite app.py**

`scraper/src/autosmart24/api/app.py` (full replacement):

```python
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
```

The backoff callback opens its own short-lived session: it is invoked from scraping worker threads, and the sweep's own `Session` is not thread-safe. Its `except Exception` is deliberate — a failure to log a monitoring event must never take down a running sweep.

- [ ] **Step 3: Update .env.example**

Read `.env.example`, then replace its full content with:

```
DATABASE_URL=postgresql+psycopg://autosmart24:autosmart24@localhost:5434/autosmart24
SCRAPE_INTERVAL_DAYS=4
SCRAPE_MIN_DELAY_SECONDS=3
SCRAPE_MAX_DELAY_SECONDS=8
SCRAPE_CONCURRENCY=6
SCRAPE_MAX_LISTING_AGE_YEARS=5
SCRAPE_SESSION_REFRESH_REQUESTS=30
VITE_API_BASE_URL=http://localhost:8001
```

(This also corrects two pre-existing drifts caught while touching the file: the Postgres port was `5432` but `docker-compose.yml` maps `5434` since an earlier port-conflict fix, and `VITE_API_BASE_URL` — needed by the dashboard's Docker build — was missing.)

- [ ] **Step 4: Verify the module imports cleanly**

Run: `cd scraper && DATABASE_URL=sqlite:///:memory: python -c "import autosmart24.api.app; print('ok')"`
Expected: prints `ok`, no traceback.

- [ ] **Step 5: Run the full backend suite**

Run: `cd scraper && python -m pytest -q`
Expected: all pass (`test_api.py` builds `create_app` directly and never imports `api/app.py`, so it is unaffected).

- [ ] **Step 6: Commit**

```bash
git add scraper/src/autosmart24/api/app.py .env.example
git commit -m "Wire concurrency, year-floor, and session-refresh env vars plus backoff event logging"
```

---

## Task 8: Add the new env vars to docker-compose.yml

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Read the current file**

Read `docker-compose.yml` — confirm the `app:` service's `environment:` block has `DATABASE_URL`, `SCRAPE_INTERVAL_DAYS`, `SCRAPE_MIN_DELAY_SECONDS`, `SCRAPE_MAX_DELAY_SECONDS`.

- [ ] **Step 2: Add the three new env vars**

In the `app:` service's `environment:` block, add these lines directly after `SCRAPE_MAX_DELAY_SECONDS: "8"`, matching the existing quoted-string style and indentation:

```yaml
      SCRAPE_CONCURRENCY: "6"
      SCRAPE_MAX_LISTING_AGE_YEARS: "5"
      SCRAPE_SESSION_REFRESH_REQUESTS: "30"
```

- [ ] **Step 3: Validate the compose file**

Run: `cd "C:\App AI\Autoscout" && docker compose config --quiet`
Expected: no output, exit code 0.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml
git commit -m "Add concurrency, year-floor, and session-refresh env vars to the app service"
```

---

## Task 9: Live verification

Not a TDD task — a manual calibration pass against the real site, mirroring the approach used earlier in this project.

**Files:** None.

- [ ] **Step 1: Full test suite baseline**

Run: `cd scraper && python -m pytest -q` then `cd dashboard && npx vitest run`
Expected: all backend and frontend tests pass.

- [ ] **Step 2: Rebuild and restart the stack**

Run (from `C:\App AI\Autoscout`): `docker compose up -d --build`
Expected: images rebuild; all three containers `Up`.

- [ ] **Step 3: Confirm the app boots**

Run: `docker compose logs app --tail 20`
Expected: Alembic lines (no new migration in this plan — no schema change) then `Uvicorn running on http://0.0.0.0:8000`, no traceback.

- [ ] **Step 4: Trigger a run and measure throughput**

Run: `curl -s -X POST http://localhost:8001/brands/fiat/run-now`
Expected: `{"triggered":true}`

Then sample the count a few times over 2-3 minutes:
`docker exec autoscout-postgres-1 psql -U autosmart24 -d autosmart24 -c "SELECT count(*) FROM listings WHERE brand='Fiat';"`

Record the observed listings/minute. The pre-change single-threaded baseline was ~157/min. Expect a clear improvement, but do **not** expect exactly 6×: real-world network and DB overhead, plus the fact that detail fetches now run for every new listing, change the mix. Record the actual number for future calibration rather than asserting a target.

- [ ] **Step 5: Confirm the year floor is applied**

Run: `docker exec autoscout-postgres-1 psql -U autosmart24 -d autosmart24 -c "SELECT min(first_registration) FROM listings WHERE brand='Fiat' AND first_seen_at > now() - interval '15 minutes';"`
Expected: not earlier than January of `(current year - 5)`. (Filtering by `first_seen_at` isolates this run from older rows already in the table.)

- [ ] **Step 6: Confirm same-sweep detail enrichment is happening**

Run: `docker exec autoscout-postgres-1 psql -U autosmart24 -d autosmart24 -c "SELECT count(*) FILTER (WHERE detail_scraped) AS enriched, count(*) AS total FROM listings WHERE brand='Fiat' AND first_seen_at > now() - interval '15 minutes';"`
Expected: `enriched` is greater than zero and climbing on repeat sampling — the binding requirement in action. (It will lag `total`, since the backlog runs after the search phase completes.)

- [ ] **Step 7: Confirm no stuck or silently-failed run**

Run: `docker exec autoscout-postgres-1 psql -U autosmart24 -d autosmart24 -c "SELECT status, listings_seen, new_listings, sold_detected, errors_count FROM scrape_runs WHERE brand='Fiat' ORDER BY started_at DESC LIMIT 1;"`
Expected: `status` is `running`, or a terminal `success`/`blocked`/`error`. Critically, `errors_count` should **not** be in the thousands — that would indicate the year-floor scoping (Task 6C) is not working and out-of-floor listings are being sold-confirmed.

Also check for backoff events: `docker exec autoscout-postgres-1 psql -U autosmart24 -d autosmart24 -c "SELECT level, message, created_at FROM scrape_events WHERE level='warning' ORDER BY created_at DESC LIMIT 5;"`
Expected: ideally none. If "Adaptive backoff engaged" appears, the site is pushing back at `SCRAPE_CONCURRENCY=6` — record it, and note that the tunable to lower is `SCRAPE_CONCURRENCY` (per the spec, the escalation path is never more aggressive scraping from one IP).

- [ ] **Step 8: Stop the stack**

Run: `docker compose down`
Expected: all containers removed cleanly.

## Self-review notes

- **Spec coverage:** design §3 (year filter) → Task 4 (`year_from` on probe/split/page URLs) + Task 6C (scoping stored inventory to the same floor) + Task 7 (`_year_from()`); §4 (two-phase parallel search) → Tasks 3-4; §5 (parallel, uncapped detail phase) → Tasks 5-6; §6 (UA rotation, session refresh, adaptive backoff, `ScrapeEvent` warning) → Tasks 1, 2, 3, 7; §7 (clean stop on block, exception net) → Task 3 (error propagation + `finally` shutdown), Task 6 (skip backlog when blocked); §8 (tests) → each task's own TDD steps; §9 (config) → Tasks 7-8. Binding requirement (detail fetch in the discovering sweep) → Task 6B.
- **Placeholder scan:** no TBD/TODO; every step has runnable code or an exact, verified find/replace with a `grep -c` pre-check.
- **Type consistency verified:** `run_worker_pool`'s `worker_fn: Callable[[JobT, RateLimitedClient], list[ResultT]]` is matched by `_discovery_worker`/`_page_worker` (Task 4) and `_detail_worker`/`_missing_worker` (Tasks 5-6), all of which return a list; `crawl_brand`'s `client_factory`/`year_from`/`concurrency`/`session_refresh_requests` match the keyword arguments `run_brand_sweep` passes; `make_client`'s signature matches `_client_factory` in Task 7; `BlockRateTracker(on_backoff_change=...)` matches the callback defined in Task 7.
- **Known limitation, documented deliberately:** in the detail phase `session_refresh_requests` rarely triggers, because `process_detail_backlog` invokes `run_worker_pool` once per DB page (default 50 rows across N workers), and each invocation already creates fresh clients. Session rotation therefore happens *more* often than configured, never less — the safe direction — so this is left as-is rather than restructured.
