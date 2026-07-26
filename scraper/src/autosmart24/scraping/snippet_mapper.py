from __future__ import annotations

import datetime as dt

from autosmart24.config import BASE_URL


def _parse_first_registration(value: str | None) -> dt.date | None:
    if not value:
        return None
    parts = value.split("-")
    if len(parts) == 2:
        month_str, year_str = parts
        return dt.date(int(year_str), int(month_str), 1)
    if len(parts) == 1 and parts[0].isdigit():
        return dt.date(int(parts[0]), 1, 1)
    return None


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    return int(digits) if digits else None


def _absolute_url(url: str) -> str:
    return f"{BASE_URL}{url}" if url.startswith("/") else url


def map_snippet_listing(raw: dict) -> dict:
    vehicle = raw.get("vehicle") or {}
    price = raw.get("price") or {}
    location = raw.get("location") or {}
    seller = raw.get("seller") or {}
    tracking = raw.get("tracking") or {}

    return {
        "id": raw["id"],
        "cross_reference_id": raw.get("crossReferenceId"),
        "url": _absolute_url(raw["url"]),
        "brand": vehicle.get("make"),
        "model": vehicle.get("model"),
        "model_group": vehicle.get("modelGroup"),
        "variant": vehicle.get("variant"),
        "motor_type_name": vehicle.get("motorTypeName"),
        "version_input": vehicle.get("modelVersionInput"),
        "transmission": vehicle.get("transmission"),
        "fuel": vehicle.get("fuel"),
        "first_registration": _parse_first_registration(tracking.get("firstRegistration")),
        "mileage_km": _parse_int(tracking.get("mileage")),
        "seller_type": seller.get("type"),
        "seller_company_name": seller.get("companyName"),
        "city": location.get("city"),
        "zip_code": location.get("zip"),
        "price": price.get("priceRaw"),
    }
