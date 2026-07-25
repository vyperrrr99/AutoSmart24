from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    pass


JSONVariant = JSONB().with_variant(JSON(), "sqlite")


class Listing(Base):
    __tablename__ = "listings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    cross_reference_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    brand: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model_group: Mapped[str | None] = mapped_column(String(128), nullable=True)
    variant: Mapped[str | None] = mapped_column(String(128), nullable=True)
    motor_type_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    version_input: Mapped[str | None] = mapped_column(String(256), nullable=True)
    transmission: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fuel: Mapped[str | None] = mapped_column(String(64), nullable=True)

    first_registration: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    mileage_km: Mapped[int | None] = mapped_column(Integer, nullable=True)
    power_kw: Mapped[int | None] = mapped_column(Integer, nullable=True)
    power_cv: Mapped[int | None] = mapped_column(Integer, nullable=True)
    displacement_ccm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    body_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    body_color: Mapped[str | None] = mapped_column(String(64), nullable=True)
    num_seats: Mapped[int | None] = mapped_column(Integer, nullable=True)
    num_doors: Mapped[int | None] = mapped_column(Integer, nullable=True)
    num_previous_owners: Mapped[int | None] = mapped_column(Integer, nullable=True)

    seller_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    seller_company_name: Mapped[str | None] = mapped_column(String(256), nullable=True)

    city: Mapped[str | None] = mapped_column(String(256), nullable=True)
    province: Mapped[str | None] = mapped_column(String(64), nullable=True)
    zip_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)

    price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vat_exposed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    price_evaluation_category: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_evaluation_median: Mapped[int | None] = mapped_column(Integer, nullable=True)

    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    created_at_source: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    first_seen_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)
    last_checked_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    sold_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    detail_scraped: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    raw_snippet: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    raw_detail: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)


class PriceHistory(Base):
    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_id: Mapped[str] = mapped_column(String(36), ForeignKey("listings.id"), nullable=False, index=True)
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    recorded_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    brand: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")

    listings_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_listings: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    price_changes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sold_detected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ScrapeEvent(Base):
    __tablename__ = "scrape_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("scrape_runs.id"), nullable=True, index=True)
    brand: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(String(2048), nullable=False)
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)


class BrandCatalog(Base):
    __tablename__ = "brand_catalog"

    make_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    synced_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)


class TrackedBrand(Base):
    __tablename__ = "tracked_brands"

    make_id: Mapped[int] = mapped_column(Integer, ForeignKey("brand_catalog.make_id"), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    year_from_years: Mapped[int | None] = mapped_column(Integer, nullable=True)
    schedule_day_of_week: Mapped[str | None] = mapped_column(String(3), nullable=True)
    schedule_hour: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    schedule_minute: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)
