from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from autosmart24.config import MAX_RESULTS_PER_QUERY
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


def _crawl_remaining_pages(
    client: RateLimitedClient,
    brand_slug: str,
    make_id: int,
    model_id: int,
    year_from: int | None,
    year_to: int | None,
    number_of_pages: int,
) -> Iterator[dict]:
    for page in range(2, number_of_pages + 1):
        url = build_search_url(
            brand_slug, page=page, make_id=make_id, model_id=model_id, year_from=year_from, year_to=year_to
        )
        yield from _iter_listings_from_page(fetch_page_data(client, url))


def _crawl_all_pages(
    client: RateLimitedClient,
    brand_slug: str,
    make_id: int,
    model_id: int,
    year_from: int | None,
    year_to: int | None,
) -> Iterator[dict]:
    url = build_search_url(
        brand_slug, page=1, make_id=make_id, model_id=model_id, year_from=year_from, year_to=year_to
    )
    page_props = fetch_page_data(client, url)
    yield from _iter_listings_from_page(page_props)
    yield from _crawl_remaining_pages(
        client, brand_slug, make_id, model_id, year_from, year_to, page_props["numberOfPages"]
    )


def crawl_brand(client: RateLimitedClient, brand_slug: str, make_id: int) -> Iterator[dict]:
    models = discover_models(client, brand_slug, make_id)

    for model in models:
        probe_url = build_search_url(brand_slug, page=1, make_id=make_id, model_id=model.model_id)
        probe_page_props = fetch_page_data(client, probe_url)
        total_results = probe_page_props["numberOfResults"]

        if total_results <= MAX_RESULTS_PER_QUERY:
            yield from _iter_listings_from_page(probe_page_props)
            yield from _crawl_remaining_pages(
                client, brand_slug, make_id, model.model_id, None, None, probe_page_props["numberOfPages"]
            )
        else:
            year_ranges = split_year_ranges(
                lambda yf, yt: _count_for_year_range(client, brand_slug, make_id, model.model_id, yf, yt),
                MIN_YEAR,
                MAX_YEAR,
                MAX_RESULTS_PER_QUERY,
            )
            for year_from, year_to in year_ranges:
                yield from _crawl_all_pages(client, brand_slug, make_id, model.model_id, year_from, year_to)
