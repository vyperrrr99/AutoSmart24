from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    brand: str
    started_at: dt.datetime
    finished_at: dt.datetime | None
    status: str
    listings_seen: int
    new_listings: int
    price_changes: int
    sold_detected: int
    errors_count: int


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int | None
    brand: str | None
    level: str
    message: str
    url: str | None
    created_at: dt.datetime


class BrandStatusOut(BaseModel):
    make_id: int
    brand: str
    slug: str
    paused: bool
    year_from_years: int | None
    schedule_day_of_week: str | None
    schedule_hour: int
    schedule_minute: int
    last_run: RunOut | None


class BrandCatalogEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    make_id: int
    display_name: str
    slug: str


class AddBrandsRequest(BaseModel):
    make_ids: list[int]
    year_from_years: int | None = None
    schedule_day_of_week: str | None = None
    schedule_hour: int = 3
    schedule_minute: int = 0


class UpdateBrandRequest(BaseModel):
    year_from_years: int | None = None
    schedule_day_of_week: str | None = None
    schedule_hour: int | None = None
    schedule_minute: int | None = None


class ApplyDefaultsRequest(BaseModel):
    year_from_years: int | None = None
    schedule_day_of_week: str | None = None
    schedule_hour: int | None = None
    schedule_minute: int | None = None
