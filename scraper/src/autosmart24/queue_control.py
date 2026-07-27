from __future__ import annotations

import datetime as dt
import threading
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class QueueState:
    halted: bool
    reason: str | None
    halted_at: dt.datetime | None


class QueueController:
    """Global stop switch for the scrape queue.

    A run that ends `blocked` halts the queue: with a rate-limited or banned
    IP, letting the remaining brands run would turn one block into a cascade
    of failures and deepen the block. A run that ends `error` leaves the
    queue alone -- that is an isolated fault, not a signal about the whole
    site. Resuming is deliberate and manual, from the dashboard.

    State is in-process, matching the scheduler it guards; a container
    restart clears it, which is acceptable because restarting is itself a
    manual act.
    """

    def __init__(self, now_fn: Callable[[], dt.datetime] = dt.datetime.utcnow) -> None:
        self._lock = threading.Lock()
        self._now_fn = now_fn
        self._halted = False
        self._reason: str | None = None
        self._halted_at: dt.datetime | None = None

    def is_halted(self) -> bool:
        with self._lock:
            return self._halted

    def halt(self, reason: str) -> None:
        with self._lock:
            # Keep the first reason: it names the run that actually hit the
            # block, which is the one worth showing to the operator.
            if self._halted:
                return
            self._halted = True
            self._reason = reason
            self._halted_at = self._now_fn()

    def resume(self) -> None:
        with self._lock:
            self._halted = False
            self._reason = None
            self._halted_at = None

    def state(self) -> QueueState:
        with self._lock:
            return QueueState(halted=self._halted, reason=self._reason, halted_at=self._halted_at)
