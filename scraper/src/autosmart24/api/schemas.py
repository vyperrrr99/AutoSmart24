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
    brand: str
    slug: str
    paused: bool
    last_run: RunOut | None
