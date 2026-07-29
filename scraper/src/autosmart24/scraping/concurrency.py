"""Thread pool for rate-limited HTTP jobs.

Invariants that are load-bearing — change these only deliberately:

* The results queue is UNBOUNDED. This is what makes abandonment
  deadlock-free: a worker can never block in ``results.put`` while the
  consumer has stopped reading. Adding a ``maxsize`` would reintroduce a
  deadlock on the generator-abandonment path.
* The watcher joins every worker BEFORE posting the done marker, and a
  worker always finishes its ``results.put`` calls before terminating, so
  the marker is provably behind every result. No result can be lost.
* Results arrive in completion order, not submission order. Callers must
  not depend on ordering.
* A job whose ``worker_fn`` raises anything other than ``BlockedError`` is
  isolated: the failure is recorded in the caller's ``failures`` list and the
  remaining jobs still run. ``BlockedError``, a failure of
  ``client_factory``, and non-``Exception`` throwables remain fatal and still
  drain the queue. Callers that pass no ``failures`` list still get the
  isolation — they simply cannot tell which job was lost.
* Captured worker errors are re-raised only when the generator runs to
  completion. If the consumer abandons the generator (``close()``, or an
  exception in the consumer's own loop body), ``GeneratorExit`` propagates
  and any captured error is discarded. A consumer that ``break``s out of
  the loop will therefore not see a ``BlockedError`` — no current caller
  does this, but a future one must not rely on the signal.
* ``close()`` blocks: the shutdown path joins the workers, each of which
  finishes its in-flight request first (rate-limit sleep plus HTTP
  timeout, repeated once per retry attempt — up to ``retries + 1``
  times, tripling the worst case versus a single attempt). The worker's
  ``stop.is_set()`` check happens only at the top of its while loop and
  is never re-checked between retry attempts, so an abandoning consumer
  or a dashboard-initiated pause waits out the entire retry chain, not
  just one request. Stopping the pool can take tens of seconds.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Callable, Iterator, TypeVar

from autosmart24.scraping.http_client import BlockedError, RateLimitedClient

JobT = TypeVar("JobT")
ResultT = TypeVar("ResultT")


@dataclass
class JobFailure:
    """One job that could not be completed. The rest of the queue is unaffected."""

    job: object
    error: BaseException


def run_worker_pool(
    jobs: list[JobT],
    worker_fn: Callable[[JobT, RateLimitedClient], list[ResultT]],
    client_factory: Callable[[], RateLimitedClient],
    concurrency: int,
    session_refresh_requests: int,
    failures: list[JobFailure] | None = None,
) -> Iterator[ResultT]:
    if not jobs:
        return

    # Callers that can infer failure from a missing result (the detail backlog
    # parks unreported rows; the confirmation pass simply declares nothing)
    # pass no list and read nothing back.
    failure_sink = failures if failures is not None else []

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
                try:
                    job_results = worker_fn(job, client)
                except BlockedError:
                    # The site is refusing us. Pressing on lengthens the block,
                    # so this stays fatal and reaches the outer handler below.
                    raise
                except Exception as exc:
                    # One unreachable page costs that page. Counted as processed
                    # so the session-refresh cadence still advances: a client
                    # that just failed is a client worth rotating.
                    with error_lock:
                        failure_sink.append(JobFailure(job, exc))
                    processed += 1
                    continue
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
