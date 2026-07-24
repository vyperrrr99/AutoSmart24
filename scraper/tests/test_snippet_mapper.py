from pathlib import Path

from autosmart24.scraping.next_data import extract_next_data
from autosmart24.scraping.snippet_mapper import map_snippet_listing

FIXTURES = Path(__file__).parent / "fixtures"


def _first_raw_listing() -> dict:
    html = (FIXTURES / "search_fiat_page1.html").read_text(encoding="utf-8")
    data = extract_next_data(html)
    return data["props"]["pageProps"]["listings"][0]


def test_map_snippet_listing_extracts_core_fields():
    raw = _first_raw_listing()
    mapped = map_snippet_listing(raw)

    assert mapped["id"] == "b73b0c64-3c16-4215-b927-02a5fe324ee7"
    assert mapped["brand"] == "Fiat"
    assert mapped["model"] == "Grande Panda"
    assert mapped["price"] == 13990
    assert mapped["mileage_km"] == 10
    assert mapped["first_registration"].isoformat() == "2026-04-01"
    assert mapped["seller_type"] == "Dealer"
    assert mapped["url"] == (
        "https://www.autoscout24.it/annunci/"
        "fiat-grande-panda-benzina-icon-cambio-manuale-promo-flex-benzina-cat_ma28mo76901-"
        "b73b0c64-3c16-4215-b927-02a5fe324ee7"
    )
    assert mapped["raw_snippet"] == raw


def test_map_snippet_listing_handles_missing_tracking_gracefully():
    raw = {
        "id": "zzz",
        "crossReferenceId": None,
        "url": "/annunci/zzz",
        "price": {},
        "vehicle": {},
        "location": {},
        "seller": {},
        "tracking": {},
    }
    mapped = map_snippet_listing(raw)

    assert mapped["price"] is None
    assert mapped["mileage_km"] is None
    assert mapped["first_registration"] is None
