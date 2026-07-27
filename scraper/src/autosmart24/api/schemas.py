from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field


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
    phase: str | None = None
    search_finished_at: dt.datetime | None = None
    search_total: int | None = None
    detail_total: int | None = None
    detail_enriched: int = 0


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
    schedule_hour: int = Field(default=3, ge=0, le=23)
    schedule_minute: int = Field(default=0, ge=0, le=59)


class UpdateBrandRequest(BaseModel):
    year_from_years: int | None = None
    schedule_day_of_week: str | None = None
    schedule_hour: int | None = Field(default=None, ge=0, le=23)
    schedule_minute: int | None = Field(default=None, ge=0, le=59)


class ApplyDefaultsRequest(BaseModel):
    year_from_years: int | None = None
    schedule_day_of_week: str | None = None
    schedule_hour: int | None = Field(default=None, ge=0, le=23)
    schedule_minute: int | None = Field(default=None, ge=0, le=59)


class QueueCurrentOut(BaseModel):
    slug: str
    brand: str
    phase: str | None
    done: int
    total: int | None
    percent: float | None
    eta_seconds: int | None
    eta_is_fallback: bool
    started_at: dt.datetime


class QueuePendingOut(BaseModel):
    slug: str
    brand: str
    position: int
    eta_seconds: int | None


class QueueOut(BaseModel):
    halted: bool
    halted_reason: str | None
    halted_at: dt.datetime | None
    current: QueueCurrentOut | None
    pending: list[QueuePendingOut]
    total_eta_seconds: int | None


class RunMetricsOut(BaseModel):
    run_id: int
    started_at: dt.datetime
    status: str
    search_seconds: int | None
    search_items: int | None
    search_rate_per_min: float | None
    detail_seconds: int | None
    detail_items: int | None
    detail_rate_per_min: float | None
