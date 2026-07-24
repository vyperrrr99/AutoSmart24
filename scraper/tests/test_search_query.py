from urllib.parse import parse_qs, urlparse

from autosmart24.scraping.search_query import build_search_url


def test_build_search_url_brand_only():
    url = build_search_url("fiat", page=1, make_id=28)
    parsed = urlparse(url)
    assert parsed.path == "/lst/fiat"
    query = parse_qs(parsed.query)
    assert query["page"] == ["1"]
    assert query["cy"] == ["I"]
    assert "mmmv" not in query
    assert "fregfrom" not in query


def test_build_search_url_with_model_filter():
    url = build_search_url("fiat", page=3, make_id=28, model_id=1746)
    query = parse_qs(urlparse(url).query)
    assert query["mmmv"] == ["28|1746||"]
    assert query["page"] == ["3"]


def test_build_search_url_with_year_range():
    url = build_search_url("fiat", page=1, make_id=28, model_id=1746, year_from=2020, year_to=2022)
    query = parse_qs(urlparse(url).query)
    assert query["fregfrom"] == ["2020"]
    assert query["fregto"] == ["2022"]
