from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterator

from autosmart24.config import MAX_RESULTS_PER_QUERY
from autosmart24.scraping.concurrency import JobFailure, run_worker_pool
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


@dataclass
class CrawlReport:
    """What the crawl could not fetch, kept separate by severity.

    A lost page is worth roughly PAGE_SIZE listings and can be estimated. A
    lost model was dropped while learning how many pages it has, so its size
    is unknown and no estimate is possible -- which is why the two are not
    merged into a single counter.

    `finished` guards against reading a partial report as a clean one. The
    lists are only populated once `crawl_brand` reaches its final block, so a
    consumer that abandons the generator early (or one killed mid-flight by a
    fatal `BlockedError`) leaves them empty -- which must not look the same as
    a crawl that genuinely lost nothing. `complete` fails closed on this: an
    unfinished crawl is never complete, whatever its lists contain, because
    the caller deciding whether sold detection may run needs "we don't know"
    to read as "no", not as "yes".
    """

    lost_models: list = field(default_factory=list)
    lost_pages: list = field(default_factory=list)
    finished: bool = False

    @property
    def complete(self) -> bool:
        return self.finished and not self.lost_models and not self.lost_pages


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
    report: CrawlReport | None = None,
) -> Iterator[dict]:
    bootstrap_client = client_factory()
    try:
        models = discover_models(bootstrap_client, brand_slug, make_id)
    finally:
        bootstrap_client.close()

    def _discovery_worker(model: ModelInfo, client: RateLimitedClient) -> list[tuple[QueryUnit, list[dict]]]:
        return _discover_model_units(model, client, brand_slug, make_id, year_from)

    units: list[QueryUnit] = []
    discovery_failures: list[JobFailure] = []
    for unit, listings in run_worker_pool(
        models, _discovery_worker, client_factory, concurrency, session_refresh_requests,
        failures=discovery_failures,
    ):
        units.append(unit)
        yield from listings

    # Retry the lost models before the page list is built: a model recovered
    # here still contributes its pages below, whereas one recovered afterwards
    # would silently contribute only its first page. Minutes have passed since
    # the first attempt, which is what makes a retry worth making at all.
    lost_models: list[JobFailure] = []
    if discovery_failures:
        retry_models = [f.job for f in discovery_failures]
        for unit, listings in run_worker_pool(
            retry_models, _discovery_worker, client_factory, concurrency, session_refresh_requests,
            failures=lost_models,
        ):
            units.append(unit)
            yield from listings

    def _page_worker(job: tuple[int, int | None, int | None, int], client: RateLimitedClient) -> list[dict]:
        model_id, yf, yt, page = job
        url = build_search_url(brand_slug, page=page, make_id=make_id, model_id=model_id, year_from=yf, year_to=yt)
        return list(_iter_listings_from_page(fetch_page_data(client, url)))

    page_jobs: list[tuple[int, int | None, int | None, int]] = []
    for unit in units:
        for page in range(2, unit.number_of_pages + 1):
            page_jobs.append((unit.model_id, unit.year_from, unit.year_to, page))

    page_failures: list[JobFailure] = []
    yield from run_worker_pool(
        page_jobs, _page_worker, client_factory, concurrency, session_refresh_requests,
        failures=page_failures,
    )

    lost_pages: list[JobFailure] = []
    if page_failures:
        yield from run_worker_pool(
            [f.job for f in page_failures], _page_worker, client_factory,
            concurrency, session_refresh_requests, failures=lost_pages,
        )

    if report is not None:
        report.lost_models.extend(f.job for f in lost_models)
        report.lost_pages.extend(f.job for f in lost_pages)
        report.finished = True
