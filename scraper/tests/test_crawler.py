import json

import httpx
import pytest
import respx

from autosmart24.scraping.crawler import crawl_brand
from autosmart24.scraping.http_client import BlockedError, RateLimitedClient
from autosmart24.scraping.search_query import build_search_url


def _next_data_html(page_props: dict) -> str:
    payload = {"props": {"pageProps": page_props}}
    return f'<html><body><script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script></body></html>'


def _fake_listing(listing_id: str, price: int) -> dict:
    return {
        "id": listing_id,
        "crossReferenceId": listing_id,
        "url": f"/annunci/{listing_id}",
        "price": {"priceRaw": price},
        "vehicle": {
            "make": "Fiat",
            "model": "Panda",
            "modelGroup": "Panda",
            "variant": None,
            "motorTypeName": "1.0",
            "modelVersionInput": None,
            "transmission": "Manuale",
            "fuel": "Benzina",
        },
        "location": {"city": "Roma - Roma - RM", "zip": "00100"},
        "seller": {"type": "Dealer", "companyName": "Test Dealer"},
        "tracking": {"firstRegistration": "01-2020", "mileage": "50000"},
    }


@respx.mock
def test_crawl_brand_yields_all_listings_across_pages():
    discovery_page_props = {
        "numberOfResults": 1,
        "numberOfPages": 1,
        "listings": [_fake_listing("discovery-1", 1000)],
        "taxonomy": {"models": {"28": [{"value": 1746, "label": "Panda"}]}},
    }
    model_page1_props = {
        "numberOfResults": 25,
        "numberOfPages": 2,
        "listings": [_fake_listing(f"p1-{i}", 10000 + i) for i in range(20)],
    }
    model_page2_props = {
        "numberOfResults": 25,
        "numberOfPages": 2,
        "listings": [_fake_listing(f"p2-{i}", 20000 + i) for i in range(5)],
    }

    discovery_url = build_search_url("fiat", page=1, make_id=28)
    page1_url = build_search_url("fiat", page=1, make_id=28, model_id=1746)
    page2_url = build_search_url("fiat", page=2, make_id=28, model_id=1746)

    respx.get(discovery_url).mock(return_value=httpx.Response(200, text=_next_data_html(discovery_page_props)))
    respx.get(page1_url).mock(return_value=httpx.Response(200, text=_next_data_html(model_page1_props)))
    respx.get(page2_url).mock(return_value=httpx.Response(200, text=_next_data_html(model_page2_props)))

    def client_factory():
        return RateLimitedClient(min_delay_seconds=0, max_delay_seconds=0, sleep_fn=lambda _: None)

    results = list(crawl_brand(client_factory, "fiat", 28, concurrency=1))

    assert len(results) == 25
    assert {r["id"] for r in results} == {f"p1-{i}" for i in range(20)} | {f"p2-{i}" for i in range(5)}


@respx.mock
def test_crawl_brand_splits_by_year_when_model_exceeds_threshold():
    discovery_page_props = {
        "numberOfResults": 1,
        "numberOfPages": 1,
        "listings": [_fake_listing("discovery-1", 1000)],
        "taxonomy": {"models": {"28": [{"value": 1746, "label": "Panda"}]}},
    }
    probe_over_threshold_props = {"numberOfResults": 5000, "numberOfPages": 200, "listings": []}
    year_range_props = {
        "numberOfResults": 2,
        "numberOfPages": 1,
        "listings": [_fake_listing("y1", 5000), _fake_listing("y2", 6000)],
    }

    discovery_url = build_search_url("fiat", page=1, make_id=28)
    probe_url = build_search_url("fiat", page=1, make_id=28, model_id=1746)

    respx.get(discovery_url).mock(return_value=httpx.Response(200, text=_next_data_html(discovery_page_props)))
    respx.get(probe_url).mock(return_value=httpx.Response(200, text=_next_data_html(probe_over_threshold_props)))
    # Any year-range query (bisection probes + final leaf fetches) returns the same small result set.
    respx.get(url__regex=r".*fregfrom=.*").mock(return_value=httpx.Response(200, text=_next_data_html(year_range_props)))

    def client_factory():
        return RateLimitedClient(min_delay_seconds=0, max_delay_seconds=0, sleep_fn=lambda _: None)

    results = list(crawl_brand(client_factory, "fiat", 28, concurrency=1))

    assert {r["id"] for r in results} == {"y1", "y2"}


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
