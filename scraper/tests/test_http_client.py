import httpx
import pytest
import respx

from autosmart24.scraping.http_client import BlockedError, RateLimitedClient


def _instant_client() -> RateLimitedClient:
    return RateLimitedClient(min_delay_seconds=0, max_delay_seconds=0, sleep_fn=lambda _: None)


@respx.mock
def test_get_returns_response_body():
    respx.get("https://example.test/page").mock(return_value=httpx.Response(200, text="ok"))

    response = _instant_client().get("https://example.test/page")

    assert response.status_code == 200
    assert response.text == "ok"


@respx.mock
def test_get_raises_blocked_error_on_403():
    respx.get("https://example.test/blocked").mock(return_value=httpx.Response(403, text="forbidden"))

    with pytest.raises(BlockedError) as exc_info:
        _instant_client().get("https://example.test/blocked")

    assert exc_info.value.status_code == 403


@respx.mock
def test_get_raises_blocked_error_on_429():
    respx.get("https://example.test/limited").mock(return_value=httpx.Response(429, text="too many"))

    with pytest.raises(BlockedError):
        _instant_client().get("https://example.test/limited")


@respx.mock
def test_get_raises_http_status_error_on_404():
    respx.get("https://example.test/gone").mock(return_value=httpx.Response(404, text="not found"))

    with pytest.raises(httpx.HTTPStatusError):
        _instant_client().get("https://example.test/gone")
