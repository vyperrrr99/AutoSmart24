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


@respx.mock
def test_get_retries_transient_timeout_then_succeeds():
    route = respx.get("https://example.test/flaky")
    route.side_effect = [httpx.ReadTimeout("timed out"), httpx.Response(200, text="ok")]

    response = _instant_client().get("https://example.test/flaky")

    assert response.status_code == 200
    assert response.text == "ok"
    assert route.call_count == 2


@respx.mock
def test_get_retries_remote_protocol_error_then_succeeds():
    """Second real production incident of this class: a Peugeot sweep died
    on httpx.RemoteProtocolError ("Server disconnected without sending a
    response") after the original ReadTimeout-only retry didn't cover it --
    RemoteProtocolError is a sibling of TimeoutException/NetworkError under
    the common httpx.TransportError parent, not a subclass of either."""
    route = respx.get("https://example.test/flaky-remote")
    route.side_effect = [
        httpx.RemoteProtocolError("Server disconnected without sending a response"),
        httpx.Response(200, text="ok"),
    ]

    response = _instant_client().get("https://example.test/flaky-remote")

    assert response.status_code == 200
    assert response.text == "ok"
    assert route.call_count == 2


@respx.mock
def test_get_exhausts_retries_and_raises_original_timeout():
    route = respx.get("https://example.test/always-flaky")
    route.side_effect = httpx.ReadTimeout("timed out")

    with pytest.raises(httpx.ReadTimeout):
        _instant_client().get("https://example.test/always-flaky")

    assert route.call_count == 3


@respx.mock
def test_get_does_not_retry_blocked_error():
    route = respx.get("https://example.test/blocked-no-retry")
    route.mock(return_value=httpx.Response(403, text="forbidden"))

    with pytest.raises(BlockedError):
        _instant_client().get("https://example.test/blocked-no-retry")

    assert route.call_count == 1


@respx.mock
def test_get_does_not_retry_http_status_error():
    route = respx.get("https://example.test/server-error")
    route.mock(return_value=httpx.Response(500, text="boom"))

    with pytest.raises(httpx.HTTPStatusError):
        _instant_client().get("https://example.test/server-error")

    assert route.call_count == 1


@respx.mock
def test_get_retry_records_success_once_and_never_records_blocked():
    route = respx.get("https://example.test/flaky-tracked")
    route.side_effect = [httpx.ReadTimeout("timed out"), httpx.Response(200, text="ok")]
    tracker = BlockRateTracker()

    client = RateLimitedClient(
        min_delay_seconds=0, max_delay_seconds=0, sleep_fn=lambda _: None, rate_controller=tracker
    )
    success_calls = []
    blocked_calls = []
    tracker.record_success = lambda: success_calls.append(1)  # type: ignore[method-assign]
    tracker.record_blocked = lambda: blocked_calls.append(1)  # type: ignore[method-assign]

    client.get("https://example.test/flaky-tracked")

    assert len(success_calls) == 1
    assert len(blocked_calls) == 0


@respx.mock
def test_get_respects_configured_retry_count_of_zero():
    route = respx.get("https://example.test/no-retries")
    route.side_effect = httpx.ReadTimeout("timed out")

    client = RateLimitedClient(
        min_delay_seconds=0, max_delay_seconds=0, sleep_fn=lambda _: None, retries=0
    )

    with pytest.raises(httpx.ReadTimeout):
        client.get("https://example.test/no-retries")

    assert route.call_count == 1


@respx.mock
def test_get_sleeps_once_per_attempt_not_once_total():
    """Pins the anti-hammering property: each retry attempt pays its own
    rate-limit delay. A "hoist the delay above the loop" refactor would
    make all retries fire back-to-back with zero spacing against a site
    that just failed on us, and must be caught here."""
    route = respx.get("https://example.test/flaky-delay")
    route.side_effect = [
        httpx.ReadTimeout("timed out"),
        httpx.ReadTimeout("timed out"),
        httpx.Response(200, text="ok"),
    ]
    delays: list[float] = []
    client = RateLimitedClient(
        min_delay_seconds=5, max_delay_seconds=5,
        sleep_fn=lambda d: delays.append(d),
    )

    response = client.get("https://example.test/flaky-delay")

    assert response.status_code == 200
    assert delays == [5.0, 5.0, 5.0]


@respx.mock
def test_get_negative_retries_behaves_like_zero_retries():
    route = respx.get("https://example.test/negative-retries")
    route.side_effect = httpx.ReadTimeout("timed out")

    client = RateLimitedClient(
        min_delay_seconds=0, max_delay_seconds=0, sleep_fn=lambda _: None, retries=-1
    )

    with pytest.raises(httpx.ReadTimeout):
        client.get("https://example.test/negative-retries")

    assert route.call_count == 1


# --- proxy ------------------------------------------------------------------
#
# La seconda macchina raccoglie dallo stesso sito con un IP diverso. Senza
# proxy configurabile uscirebbe con l'IP di casa, cioe' lo stesso della prima:
# raddoppierebbe la frequenza sullo stesso indirizzo, che e' il modo piu'
# rapido per farsi bloccare entrambe.

def test_no_proxy_configured_leaves_the_client_direct(monkeypatch):
    monkeypatch.delenv("SCRAPE_PROXY", raising=False)
    c = make_client(0.0, 0.0)
    assert c.proxy_url is None
    c.close()


def test_the_proxy_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("SCRAPE_PROXY", "http://utente:chiave@proxy.esempio:8080")
    c = make_client(0.0, 0.0)
    assert c.proxy_url == "http://utente:chiave@proxy.esempio:8080"
    c.close()


def test_an_explicit_proxy_beats_the_environment(monkeypatch):
    """Perche' si possa provarne uno senza toccare la configurazione."""
    monkeypatch.setenv("SCRAPE_PROXY", "http://dall-ambiente:8080")
    c = make_client(0.0, 0.0, proxy_url="http://esplicito:9090")
    assert c.proxy_url == "http://esplicito:9090"
    c.close()


def test_an_empty_proxy_variable_means_no_proxy(monkeypatch):
    """Una variabile impostata a stringa vuota e' il modo normale di
    disattivarla in un file di ambiente: non deve diventare un proxy vuoto."""
    monkeypatch.setenv("SCRAPE_PROXY", "")
    c = make_client(0.0, 0.0)
    assert c.proxy_url is None
    c.close()


def test_httpx_actually_receives_the_proxy(monkeypatch):
    """Non basta che il campo sia valorizzato: deve arrivare a httpx.

    I test qui sopra guardano `proxy_url`, quindi restano verdi anche se il
    valore non viene passato al client -- e le richieste uscirebbero dall'IP di
    casa credendo di uscire dal proxy. Questo controlla il montaggio vero.
    """
    monkeypatch.setenv("SCRAPE_PROXY", "http://proxy.esempio:8080")
    con = make_client(0.0, 0.0)
    monkeypatch.delenv("SCRAPE_PROXY")
    senza = make_client(0.0, 0.0)
    try:
        assert con.client._mounts, "nessun proxy montato: le richieste uscirebbero dirette"
        assert not senza.client._mounts, "montato un proxy che non era configurato"
    finally:
        con.close()
        senza.close()
