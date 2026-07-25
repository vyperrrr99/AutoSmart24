import json

import httpx
import respx

from autosmart24.scraping.brand_catalog import derive_slug, fetch_brand_catalog
from autosmart24.scraping.http_client import RateLimitedClient
from autosmart24.scraping.search_query import build_search_url


def test_derive_slug_single_word():
    assert derive_slug("Fiat") == "fiat"


def test_derive_slug_two_words():
    assert derive_slug("Alfa Romeo") == "alfa-romeo"


def test_derive_slug_already_hyphenated():
    assert derive_slug("Mercedes-Benz") == "mercedes-benz"


def test_derive_slug_collapses_multiple_separators():
    assert derive_slug("Land  Rover") == "land-rover"


def test_derive_slug_strips_leading_trailing_punctuation():
    assert derive_slug(" DS Automobiles ") == "ds-automobiles"


def _next_data_html(page_props: dict) -> str:
    payload = {"props": {"pageProps": page_props}}
    return f'<html><body><script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script></body></html>'


@respx.mock
def test_fetch_brand_catalog_parses_all_makes():
    page_props = {
        "taxonomy": {
            "makes": {
                "6": {"label": "Alfa Romeo", "value": 6},
                "28": {"label": "Fiat", "value": 28},
                "47": {"label": "Mercedes-Benz", "value": 47},
            }
        }
    }
    url = build_search_url("fiat", page=1, make_id=28)
    respx.get(url).mock(return_value=httpx.Response(200, text=_next_data_html(page_props)))

    client = RateLimitedClient(min_delay_seconds=0, max_delay_seconds=0, sleep_fn=lambda _: None)
    entries = fetch_brand_catalog(client)

    by_make_id = {e.make_id: e for e in entries}
    assert len(entries) == 3
    assert by_make_id[6].display_name == "Alfa Romeo"
    assert by_make_id[6].slug == "alfa-romeo"
    assert by_make_id[28].slug == "fiat"
    assert by_make_id[47].slug == "mercedes-benz"
