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


# --- redirect ----------------------------------------------------------------
#
# AutoScout non risponde 404 per un annuncio sparito: rimanda alla pagina di
# lista del modello. Seguendo il redirect si scarica una pagina da diecimila
# risultati al posto di un annuncio, e farlo migliaia di volte di fila e' cio'
# per cui il sito ci ha bloccati tre volte su Fiat -- 5 pagine su 20
# dell'arretrato Panda facevano cosi'.

_UUID = "e3517c11-7d63-42b8-8ea5-9565a5657a40"
_DETT = f"https://www.autoscout24.it/annunci/fiat-panda-lounge-cat_ma28mo1746-{_UUID}"


@respx.mock
def test_a_redirect_to_a_list_page_means_gone_and_is_not_downloaded():
    respx.get(_DETT).mock(return_value=httpx.Response(
        302, headers={"location": "https://www.autoscout24.it/lst/fiat/panda"}))
    lista = respx.get("https://www.autoscout24.it/lst/fiat/panda").mock(
        return_value=httpx.Response(200, text="<html>diecimila risultati</html>"))

    r = fetch_detail(_client(), _DETT)

    assert r.sold is True
    assert r.redirect_to.endswith("/lst/fiat/panda")
    assert not lista.called, "la pagina di lista NON deve essere scaricata: e' il danno"


@respx.mock
def test_a_redirect_to_a_different_listing_means_the_id_was_reused():
    """Non e' la nostra auto: l'id e' stato riassegnato. Sparita comunque, e la
    pagina dell'altra auto non ci serve."""
    altro = "https://www.autoscout24.it/annunci/audi-a3-cat_ma9mo99-11111111-2222-3333-4444-555555555555"
    respx.get(_DETT).mock(return_value=httpx.Response(308, headers={"location": altro}))
    pagina_altrui = respx.get(altro).mock(return_value=httpx.Response(200, text="<html/>"))

    r = fetch_detail(_client(), _DETT)

    assert r.sold is True
    assert not pagina_altrui.called, "non scarichiamo l'annuncio di un'altra auto"


@respx.mock
def test_a_redirect_keeping_the_same_uuid_is_only_a_renamed_title():
    """Il venditore ha cambiato il titolo: lo slug cambia, l'UUID no. E' la
    pagina che volevamo, e va seguita."""
    nuovo = f"https://www.autoscout24.it/annunci/fiat-panda-TITOLO-NUOVO-cat_ma28mo1746-{_UUID}"
    respx.get(_DETT).mock(return_value=httpx.Response(301, headers={"location": nuovo}))
    respx.get(nuovo).mock(return_value=httpx.Response(
        200, text=(FIXTURES / "detail_fiat_grande_panda.html").read_text(encoding="utf-8")))

    r = fetch_detail(_client(), _DETT)

    assert r.sold is False
    assert r.data is not None, "seguito il redirect, i dati ci sono"
