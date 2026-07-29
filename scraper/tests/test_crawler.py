import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from autosmart24.scraping.crawler import CrawlReport, ModelInfo, QueryUnit, crawl_brand
from autosmart24.scraping.http_client import BlockedError, RateLimitedClient
from autosmart24.scraping.search_query import build_search_url


def _client_factory() -> RateLimitedClient:
    return RateLimitedClient(min_delay_seconds=0, max_delay_seconds=0, sleep_fn=lambda _: None)


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


@respx.mock
def test_crawl_brand_year_split_never_probes_below_the_year_floor():
    """The bisection's lower bound must be the caller's floor, not MIN_YEAR.
    Starting at 1950 would rescan decades the floor exists to exclude, and the
    other split test cannot detect it because it mocks every year range
    identically and asserts on a deduplicated id set."""
    discovery_page_props = {
        "numberOfResults": 1,
        "numberOfPages": 1,
        "listings": [],
        "taxonomy": {"models": {"28": [{"value": 1746, "label": "Panda"}]}},
    }
    over_cap_props = {"numberOfResults": 999_999, "numberOfPages": 200, "listings": []}
    leaf_props = {
        "numberOfResults": 2,
        "numberOfPages": 1,
        "listings": [_fake_listing("leaf-1", 5000)],
    }

    respx.get(build_search_url("fiat", page=1, make_id=28)).mock(
        return_value=httpx.Response(200, text=_next_data_html(discovery_page_props))
    )
    respx.get(build_search_url("fiat", page=1, make_id=28, model_id=1746, year_from=2015)).mock(
        return_value=httpx.Response(200, text=_next_data_html(over_cap_props))
    )
    respx.route().mock(return_value=httpx.Response(200, text=_next_data_html(leaf_props)))

    def client_factory():
        return RateLimitedClient(min_delay_seconds=0, max_delay_seconds=0, sleep_fn=lambda _: None)

    list(crawl_brand(client_factory, "fiat", 28, year_from=2015, concurrency=1))

    seen_year_froms = set()
    for call in respx.calls:
        params = parse_qs(urlparse(str(call.request.url)).query)
        if "fregfrom" in params:
            seen_year_froms.add(int(params["fregfrom"][0]))

    assert seen_year_froms, "expected at least one year-filtered request"
    assert min(seen_year_froms) >= 2015, f"bisection probed below the floor: {sorted(seen_year_froms)}"


@respx.mock
def test_crawl_brand_keeps_the_year_floor_on_phase_two_page_urls():
    """The floor must survive into the pages fetched after the probe. The other
    floor test uses a single-page model, so phase 2 never runs there and a
    dropped year_from in the page worker would go unnoticed."""
    discovery_page_props = {
        "numberOfResults": 1,
        "numberOfPages": 1,
        "listings": [],
        "taxonomy": {"models": {"28": [{"value": 1746, "label": "Panda"}]}},
    }
    multi_page_props = {
        "numberOfResults": 60,
        "numberOfPages": 3,
        "listings": [_fake_listing("p-1", 9000)],
    }

    respx.get(build_search_url("fiat", page=1, make_id=28)).mock(
        return_value=httpx.Response(200, text=_next_data_html(discovery_page_props))
    )
    for page in (1, 2, 3):
        respx.get(build_search_url("fiat", page=page, make_id=28, model_id=1746, year_from=2021)).mock(
            return_value=httpx.Response(200, text=_next_data_html(multi_page_props))
        )

    def client_factory():
        return RateLimitedClient(min_delay_seconds=0, max_delay_seconds=0, sleep_fn=lambda _: None)

    list(crawl_brand(client_factory, "fiat", 28, year_from=2021, concurrency=2))

    requested_pages = {}
    for call in respx.calls:
        params = parse_qs(urlparse(str(call.request.url)).query)
        if "mmmv" not in params:
            continue
        page = int(params["page"][0])
        requested_pages[page] = params.get("fregfrom", [None])[0]

    assert set(requested_pages) == {1, 2, 3}, f"expected pages 1-3, got {sorted(requested_pages)}"
    assert all(v == "2021" for v in requested_pages.values()), requested_pages


def test_crawl_report_is_not_complete_when_freshly_constructed():
    """A report only reflects reality once `crawl_brand` has actually
    finished writing to it -- a fresh (or partially drained) report must read
    as unknown, never as a clean crawl."""
    assert CrawlReport().complete is False


def test_crawl_report_is_complete_only_when_finished_and_nothing_was_lost():
    assert CrawlReport(finished=True).complete is True
    assert CrawlReport(finished=True, lost_pages=[("a",)]).complete is False
    assert CrawlReport(finished=True, lost_models=[("m",)]).complete is False
    assert CrawlReport(finished=False, lost_pages=[], lost_models=[]).complete is False


def test_crawl_brand_recovers_a_discovery_that_failed_the_first_time(monkeypatch):
    """A model lost in discovery costs the whole model, so it is retried first
    and its pages must then be fetched like any other model's."""
    attempts: dict[str, int] = {}

    def fake_discover(model, client, brand_slug, make_id, year_from):
        attempts[model.model_id] = attempts.get(model.model_id, 0) + 1
        if model.model_id == 2 and attempts[2] == 1:
            raise TimeoutError("timed out")
        unit = QueryUnit(model.model_id, None, None, 2)
        return [(unit, [{"id": f"m{model.model_id}-p1"}])]

    def fake_page(client, url):
        return {"listings": []}

    monkeypatch.setattr("autosmart24.scraping.crawler._discover_model_units", fake_discover)
    monkeypatch.setattr("autosmart24.scraping.crawler.discover_models",
                        lambda c, s, m: [ModelInfo(1, "one"), ModelInfo(2, "two")])
    monkeypatch.setattr("autosmart24.scraping.crawler.fetch_page_data",
                        lambda client, url: {"listings": [{"id": url}]})
    monkeypatch.setattr("autosmart24.scraping.crawler.map_snippet_listing", lambda raw: raw)

    report = CrawlReport()
    out = list(crawl_brand(_client_factory, "brand", 7, concurrency=1,
                           session_refresh_requests=100, report=report))

    assert attempts[2] == 2, "the failed discovery must be retried exactly once"
    assert report.complete is True
    ids = {item["id"] for item in out if "id" in item}
    assert "m2-p1" in ids, "the recovered model's first page must be yielded"
    # Model 1 also has number_of_pages=2, so a bare "page=2" substring check
    # would pass even if model 2's own page 2 were never queued -- it must be
    # tied to model 2's own mmmv marker (7|2||, urlencoded) to actually prove
    # the recovered model's pages were queued, not just some other model's.
    assert any("page=2" in str(item.get("id", "")) and "mmmv=7%7C2%7C%7C" in str(item.get("id", ""))
               for item in out), \
        "the recovered model's remaining pages must be fetched too"


def test_crawl_brand_reports_a_discovery_it_could_not_recover(monkeypatch):
    def always_fails(model, client, brand_slug, make_id, year_from):
        raise TimeoutError("timed out")

    monkeypatch.setattr("autosmart24.scraping.crawler._discover_model_units", always_fails)
    monkeypatch.setattr("autosmart24.scraping.crawler.discover_models",
                        lambda c, s, m: [ModelInfo(1, "one")])

    report = CrawlReport()
    out = list(crawl_brand(_client_factory, "brand", 7, concurrency=1,
                           session_refresh_requests=100, report=report))

    assert out == []
    assert len(report.lost_models) == 1
    assert report.lost_pages == []
    assert report.complete is False


def test_crawl_brand_reports_a_page_it_could_not_recover(monkeypatch):
    def fake_discover(model, client, brand_slug, make_id, year_from):
        return [(QueryUnit(model.model_id, None, None, 2), [])]

    def failing_page(client, url):
        raise TimeoutError("timed out")

    monkeypatch.setattr("autosmart24.scraping.crawler._discover_model_units", fake_discover)
    monkeypatch.setattr("autosmart24.scraping.crawler.discover_models",
                        lambda c, s, m: [ModelInfo(1, "one")])
    monkeypatch.setattr("autosmart24.scraping.crawler.fetch_page_data", failing_page)

    report = CrawlReport()
    list(crawl_brand(_client_factory, "brand", 7, concurrency=1,
                     session_refresh_requests=100, report=report))

    assert report.lost_models == []
    assert len(report.lost_pages) == 1
    assert report.complete is False


@respx.mock
def test_crawl_brand_works_without_a_report():
    """The report is optional so existing callers and tests keep working."""
    discovery_page_props = {
        "numberOfResults": 0,
        "numberOfPages": 0,
        "listings": [],
        "taxonomy": {"models": {"28": []}},
    }
    respx.get(build_search_url("fiat", page=1, make_id=28)).mock(
        return_value=httpx.Response(200, text=_next_data_html(discovery_page_props))
    )

    assert list(crawl_brand(_client_factory, "fiat", 28, concurrency=1)) == []


def test_crawl_brand_report_stays_incomplete_when_the_generator_is_abandoned(monkeypatch):
    """A consumer that stops draining early (a `break`, an abandoned
    generator) must not leave the report looking like a clean crawl: the
    final block that populates it never runs, so `complete` must read False
    on whatever partial state is left behind, not on an empty-looks-clean
    default."""
    def fake_discover(model, client, brand_slug, make_id, year_from):
        unit = QueryUnit(model.model_id, None, None, 2)
        return [(unit, [{"id": f"m{model.model_id}-p1"}])]

    monkeypatch.setattr("autosmart24.scraping.crawler._discover_model_units", fake_discover)
    monkeypatch.setattr("autosmart24.scraping.crawler.discover_models",
                        lambda c, s, m: [ModelInfo(1, "one"), ModelInfo(2, "two")])
    monkeypatch.setattr("autosmart24.scraping.crawler.fetch_page_data",
                        lambda client, url: {"listings": [{"id": url}]})
    monkeypatch.setattr("autosmart24.scraping.crawler.map_snippet_listing", lambda raw: raw)

    report = CrawlReport()
    gen = crawl_brand(_client_factory, "brand", 7, concurrency=1,
                       session_refresh_requests=100, report=report)
    next(gen)
    gen.close()

    assert report.complete is False
