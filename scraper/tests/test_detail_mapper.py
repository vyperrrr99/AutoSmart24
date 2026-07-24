from pathlib import Path

from autosmart24.scraping.next_data import extract_next_data
from autosmart24.scraping.detail_mapper import map_detail_listing

FIXTURES = Path(__file__).parent / "fixtures"


def _listing_details() -> dict:
    html = (FIXTURES / "detail_fiat_grande_panda.html").read_text(encoding="utf-8")
    data = extract_next_data(html)
    return data["props"]["pageProps"]["listingDetails"]


def test_map_detail_listing_extracts_full_fields():
    ld = _listing_details()
    mapped = map_detail_listing(ld)

    assert mapped["id"] == ld["id"]
    assert mapped["brand"] == "Fiat"
    assert mapped["model"] == "Grande Panda"
    assert mapped["price"] == 13990
    assert mapped["power_kw"] == 74
    assert mapped["power_cv"] == 101
    assert mapped["displacement_ccm"] == 1199
    assert mapped["body_type"] == "Berlina"
    assert mapped["num_seats"] == 5
    assert mapped["province"] == "TO"
    assert mapped["seller_type"] == "Dealer"
    assert mapped["source_status"] == "Active"
    assert mapped["first_registration"].isoformat() == "2026-04-01"
    assert mapped["created_at_source"].year == 2026
    assert mapped["url"].startswith("https://www.autoscout24.it/annunci/")
    assert mapped["raw_detail"] == ld


def test_map_detail_listing_handles_missing_city_gracefully():
    ld = {
        "id": "zzz",
        "identifier": {},
        "vehicle": {},
        "location": {},
        "seller": {},
        "prices": {},
        "price": {},
        "webPage": "https://www.autoscout24.it/annunci/zzz",
        "status": "Active",
        "createdTimestampWithOffset": None,
    }
    mapped = map_detail_listing(ld)

    assert mapped["city"] is None
    assert mapped["province"] is None
    assert mapped["created_at_source"] is None
