from pathlib import Path

import httpx
import respx

from autosmart24.scraping.detail_queue import fetch_detail
from autosmart24.scraping.http_client import RateLimitedClient

FIXTURES = Path(__file__).parent / "fixtures"


def _client() -> RateLimitedClient:
    return RateLimitedClient(min_delay_seconds=0, max_delay_seconds=0, sleep_fn=lambda _: None)


@respx.mock
def test_fetch_detail_returns_data_when_active():
    html = (FIXTURES / "detail_fiat_grande_panda.html").read_text(encoding="utf-8")
    url = "https://www.autoscout24.it/annunci/fiat-grande-panda-test"
    respx.get(url).mock(return_value=httpx.Response(200, text=html))

    result = fetch_detail(_client(), url)

    assert result.sold is False
    assert result.data["brand"] == "Fiat"


@respx.mock
def test_fetch_detail_marks_sold_on_404():
    url = "https://www.autoscout24.it/annunci/gone"
    respx.get(url).mock(return_value=httpx.Response(404, text="not found"))

    result = fetch_detail(_client(), url)

    assert result.sold is True
    assert result.data is None


@respx.mock
def test_fetch_detail_marks_sold_when_status_not_active():
    html = (FIXTURES / "detail_fiat_grande_panda.html").read_text(encoding="utf-8")
    modified = html.replace('"status":"Active"', '"status":"Removed"')
    url = "https://www.autoscout24.it/annunci/removed"
    respx.get(url).mock(return_value=httpx.Response(200, text=modified))

    result = fetch_detail(_client(), url)

    assert result.sold is True
    assert result.data["source_status"] == "Removed"
