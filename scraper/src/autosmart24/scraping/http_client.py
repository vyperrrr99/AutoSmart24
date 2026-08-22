from __future__ import annotations

import logging
import random
import os
import time
from dataclasses import dataclass, field
from typing import Callable

import httpx

from autosmart24.scraping.rate_control import BlockRateTracker

logger = logging.getLogger(__name__)

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
    retries: int = 2
    # Una seconda macchina raccoglie dallo stesso sito con un IP diverso. Senza
    # proxy uscirebbe con lo stesso indirizzo della prima: raddoppierebbe la
    # frequenza su quell'IP, che e' il modo piu' rapido per far bloccare
    # entrambe invece di aggiungere capacita'.
    proxy_url: str | None = None
    client: httpx.Client = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.client = httpx.Client(
            headers={"User-Agent": self.user_agent, "Accept-Language": "it-IT,it;q=0.9"},
            timeout=15.0,
            follow_redirects=True,
            proxy=self.proxy_url,
        )

    def get(self, url: str, follow_redirects: bool | None = None) -> httpx.Response:
        """`follow_redirects=False` restituisce il 3xx invece di seguirlo.

        Serve sulle pagine di dettaglio: AutoScout non risponde 404 per un
        annuncio sparito, rimanda alla pagina di lista del modello. Seguendo il
        redirect si scarica una pagina da diecimila risultati al posto di un
        annuncio, e farlo migliaia di volte di fila e' cio' per cui il sito ci
        ha bloccati tre volte.
        """
        attempts = max(0, self.retries) + 1
        for attempt in range(1, attempts + 1):
            multiplier = self.rate_controller.delay_multiplier() if self.rate_controller else 1.0
            delay = random.uniform(self.min_delay_seconds, self.max_delay_seconds) * multiplier
            self.sleep_fn(delay)
            try:
                response = self.client.get(url) if follow_redirects is None \
                    else self.client.get(url, follow_redirects=follow_redirects)
            except httpx.TransportError as exc:
                if attempt >= attempts:
                    raise
                logger.warning(
                    "Transient network error fetching %s, retrying (attempt %d/%d): %s: %s",
                    url,
                    attempt + 1,
                    attempts,
                    type(exc).__name__,
                    exc,
                )
                continue
            if response.status_code in BLOCK_STATUS_CODES:
                if self.rate_controller:
                    self.rate_controller.record_blocked()
                raise BlockedError(response.status_code, url)
            if self.rate_controller:
                self.rate_controller.record_success()
            # httpx considera un 3xx un errore quando i redirect non vengono
            # seguiti. Qui invece e' il risultato che ci interessa: chi chiede
            # di non seguirli vuole vedere dove il sito lo stava mandando.
            if not (follow_redirects is False and response.is_redirect):
                response.raise_for_status()
            return response
        raise AssertionError("unreachable: get() loop exited without returning or raising")

    def close(self) -> None:
        self.client.close()


def make_client(
    min_delay_seconds: float,
    max_delay_seconds: float,
    rate_controller: BlockRateTracker | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    retries: int = 2,
    proxy_url: str | None = None,
) -> RateLimitedClient:
    # `or None` e non `get(..., None)`: in un file di ambiente si disattiva una
    # variabile lasciandola vuota, e una stringa vuota passata a httpx non e'
    # "nessun proxy", e' un proxy senza indirizzo.
    proxy_url = proxy_url or os.environ.get("SCRAPE_PROXY") or None
    return RateLimitedClient(
        proxy_url=proxy_url,
        min_delay_seconds=min_delay_seconds,
        max_delay_seconds=max_delay_seconds,
        user_agent=random.choice(USER_AGENTS),
        rate_controller=rate_controller,
        sleep_fn=sleep_fn,
        retries=retries,
    )
