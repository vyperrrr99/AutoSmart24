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

# A listing at or below this mileage is not a used car. Two populations sit
# here, measured on 296,685 live listings on 31/07/2026: 11,041 never
# registered (49,543 EUR average) and 20,895 registered by a dealer at nine
# months old with zero mileage (33,557 EUR) -- against 23,408 EUR and 81,217 km
# for genuine used cars. Left in, they pull every price statistic upward, and
# unevenly across makes, because premium brands carry more of them.
#
# Not zero: a car delivered with 90 km on the clock is the same thing as one
# with 0, and only three listings older than eighteen months declare no
# mileage at all, so treating a missing value as new costs almost nothing.
NEW_CAR_MAX_KM = 100
