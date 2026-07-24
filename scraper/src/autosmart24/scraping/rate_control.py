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
