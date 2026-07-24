from pathlib import Path

import pytest

from autosmart24.scraping.next_data import NextDataNotFoundError, extract_next_data

FIXTURES = Path(__file__).parent / "fixtures"


def test_extract_next_data_from_search_page():
    html = (FIXTURES / "search_fiat_page1.html").read_text(encoding="utf-8")
    data = extract_next_data(html)
    page_props = data["props"]["pageProps"]
    assert page_props["numberOfResults"] > 0
    assert isinstance(page_props["listings"], list)
    assert len(page_props["listings"]) == 20


def test_extract_next_data_from_detail_page():
    html = (FIXTURES / "detail_fiat_grande_panda.html").read_text(encoding="utf-8")
    data = extract_next_data(html)
    listing_details = data["props"]["pageProps"]["listingDetails"]
    assert listing_details["vehicle"]["make"] == "Fiat"


def test_extract_next_data_raises_when_missing():
    with pytest.raises(NextDataNotFoundError):
        extract_next_data("<html><body>no data here</body></html>")
