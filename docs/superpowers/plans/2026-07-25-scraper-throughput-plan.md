# Scraper Throughput: Concurrency, Year Filter, Light Camouflage — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the artificial 50-listing/run cap on detail-page enrichment, add configurable in-process thread concurrency for both the search and detail scraping phases, add a configurable registration-year floor to shrink scan volume, and add light anti-fingerprinting (User-Agent rotation, periodic session reset, adaptive backoff) — all within the existing single Python process, no new hosts or processes.

**Architecture:** A new generic `run_worker_pool` utility (`scraping/concurrency.py`) drains a list of jobs with N thread workers, each owning its own `RateLimitedClient` (created via a `client_factory`, refreshed every K requests), streaming results back to the caller as they complete and raising `BlockedError` (after draining remaining queued jobs) if any worker hits a block. `crawler.py`'s `crawl_brand` becomes two-phase (parallel model discovery/probing, then parallel page fetching) built on this utility; `run_manager.py`'s detail backlog and sold-confirmation loops reuse the same utility instead of sequential single-client loops.

**Tech Stack:** Same as the existing project (Python 3.12, httpx, SQLAlchemy, pytest + respx) — no new dependencies. Concurrency via the standard library `threading`/`queue`/`concurrent.futures` is NOT used for pool management (a hand-rolled `queue.Queue` + `threading.Thread` pool is used instead, for full control over session-refresh-per-worker and clean job-draining on block — see Task 3).

## Global Constraints

- Search-query splitting criterion must NEVER be price — only model (`mmmv` param) and registration year (`fregfrom`/`fregto`) may be used (unchanged from the original plan; this plan only adds a year *floor*, applied the same way as existing year-splitting).
- "Sold" status requires explicit detail-page confirmation — never inferred from absence in a sweep alone (unchanged).
- The dashboard is the sole monitoring/notification channel — no email/Telegram (unchanged).
- MVP brands (fixed): Fiat, Volkswagen, BMW, Audi, Mercedes-Benz (unchanged).
- Single machine, single IP, **no IP rotation**. This plan adds concurrency as multiple threads within the same process on the same single IP — it does NOT add multiple hosts, processes, or proxies. If real block-rate problems appear after this work ships, the spec's own escalation path is "more workers on different IPs (LAN/other machines)," not proxy rotation — out of scope here.
- Explicitly OUT OF SCOPE for this plan: TLS/JA3 impersonation (curl_cffi), Playwright fallback, multi-process/multi-host workers coordinated via DB claim-and-lease. These may be revisited later only if real blocks are observed after this ships.
- All new tunables (`SCRAPE_CONCURRENCY`, `SCRAPE_MAX_LISTING_AGE_YEARS`, `SCRAPE_SESSION_REFRESH_REQUESTS`) must be environment-configurable without code changes, following the existing `SCRAPE_MIN_DELAY_SECONDS`/`SCRAPE_MAX_DELAY_SECONDS`/`SCRAPE_INTERVAL_DAYS` pattern in `scraper/src/autosmart24/api/app.py`.
- Base URL: `https://www.autoscout24.it`.

---

## Task 1: Adaptive block-rate tracker (`rate_control.py`)

**Files:**
- Create: `scraper/src/autosmart24/scraping/rate_control.py`
- Create: `scraper/tests/test_rate_control.py`

**Interfaces:**
- Produces: `autosmart24.scraping.rate_control.BlockRateTracker` (class; constructor `BlockRateTracker(window_size: int = 100, threshold: float = 0.02, backoff_multiplier: float = 2.0)`; methods `record_success() -> None`, `record_blocked() -> None`, `delay_multiplier() -> float`) — consumed by `http_client.py` (Task 2).

- [ ] **Step 1: Write the failing tests**

`scraper/tests/test_rate_control.py`:

```python
from autosmart24.scraping.rate_control import BlockRateTracker


def test_block_rate_tracker_starts_at_normal_rate():
    tracker = BlockRateTracker()
    assert tracker.delay_multiplier() == 1.0


def test_block_rate_tracker_backs_off_when_threshold_exceeded():
    tracker = BlockRateTracker(window_size=10, threshold=0.2, backoff_multiplier=2.0)
    for _ in range(8):
        tracker.record_success()
    for _ in range(2):
        tracker.record_blocked()
    assert tracker.delay_multiplier() == 2.0


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
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd scraper && pytest tests/test_rate_control.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autosmart24.scraping.rate_control'`

- [ ] **Step 3: Implement rate_control.py**

`scraper/src/autosmart24/scraping/rate_control.py`:

```python
from __future__ import annotations

import threading
from collections import deque


class BlockRateTracker:
    def __init__(self, window_size: int = 100, threshold: float = 0.02, backoff_multiplier: float = 2.0):
        self._threshold = threshold
        self._backoff_multiplier = backoff_multiplier
        self._outcomes: deque[bool] = deque(maxlen=window_size)
        self._lock = threading.Lock()

    def record_success(self) -> None:
        with self._lock:
            self._outcomes.append(False)

    def record_blocked(self) -> None:
        with self._lock:
            self._outcomes.append(True)

    def delay_multiplier(self) -> float:
        with self._lock:
            if not self._outcomes:
                return 1.0
            block_rate = sum(self._outcomes) / len(self._outcomes)
        return self._backoff_multiplier if block_rate > self._threshold else 1.0
```

- [ ] **Step 4: Run to confirm pass**

Run: `cd scraper && pytest tests/test_rate_control.py -v`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add scraper/src/autosmart24/scraping/rate_control.py scraper/tests/test_rate_control.py
git commit -m "Add thread-safe adaptive block-rate tracker for backoff"
```

---

## Task 2: User-Agent rotation, client factory, and rate-controller wiring (`http_client.py`)

**Files:**
- Modify: `scraper/src/autosmart24/scraping/http_client.py`
- Modify: `scraper/tests/test_http_client.py`

**Interfaces:**
- Consumes: `BlockRateTracker` (Task 1).
- Produces: `autosmart24.scraping.http_client.USER_AGENTS: list[str]`, `.make_client(min_delay_seconds: float, max_delay_seconds: float, rate_controller: BlockRateTracker | None = None, sleep_fn: Callable[[float], None] = time.sleep) -> RateLimitedClient`, and an updated `RateLimitedClient` with new fields `user_agent: str` and `rate_controller: BlockRateTracker | None` — consumed by `concurrency.py` (Task 3), `crawler.py` (Task 4), `run_manager.py` (Task 5), `api/app.py` (Task 6).

- [ ] **Step 1: Read the current file**

Read `scraper/src/autosmart24/scraping/http_client.py` in full before editing — confirm it still matches the structure below (it was last touched in the error-resilience fix; the `get()` method and `BlockedError`/`BLOCK_STATUS_CODES` should be unchanged since then).

- [ ] **Step 2: Write the failing tests**

Add to `scraper/tests/test_http_client.py` (keep all 4 existing tests in the file unchanged — do not remove `_instant_client`, it's still used):

```python
from autosmart24.scraping.http_client import USER_AGENTS, make_client
from autosmart24.scraping.rate_control import BlockRateTracker


def test_make_client_picks_a_user_agent_from_the_pool():
    client = make_client(min_delay_seconds=0, max_delay_seconds=0, sleep_fn=lambda _: None)
    assert client.user_agent in USER_AGENTS
    client.close()


def test_make_client_rotates_user_agents_across_many_calls():
    clients = [make_client(0, 0, sleep_fn=lambda _: None) for _ in range(50)]
    try:
        seen = {c.user_agent for c in clients}
        assert len(seen) > 1
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
Expected: FAIL — `ImportError: cannot import name 'USER_AGENTS'` (and/or `TypeError: unexpected keyword argument 'rate_controller'`)

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
    user_agent: str = field(default_factory=lambda: USER_AGENTS[0])
    rate_controller: BlockRateTracker | None = None
    sleep_fn: Callable[[float], None] = field(default=time.sleep)
    client: httpx.Client = field(init=False)

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

Note: `client` becomes `field(init=False)`, built in `__post_init__` so it can depend on `self.user_agent` (a `dataclasses.field(default_factory=...)` cannot reference sibling fields). Existing callers that construct `RateLimitedClient(min_delay_seconds=..., max_delay_seconds=..., sleep_fn=...)` without passing `client=` explicitly are unaffected — confirm this by grep: `grep -rn "RateLimitedClient(" scraper/tests scraper/src` and check none pass a `client=` kwarg.

Also add the needed test imports at the top of `scraper/tests/test_http_client.py` if not already present: `import pytest` (already there per the existing 4 tests using `pytest.raises`).

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

This is the core reusable primitive: given a list of jobs and a per-job worker function, runs them across N threads (each with its own `RateLimitedClient` from a factory, refreshed every K jobs), streaming results back as they complete, and cleanly stopping (draining remaining queued jobs, re-raising) on `BlockedError`.

**Files:**
- Create: `scraper/src/autosmart24/scraping/concurrency.py`
- Create: `scraper/tests/test_concurrency.py`

**Interfaces:**
- Consumes: `RateLimitedClient`, `BlockedError` (`http_client.py`).
- Produces: `autosmart24.scraping.concurrency.run_worker_pool(jobs: list[JobT], worker_fn: Callable[[JobT, RateLimitedClient], list[ResultT]], client_factory: Callable[[], RateLimitedClient], concurrency: int, session_refresh_requests: int) -> Iterator[ResultT]` — consumed by `crawler.py` (Task 4), `run_manager.py` (Task 5).

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
    jobs = list(range(10))

    def worker_fn(job, client):
        return [job * 2]

    results = sorted(run_worker_pool(jobs, worker_fn, _client_factory, concurrency=3, session_refresh_requests=100))
    assert results == [i * 2 for i in range(10)]


def test_run_worker_pool_worker_fn_can_return_multiple_results_per_job():
    jobs = [1, 2, 3]

    def worker_fn(job, client):
        return [job, job * 10]

    results = sorted(run_worker_pool(jobs, worker_fn, _client_factory, concurrency=2, session_refresh_requests=100))
    assert results == sorted([1, 10, 2, 20, 3, 30])


def test_run_worker_pool_stops_and_raises_on_blocked_error():
    jobs = list(range(20))
    call_count = {"n": 0}
    lock = threading.Lock()

    def worker_fn(job, client):
        with lock:
            call_count["n"] += 1
        if job == 5:
            raise BlockedError(403, "https://example.test/blocked")
        return [job]

    with pytest.raises(BlockedError):
        list(run_worker_pool(jobs, worker_fn, _client_factory, concurrency=1, session_refresh_requests=100))

    assert call_count["n"] == 6


def test_run_worker_pool_creates_fresh_client_after_session_refresh_threshold():
    created_clients = []

    def factory():
        c = RateLimitedClient(min_delay_seconds=0, max_delay_seconds=0, sleep_fn=lambda _: None)
        created_clients.append(c)
        return c

    jobs = list(range(5))

    def worker_fn(job, client):
        return [job]

    list(run_worker_pool(jobs, worker_fn, factory, concurrency=1, session_refresh_requests=2))

    assert len(created_clients) == 3


def test_run_worker_pool_uses_multiple_threads_concurrently():
    start_times = []
    lock = threading.Lock()

    def worker_fn(job, client):
        with lock:
            start_times.append(time.monotonic())
        time.sleep(0.2)
        return [job]

    jobs = list(range(4))
    list(run_worker_pool(jobs, worker_fn, _client_factory, concurrency=4, session_refresh_requests=100))

    assert max(start_times) - min(start_times) < 0.15
```

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
    job_queue: "queue.Queue[JobT]" = queue.Queue()
    for job in jobs:
        job_queue.put(job)

    results: "queue.Queue[object]" = queue.Queue()
    done_marker = object()
    blocked_holder: list[BlockedError] = []
    blocked_lock = threading.Lock()

    def _drain_queue() -> None:
        while True:
            try:
                job_queue.get_nowait()
            except queue.Empty:
                return

    def worker() -> None:
        client = client_factory()
        processed = 0
        try:
            while True:
                try:
                    job = job_queue.get_nowait()
                except queue.Empty:
                    return
                if processed >= session_refresh_requests:
                    client.close()
                    client = client_factory()
                    processed = 0
                try:
                    job_results = worker_fn(job, client)
                except BlockedError as exc:
                    with blocked_lock:
                        blocked_holder.append(exc)
                    _drain_queue()
                    return
                processed += 1
                for item in job_results:
                    results.put(item)
        finally:
            client.close()

    threads = [threading.Thread(target=worker) for _ in range(concurrency)]
    for t in threads:
        t.start()

    def _wait_then_signal_done() -> None:
        for t in threads:
            t.join()
        results.put(done_marker)

    watcher = threading.Thread(target=_wait_then_signal_done)
    watcher.start()

    while True:
        item = results.get()
        if item is done_marker:
            break
        yield item

    watcher.join()

    if blocked_holder:
        raise blocked_holder[0]
```

- [ ] **Step 4: Run to confirm pass**

Run: `cd scraper && pytest tests/test_concurrency.py -v`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add scraper/src/autosmart24/scraping/concurrency.py scraper/tests/test_concurrency.py
git commit -m "Add generic thread-pool job runner with per-worker client refresh and block handling"
```

---

## Task 4: Two-phase parallel crawl with year-floor filter (`crawler.py`)

**Files:**
- Modify: `scraper/src/autosmart24/scraping/crawler.py`
- Modify: `scraper/tests/test_crawler.py`

**Interfaces:**
- Consumes: `run_worker_pool` (Task 3), `RateLimitedClient`, `BlockedError` (`http_client.py`).
- Produces: `autosmart24.scraping.crawler.crawl_brand(client_factory: Callable[[], RateLimitedClient], brand_slug: str, make_id: int, year_from: int | None = None, concurrency: int = 1, session_refresh_requests: int = 30) -> Iterator[dict]` (signature change: first param is now a factory, not a client; three new optional params) — consumed by `run_manager.py` (Task 5).
- Also produces: `autosmart24.scraping.crawler.QueryUnit` (dataclass: `model_id: int`, `year_from: int | None`, `year_to: int | None`, `number_of_pages: int`) — internal, but importable for tests.

- [ ] **Step 1: Read the current file**

Read `scraper/src/autosmart24/scraping/crawler.py` in full — confirm `MIN_YEAR = 1950`, `MAX_YEAR = 2027`, `ModelInfo`, `discover_models`, `_count_for_year_range`, `_iter_listings_from_page` are present and unchanged since Task 10 of the original plan.

- [ ] **Step 2: Write the failing tests**

Update `scraper/tests/test_crawler.py`. Add these imports at the top (alongside the existing `json`, `httpx`, `respx` imports):

```python
import pytest

from autosmart24.scraping.http_client import BlockedError, RateLimitedClient
```

(Note: `RateLimitedClient` may already be imported — check before duplicating.)

In the two EXISTING test functions, replace the client construction and `crawl_brand` call. In `test_crawl_brand_yields_all_listings_across_pages`, replace:

```python
    client = RateLimitedClient(min_delay_seconds=0, max_delay_seconds=0, sleep_fn=lambda _: None)
    results = list(crawl_brand(client, "fiat", 28))
```

with:

```python
    client_factory = lambda: RateLimitedClient(min_delay_seconds=0, max_delay_seconds=0, sleep_fn=lambda _: None)
    results = list(crawl_brand(client_factory, "fiat", 28, concurrency=1))
```

Apply the exact same replacement (same old text, same new text) in `test_crawl_brand_splits_by_year_when_model_exceeds_threshold`. Both tests' mock setups and assertions stay unchanged — only these two lines change, in both functions.

Then add two new test functions at the end of the file:

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

    client_factory = lambda: RateLimitedClient(min_delay_seconds=0, max_delay_seconds=0, sleep_fn=lambda _: None)
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

    client_factory = lambda: RateLimitedClient(min_delay_seconds=0, max_delay_seconds=0, sleep_fn=lambda _: None)

    with pytest.raises(BlockedError):
        list(crawl_brand(client_factory, "fiat", 28, concurrency=2))
```

- [ ] **Step 3: Run to confirm failure**

Run: `cd scraper && pytest tests/test_crawler.py -v`
Expected: FAIL — existing tests fail with `TypeError` (crawl_brand still takes a client, not a factory, and doesn't accept `year_from`/`concurrency` yet); new tests fail similarly.

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
    probe_url = build_search_url(brand_slug, page=1, make_id=make_id, model_id=model.model_id, year_from=year_from)
    probe_page_props = fetch_page_data(client, probe_url)
    total_results = probe_page_props["numberOfResults"]

    if total_results <= MAX_RESULTS_PER_QUERY:
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

    units: list[QueryUnit] = []
    for unit, listings in run_worker_pool(
        models, _discovery_worker, client_factory, concurrency, session_refresh_requests
    ):
        units.append(unit)
        yield from listings

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
Expected: `4 passed` (2 pre-existing, updated + 2 new)

- [ ] **Step 6: Commit**

```bash
git add scraper/src/autosmart24/scraping/crawler.py scraper/tests/test_crawler.py
git commit -m "Rewrite crawl_brand as two-phase parallel discovery+fetch with year-floor filter"
```

---

## Task 5: Uncapped, parallel detail backlog and sold-confirmation; thread `client_factory`/`concurrency`/`year_from` through `run_manager.py`

This is the largest task — `run_brand_sweep`'s signature changes (client → client_factory, plus new params), which ripples through every existing test in `test_run_manager.py` that calls it. Handle the file as one cohesive change; splitting it further would leave the module in a broken, non-importable state mid-task.

**Files:**
- Modify: `scraper/src/autosmart24/run_manager.py`
- Modify: `scraper/tests/test_run_manager.py`

**Interfaces:**
- Consumes: `run_worker_pool` (Task 3), `crawl_brand` (Task 4, new signature).
- Produces: `autosmart24.run_manager.run_brand_sweep(session, client_factory: Callable[[], RateLimitedClient], brand, crawl_fn=crawl_brand, fetch_detail_fn=fetch_detail, batch_size=NEW_LISTING_COMMIT_BATCH_SIZE, concurrency: int = 1, year_from: int | None = None, session_refresh_requests: int = 30) -> ScrapeRun` (signature change: 2nd param is now a factory), `.process_detail_backlog(session, client_factory, brand, run, concurrency: int = 1, session_refresh_requests: int = 30, db_page_size: int = DETAIL_DB_PAGE_SIZE, fetch_detail_fn=fetch_detail, exclude_ids=frozenset()) -> int` (signature change: 2nd param is now a factory; no longer caps total processed, loops until the pending set is empty) — consumed by `api/app.py` (Task 6).

- [ ] **Step 1: Read the current file**

Read `scraper/src/autosmart24/run_manager.py` in full. Confirm it matches the state after yesterday's two fast-follow fixes: `NEW_LISTING_COMMIT_BATCH_SIZE = 100`, `DETAIL_BATCH_SIZE = 50`, `_iter_batches` helper, batched `session.commit()` per crawl batch, the outer `except BlockedError` / `except Exception` structure in `run_brand_sweep`, and the `errors_count`/`status="blocked"`/`status="error"` handling from yesterday.

- [ ] **Step 2: Write the failing tests**

In `scraper/tests/test_run_manager.py`:

**2a.** Using Edit with `replace_all=true`, apply this exact substitution (15 occurrences — verify count first with `grep -c "def fake_crawl(client, brand_slug, make_id):" scraper/tests/test_run_manager.py`, expect `15`):

- Old: `    def fake_crawl(client, brand_slug, make_id):`
- New: `    def fake_crawl(client, brand_slug, make_id, **kwargs):`

(The parameter is still named `client` for minimal diff noise — after this change it will actually receive `client_factory`, a callable, not an instance; these fakes never call it, so the name doesn't matter functionally. The `**kwargs` absorbs the new `year_from=`/`concurrency=`/`session_refresh_requests=` keyword arguments `run_brand_sweep` will now always pass to `crawl_fn`.)

**2b.** Using Edit with `replace_all=true`, apply this exact substitution (16 occurrences — verify with `grep -c "_client()" scraper/tests/test_run_manager.py`, expect `16`):

- Old: `_client()`
- New: `_client`

(`_client` — the bare function, uncalled — already has the exact shape `Callable[[], RateLimitedClient]` that the new `client_factory` parameter expects, since `def _client() -> RateLimitedClient: return RateLimitedClient(...)` takes no arguments and returns a fresh client each call. No new helper needed.)

**2c.** Add these new test functions at the end of the file:

```python
def test_process_detail_backlog_processes_more_than_one_db_page():
    from autosmart24.db.session import init_db, make_engine, make_session_factory

    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    session = make_session_factory(engine)()

    run = ScrapeRun(brand="Fiat", started_at=dt.datetime.utcnow(), status="running")
    session.add(run)
    session.flush()

    for i in range(7):
        session.add(_existing_listing(f"pending-{i}", 1000 + i, detail_scraped=False))
    session.commit()

    def fake_fetch_detail(client, url):
        return DetailResult(sold=False, data={
            "price": None, "power_kw": None, "power_cv": None, "displacement_ccm": None,
            "body_type": None, "body_color": None, "num_seats": None, "num_doors": None,
            "num_previous_owners": None, "province": None, "latitude": None, "longitude": None,
            "vat_exposed": None, "price_evaluation_category": None, "price_evaluation_median": None,
            "created_at_source": None, "raw_detail": {"id": url},
        })

    process_detail_backlog(session, _client, BRAND, run, db_page_size=3, fetch_detail_fn=fake_fetch_detail)

    rows = session.query(Listing).filter_by(brand="Fiat").all()
    assert len(rows) == 7
    assert all(row.detail_scraped for row in rows)


def test_run_brand_sweep_threads_year_from_and_concurrency_to_crawl_fn():
    received = {}

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        received.update(kwargs)
        return iter([])

    run_brand_sweep(db_session_for_thread_test(), _client, BRAND, crawl_fn=fake_crawl, year_from=2021, concurrency=4)

    assert received["year_from"] == 2021
    assert received["concurrency"] == 4
```

The second new test needs its own fresh session (it's not using the `db_session` pytest fixture, to keep the example self-contained for this plan step) — replace `db_session_for_thread_test()` with a real fixture-backed session by using the `db_session` fixture as a normal test parameter instead. Rewrite it as:

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

(Delete the standalone in-memory-engine version above — use this `db_session`-fixture version only, consistent with every other test in the file.)

- [ ] **Step 3: Run to confirm failure**

Run: `cd scraper && pytest tests/test_run_manager.py -v`
Expected: FAIL — most tests fail with `TypeError` (positional client argument type mismatch once `run_brand_sweep`/`process_detail_backlog` are still on the old signature but tests already pass `_client`/`**kwargs`-aware fakes... actually at this point in TDD the tests should fail because the OLD `run_manager.py` code doesn't accept `db_page_size=`/`year_from=`/`concurrency=` kwargs, and calling `_client` (uncalled) where the old code expects an already-constructed client will raise `AttributeError` when the old code tries to use it as a client directly). Confirm the failures are all attributable to `run_manager.py` not yet updated, not typos in the test edits.

- [ ] **Step 4: Rewrite run_manager.py**

`scraper/src/autosmart24/run_manager.py` (full replacement):

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

DETAIL_DB_PAGE_SIZE = 50
NEW_LISTING_COMMIT_BATCH_SIZE = 100


def _now() -> dt.datetime:
    return dt.datetime.utcnow()


def _log_event(session: Session, run: ScrapeRun, level: str, message: str, url: str | None = None) -> None:
    session.add(
        ScrapeEvent(run_id=run.id, brand=run.brand, level=level, message=message, url=url, created_at=_now())
    )


def _iter_batches(iterable, n: int):
    it = iter(iterable)
    while True:
        batch = list(itertools.islice(it, n))
        if not batch:
            return
        yield batch


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
) -> int:
    total_sold = 0

    while True:
        pending = session.execute(
            select(Listing)
            .where(
                Listing.brand == brand.display_name,
                Listing.status == "active",
                Listing.detail_scraped.is_(False),
                Listing.id.notin_(exclude_ids),
            )
            .order_by(Listing.first_seen_at.asc())
            .limit(db_page_size)
        ).scalars().all()

        if not pending:
            return total_sold

        rows_by_id = {row.id: row for row in pending}
        jobs = [(row.id, row.url) for row in pending]
        now = _now()

        def _detail_worker(job: tuple[str, str], client: RateLimitedClient) -> list[tuple[str, object]]:
            listing_id, url = job
            return [(listing_id, fetch_detail_fn(client, url))]

        enriched = 0
        sold = 0
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
            session.commit()
            return total_sold + sold

        _log_event(
            session, run, "info",
            f"Detail backlog batch: enriched {enriched}, confirmed sold {sold} (batch size {len(pending)})",
        )
        session.commit()
        total_sold += sold


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
    run = ScrapeRun(brand=brand.display_name, started_at=_now(), status="running")
    session.add(run)
    session.commit()

    seen_ids: set[str] = set()
    new_ids: set[str] = set()
    listings_seen = 0
    price_changes = 0

    try:
        active_rows = session.execute(
            select(Listing).where(Listing.brand == brand.display_name, Listing.status == "active")
        ).scalars().all()
        active_db_prices = {row.id: row.price for row in active_rows}
        active_rows_by_id = {row.id: row for row in active_rows}

        for batch in _iter_batches(
            crawl_fn(
                client_factory, brand.slug, brand.make_id,
                year_from=year_from, concurrency=concurrency, session_refresh_requests=session_refresh_requests,
            ),
            batch_size,
        ):
            batch_snippets = {s["id"]: s for s in batch}
            batch_prices = {sid: s["price"] for sid, s in batch_snippets.items()}
            diff = diff_sweep(batch_prices, active_db_prices)
            now = _now()

            for listing_id in diff.new_ids:
                if listing_id in active_rows_by_id or listing_id in new_ids:
                    continue
                snippet = batch_snippets[listing_id]
                session.add(
                    Listing(
                        id=listing_id,
                        cross_reference_id=snippet["cross_reference_id"],
                        brand=snippet["brand"] or brand.display_name,
                        model=snippet["model"],
                        model_group=snippet["model_group"],
                        variant=snippet["variant"],
                        motor_type_name=snippet["motor_type_name"],
                        version_input=snippet["version_input"],
                        transmission=snippet["transmission"],
                        fuel=snippet["fuel"],
                        first_registration=snippet["first_registration"],
                        mileage_km=snippet["mileage_km"],
                        seller_type=snippet["seller_type"],
                        seller_company_name=snippet["seller_company_name"],
                        city=snippet["city"],
                        zip_code=snippet["zip_code"],
                        price=snippet["price"],
                        url=snippet["url"],
                        first_seen_at=now,
                        last_seen_at=now,
                        last_checked_at=now,
                        status="active",
                        detail_scraped=False,
                        raw_snippet=snippet["raw_snippet"],
                    )
                )
                session.add(PriceHistory(listing_id=listing_id, price=snippet["price"], recorded_at=now))

            for listing_id, new_price in diff.price_changed.items():
                row = active_rows_by_id.get(listing_id)
                if row is None:
                    continue
                row.price = new_price
                row.last_seen_at = now
                row.last_checked_at = now
                session.add(PriceHistory(listing_id=listing_id, price=new_price, recorded_at=now))

            for listing_id in diff.unchanged_ids:
                row = active_rows_by_id.get(listing_id)
                if row is None:
                    continue
                row.last_seen_at = now
                row.last_checked_at = now

            session.commit()

            seen_ids.update(batch_snippets.keys())
            new_ids.update(diff.new_ids)
            listings_seen += len(batch_snippets)
            price_changes += len(diff.price_changed)

        missing_ids = set(active_db_prices.keys()) - seen_ids
        now = _now()
        sold_count = 0

        def _missing_worker(listing_id: str, client: RateLimitedClient) -> list[tuple[str, object]]:
            row = active_rows_by_id[listing_id]
            return [(listing_id, fetch_detail_fn(client, row.url))]

        try:
            for listing_id, result in run_worker_pool(
                list(missing_ids), _missing_worker, client_factory, concurrency, session_refresh_requests
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

        backlog_sold_count = process_detail_backlog(
            session, client_factory, brand, run,
            concurrency=concurrency, session_refresh_requests=session_refresh_requests,
            fetch_detail_fn=fetch_detail_fn, exclude_ids=new_ids,
        )

        run.listings_seen = listings_seen
        run.new_listings = len(new_ids)
        run.price_changes = price_changes
        run.sold_detected = sold_count + backlog_sold_count
        if run.status != "blocked":
            run.status = "success"
        run.finished_at = _now()

        session.commit()
        return run
    except BlockedError as exc:
        run.status = "blocked"
        run.listings_seen = listings_seen
        run.new_listings = len(new_ids)
        run.price_changes = price_changes
        run.finished_at = _now()
        _log_event(session, run, "blocked", str(exc), url=exc.url)
        session.commit()
        return run
    except Exception as exc:
        session.rollback()
        run.status = "error"
        run.finished_at = _now()
        run.listings_seen = listings_seen
        run.new_listings = len(new_ids)
        run.price_changes = price_changes
        run.errors_count += 1
        message = f"Unexpected error during sweep: {exc}"
        if len(message) > 2048:
            message = message[:2048]
        _log_event(session, run, "error", message)
        session.commit()
        raise
```

- [ ] **Step 5: Run to confirm pass**

Run: `cd scraper && pytest tests/test_run_manager.py -v`
Expected: `19 passed` (17 pre-existing, migrated + 2 new)

- [ ] **Step 6: Run the full backend suite**

Run: `cd scraper && python -m pytest -q`
Expected: all tests pass (no regressions in `test_api.py`, `test_scheduler.py`, `test_crawler.py`, etc. — `test_api.py` constructs `RateLimitedClient`/`create_app` independently of `run_manager.py`'s changed signature and should be unaffected).

- [ ] **Step 7: Commit**

```bash
git add scraper/src/autosmart24/run_manager.py scraper/tests/test_run_manager.py
git commit -m "Uncap detail backlog, parallelize detail/sold-confirmation fetches, thread client_factory/concurrency/year_from through run_brand_sweep"
```

---

## Task 6: Wire new env vars and build the client factory (`api/app.py`, `.env.example`)

**Files:**
- Modify: `scraper/src/autosmart24/api/app.py`
- Modify: `.env.example`

**Interfaces:**
- Consumes: `make_client` (Task 2), `BlockRateTracker` (Task 1), `run_brand_sweep` (Task 5, new signature).
- Produces: nothing new consumed by later tasks — this is the final wiring point.

- [ ] **Step 1: Read the current file**

Read `scraper/src/autosmart24/api/app.py` in full — confirm it matches the version shown in this plan's design discussion (with `run_guard = BrandRunGuard()`, `_run_fn`, `_run_now_fn` from yesterday's fixes).

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
from autosmart24.db.session import make_engine, make_session_factory
from autosmart24.run_manager import run_brand_sweep
from autosmart24.scheduler import BrandRunGuard, BrandScheduler
from autosmart24.scraping.http_client import make_client
from autosmart24.scraping.rate_control import BlockRateTracker

logger = logging.getLogger(__name__)

INTERVAL_DAYS = float(os.environ.get("SCRAPE_INTERVAL_DAYS", "4"))
MIN_DELAY_SECONDS = float(os.environ.get("SCRAPE_MIN_DELAY_SECONDS", "3"))
MAX_DELAY_SECONDS = float(os.environ.get("SCRAPE_MAX_DELAY_SECONDS", "8"))
CONCURRENCY = int(os.environ.get("SCRAPE_CONCURRENCY", "6"))
MAX_LISTING_AGE_YEARS = int(os.environ.get("SCRAPE_MAX_LISTING_AGE_YEARS", "5"))
SESSION_REFRESH_REQUESTS = int(os.environ.get("SCRAPE_SESSION_REFRESH_REQUESTS", "30"))

engine = make_engine()
session_factory = make_session_factory(engine)
rate_controller = BlockRateTracker()


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
                concurrency=CONCURRENCY, year_from=_year_from(), session_refresh_requests=SESSION_REFRESH_REQUESTS,
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

- [ ] **Step 3: Update .env.example**

Read the current `.env.example` first. Replace its full content with:

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

(This also corrects two pre-existing drifts unrelated to this feature but caught while touching this file: the Postgres port was `5432` in the example but is `5434` in the real `docker-compose.yml` since a prior port-conflict fix, and `VITE_API_BASE_URL` — needed by the dashboard's Docker build — was missing entirely.)

- [ ] **Step 4: Verify the app module still imports cleanly**

Run: `cd scraper && DATABASE_URL=sqlite:///:memory: python -c "import autosmart24.api.app"`
Expected: no traceback (module-level code runs: engine/session_factory/rate_controller/scheduler/run_guard construction, `app = create_app(...)`).

- [ ] **Step 5: Run the full backend suite once more**

Run: `cd scraper && python -m pytest -q`
Expected: all tests pass, no regressions from the app.py rewrite (test_api.py builds its own `create_app` instance directly and doesn't import `app.py`, per the existing pattern confirmed in Task 19's fix work).

- [ ] **Step 6: Commit**

```bash
git add scraper/src/autosmart24/api/app.py .env.example
git commit -m "Wire SCRAPE_CONCURRENCY/SCRAPE_MAX_LISTING_AGE_YEARS/SCRAPE_SESSION_REFRESH_REQUESTS env vars into the scheduler"
```

---

## Task 7: Add new env vars to docker-compose.yml

**Files:**
- Modify: `docker-compose.yml`

**Interfaces:** None (deployment config only).

- [ ] **Step 1: Read the current file**

Read `docker-compose.yml` in full — confirm the `app:` service's `environment:` block currently has `DATABASE_URL`, `SCRAPE_INTERVAL_DAYS`, `SCRAPE_MIN_DELAY_SECONDS`, `SCRAPE_MAX_DELAY_SECONDS`, and `ports: - "8001:8000"`.

- [ ] **Step 2: Add the three new env vars**

In `docker-compose.yml`, in the `app:` service's `environment:` block, add these three lines after `SCRAPE_MAX_DELAY_SECONDS: "8"` (keep the existing lines unchanged, just add these below them, matching the existing quoted-string style):

```yaml
      SCRAPE_CONCURRENCY: "6"
      SCRAPE_MAX_LISTING_AGE_YEARS: "5"
      SCRAPE_SESSION_REFRESH_REQUESTS: "30"
```

- [ ] **Step 3: Validate YAML syntax**

Run: `cd "C:\App AI\Autoscout" && docker compose config --quiet`
Expected: no output, exit code 0 (confirms valid YAML and valid compose schema).

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml
git commit -m "Add SCRAPE_CONCURRENCY/SCRAPE_MAX_LISTING_AGE_YEARS/SCRAPE_SESSION_REFRESH_REQUESTS to docker-compose app service"
```

---

## Task 8: Live verification

Not a TDD task — a manual verification pass against the real site, mirroring the calibration approach already used earlier in this project. Confirms the rebuilt stack is faster, respects the year filter, and doesn't crash.

**Files:** None created/modified.

- [ ] **Step 1: Full test suite baseline**

Run: `cd scraper && python -m pytest -q` and `cd dashboard && npx vitest run`
Expected: all backend and frontend tests passing (no regressions from Tasks 1-7).

- [ ] **Step 2: Rebuild and restart the stack**

Run (from `C:\App AI\Autoscout`): `docker compose up -d --build`
Expected: `app` and `dashboard` images rebuild successfully (dashboard is unaffected by this plan but gets recreated since `docker compose up` recreates dependent services); all three containers `Up`.

- [ ] **Step 3: Confirm the app boots with the new config**

Run: `docker compose logs app --tail 20`
Expected: Alembic migration log lines (no new migration needed — this plan adds no schema changes) followed by `Uvicorn running on http://0.0.0.0:8000` with no traceback.

- [ ] **Step 4: Trigger a fresh run and observe throughput**

Run: `curl -s -X POST http://localhost:8001/brands/fiat/run-now`
Expected: `{"triggered":true}`

Then poll listing count every ~30s for 2-3 minutes:
`docker exec autoscout-postgres-1 psql -U autosmart24 -d autosmart24 -c "SELECT count(*) FROM listings WHERE brand='Fiat';"`

Expected: growth rate noticeably higher than the pre-this-plan baseline of ~157/min single-threaded (exact multiple depends on real-world network/DB overhead, not just the linear `concurrency×` estimate from the design doc — record the actual observed rate for future calibration reference, don't assume it must hit exactly 6x).

- [ ] **Step 5: Confirm the year filter is applied**

Run: `docker exec autoscout-postgres-1 psql -U autosmart24 -d autosmart24 -c "SELECT min(first_registration) FROM listings WHERE brand='Fiat' AND first_seen_at > now() - interval '10 minutes';"`

Expected: the minimum `first_registration` among listings first seen in THIS run should not be earlier than `(current year - 5)` — confirms `SCRAPE_MAX_LISTING_AGE_YEARS=5` is genuinely restricting the query, not just documented. (Listings from the earlier, pre-this-plan test run will still be in the table with older dates — filter by `first_seen_at` to isolate this run's own results.)

- [ ] **Step 6: Confirm no crash / no zombie run**

Run: `docker exec autoscout-postgres-1 psql -U autosmart24 -d autosmart24 -c "SELECT status, errors_count FROM scrape_runs WHERE brand='Fiat' ORDER BY started_at DESC LIMIT 1;"`

Expected: `status` is `running` (still in progress) or `success`/`blocked`/`error` if it finished or hit a real issue — not silently stuck with no progress for an extended period (cross-check against Step 4's growth observation).

- [ ] **Step 7: Stop the stack cleanly**

Run: `docker compose down`
Expected: all three containers removed cleanly.

## Self-review notes

- **Spec coverage:** §3 (year filter) → Task 4 (`year_from` floor in `crawl_brand`) + Task 6 (`_year_from()` computed from `SCRAPE_MAX_LISTING_AGE_YEARS`); §4 (list-phase concurrency, two-phase approach) → Task 3 (`run_worker_pool`) + Task 4 (`crawl_brand` rewrite); §5 (detail-phase concurrency, uncapped) → Task 5 (`process_detail_backlog` loop-until-empty + parallel fetch); §6 (camouflage: UA rotation, session refresh, adaptive backoff) → Task 1 (`BlockRateTracker`) + Task 2 (`USER_AGENTS`, `make_client`) + Task 3/5 (`session_refresh_requests` wired through worker pools); §7 (error handling: clean stop on block) → Task 3's `run_worker_pool` block-and-drain design, reused by Tasks 4 and 5; §9 (config) → Task 6 + Task 7 (all three new env vars, both Python defaults and docker-compose explicit values).
- **Placeholder scan:** no TBD/TODO; every step has runnable code or an exact, literal find/replace instruction (Task 5's mechanical test migrations are exact strings, not vague descriptions).
- **Type consistency verified:** `run_worker_pool`'s `worker_fn: Callable[[JobT, RateLimitedClient], list[ResultT]]` signature is used identically by `crawl_brand`'s `_discovery_worker`/`_page_worker` (Task 4) and `run_manager.py`'s `_detail_worker`/`_missing_worker` (Task 5); `crawl_brand`'s new `client_factory`/`year_from`/`concurrency`/`session_refresh_requests` parameters match exactly what `run_brand_sweep` passes as keyword arguments in Task 5; `make_client`'s signature (Task 2) matches how it's called in `api/app.py`'s `_client_factory` (Task 6).
