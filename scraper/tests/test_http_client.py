import httpx
import pytest
import respx

from autosmart24.scraping.http_client import BlockedError, RateLimitedClient, USER_AGENTS, make_client
from autosmart24.scraping.rate_control import BlockRateTracker


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


def test_make_client_picks_a_user_agent_from_the_pool():
    client = make_client(min_delay_seconds=0, max_delay_seconds=0, sleep_fn=lambda _: None)
    try:
        assert client.user_agent in USER_AGENTS
    finally:
        client.close()


def test_make_client_rotates_user_agents_across_many_calls():
    clients = [make_client(0, 0, sleep_fn=lambda _: None) for _ in range(50)]
    try:
        assert len({c.user_agent for c in clients}) > 1
    finally:
        for c in clients:
            c.close()


@respx.mock
def test_get_records_success_on_rate_controller():
    respx.get("https://example.test/ok").mock(return_value=httpx.Response(200, text="ok"))
    tracker = BlockRateTracker()
    client = RateLimitedClient(
        min_delay_seconds=0, max_delay_seconds=0, sleep_fn=lambda _: None, rate_controller=tracker
    )
    client.get("https://example.test/ok")
    assert tracker.delay_multiplier() == 1.0


@respx.mock
def test_get_records_blocked_on_rate_controller_and_applies_backoff_multiplier():
    respx.get("https://example.test/blocked").mock(return_value=httpx.Response(403, text="forbidden"))
    tracker = BlockRateTracker(window_size=10, threshold=0.05, backoff_multiplier=2.0)
    delays: list[float] = []
    client = RateLimitedClient(
        min_delay_seconds=10, max_delay_seconds=10,
        sleep_fn=lambda d: delays.append(d), rate_controller=tracker,
    )
    with pytest.raises(BlockedError):
        client.get("https://example.test/blocked")
    assert tracker.delay_multiplier() == 2.0
    with pytest.raises(BlockedError):
        client.get("https://example.test/blocked")
    assert delays[-1] == 20.0
