from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Callable

import httpx

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

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
    client: httpx.Client = field(
        default_factory=lambda: httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept-Language": "it-IT,it;q=0.9"},
            timeout=15.0,
            follow_redirects=True,
        )
    )
    sleep_fn: Callable[[float], None] = field(default=time.sleep)

    def get(self, url: str) -> httpx.Response:
        delay = random.uniform(self.min_delay_seconds, self.max_delay_seconds)
        self.sleep_fn(delay)
        response = self.client.get(url)
        if response.status_code in BLOCK_STATUS_CODES:
            raise BlockedError(response.status_code, url)
        response.raise_for_status()
        return response

    def close(self) -> None:
        self.client.close()
