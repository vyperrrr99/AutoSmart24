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


def fetch_detail(client: RateLimitedClient, url: str) -> DetailResult:
    try:
        response = client.get(url)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (404, 410):
            return DetailResult(sold=True)
        raise

    data = extract_next_data(response.text)
    listing_details = data["props"]["pageProps"]["listingDetails"]
    mapped = map_detail_listing(listing_details)

    if mapped["source_status"] != "Active":
        return DetailResult(sold=True, data=mapped)

    return DetailResult(sold=False, data=mapped)
