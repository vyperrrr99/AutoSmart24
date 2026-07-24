from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BrandConfig:
    slug: str
    make_id: int
    display_name: str


MVP_BRANDS: list[BrandConfig] = [
    BrandConfig(slug="fiat", make_id=28, display_name="Fiat"),
    BrandConfig(slug="volkswagen", make_id=74, display_name="Volkswagen"),
    BrandConfig(slug="bmw", make_id=13, display_name="BMW"),
    BrandConfig(slug="audi", make_id=9, display_name="Audi"),
    BrandConfig(slug="mercedes-benz", make_id=47, display_name="Mercedes-Benz"),
]

BASE_URL = "https://www.autoscout24.it"
MAX_RESULTS_PER_QUERY = 4000  # 200 pages x ~20 results/page — autoscout24 pagination cap
RESULTS_PER_PAGE = 20
