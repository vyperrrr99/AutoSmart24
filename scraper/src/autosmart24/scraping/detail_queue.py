from __future__ import annotations

from dataclasses import dataclass

import httpx

from autosmart24.scraping.detail_mapper import map_detail_listing
from autosmart24.scraping.http_client import RateLimitedClient
from autosmart24.scraping.next_data import extract_next_data


@dataclass
class DetailResult:
    sold: bool
    data: dict | None = None
    # Dove ci ha mandati il sito, quando invece di rispondere ha rediretto.
    # Serve a chi legge per distinguere "sparito" da "id riusato" senza dover
    # scaricare la pagina di destinazione.
    redirect_to: str | None = None


def _identificativo(url: str) -> str:
    """L'UUID in fondo all'URL: e' l'unica parte che identifica l'annuncio.
    Lo slug davanti cambia quando il venditore modifica il titolo."""
    # Un UUID sono cinque gruppi separati da trattino, in fondo all'URL.
    return "-".join(url.rstrip("/").split("-")[-5:])


def fetch_detail(client: RateLimitedClient, url: str) -> DetailResult:
    try:
        # Senza seguire i redirect: vanno distinti, non subiti. Un annuncio
        # sparito non da' 404, viene rediretto alla pagina di lista del modello
        # -- diecimila risultati scaricati al posto di un annuncio. Ripetuto
        # migliaia di volte e' cio' che ci ha fatto bloccare tre volte su Fiat.
        response = client.get(url, follow_redirects=False)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (404, 410):
            return DetailResult(sold=True)
        raise

    if response.is_redirect:
        destinazione = str(response.headers.get("location", ""))
        # Stesso annuncio, titolo cambiato: l'UUID in fondo e' lo stesso.
        # Vale la pena seguirlo, e' la pagina che volevamo.
        if destinazione and _identificativo(destinazione) == _identificativo(url):
            response = client.get(destinazione, follow_redirects=False)
            if response.is_redirect:
                return DetailResult(sold=True, redirect_to=str(response.headers.get("location", "")))
        else:
            # Pagina di lista, o l'annuncio di un'altra auto sul nostro id
            # riusato. In entrambi i casi quello che cercavamo non c'e' piu',
            # e la pagina di destinazione non ci serve: non la scarichiamo.
            return DetailResult(sold=True, redirect_to=destinazione)

    data = extract_next_data(response.text)
    listing_details = data["props"]["pageProps"]["listingDetails"]
    mapped = map_detail_listing(listing_details)

    if mapped["source_status"] != "Active":
        return DetailResult(sold=True, data=mapped)

    return DetailResult(sold=False, data=mapped)
