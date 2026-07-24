from __future__ import annotations

from urllib.parse import urlencode

from autosmart24.config import BASE_URL

BASE_QUERY_PARAMS = {
    "cy": "I",
    "atype": "C",
    "ustate": "N,U",
    "sort": "standard",
    "desc": "0",
    "powertype": "kw",
}


def build_search_url(
    brand_slug: str,
    page: int,
    make_id: int,
    model_id: int | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
) -> str:
    params = dict(BASE_QUERY_PARAMS)
    params["page"] = str(page)
    if model_id is not None:
        params["mmmv"] = f"{make_id}|{model_id}||"
    if year_from is not None:
        params["fregfrom"] = str(year_from)
    if year_to is not None:
        params["fregto"] = str(year_to)

    return f"{BASE_URL}/lst/{brand_slug}?{urlencode(params)}"
