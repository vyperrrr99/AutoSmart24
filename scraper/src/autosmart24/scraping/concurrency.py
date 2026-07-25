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
        client = client_factory()
        processed = 0
        try:
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
                except BaseException as exc:
                    # Captured and re-raised to the caller below. Letting it kill
                    # the thread would silently truncate the job list.
                    with error_lock:
                        error_holder.append(exc)
                    _drain_queue()
                    return
                processed += 1
                for item in job_results:
                    results.put(item)
        finally:
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
