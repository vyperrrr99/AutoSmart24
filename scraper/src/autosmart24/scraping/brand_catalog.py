from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from autosmart24.scraping.crawler import fetch_page_data
from autosmart24.scraping.http_client import RateLimitedClient
from autosmart24.scraping.search_query import build_search_url

# Any brand's search page exposes the site's full make catalog in
# taxonomy.makes, not just that brand's own models -- this is used purely as
# a stable, already-proven-working anchor request, not because the catalog
# is Fiat-specific.
ANCHOR_BRAND_SLUG = "fiat"
ANCHOR_MAKE_ID = 28

_SLUG_SEPARATOR_RE = re.compile(r"[^a-z0-9]+")


@dataclass
class CatalogEntry:
    make_id: int
    display_name: str
    slug: str


def _strip_diacritics(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def derive_slug(display_name: str) -> str:
    # Without transliteration, accented brand names like "Bolloré" would have
    # their diacritic silently dropped (-> "bollor"), producing a truncated
    # slug that 404s and looks indistinguishable from a brand with no listings.
    normalized = _strip_diacritics(display_name)
    slug = _SLUG_SEPARATOR_RE.sub("-", normalized.strip().lower())
    return slug.strip("-")


def fetch_brand_catalog(client: RateLimitedClient) -> list[CatalogEntry]:
    url = build_search_url(ANCHOR_BRAND_SLUG, page=1, make_id=ANCHOR_MAKE_ID)
    page_props = fetch_page_data(client, url)
    makes = page_props["taxonomy"]["makes"]
    return [
        CatalogEntry(make_id=int(entry["value"]), display_name=entry["label"], slug=derive_slug(entry["label"]))
        for entry in makes.values()
    ]
