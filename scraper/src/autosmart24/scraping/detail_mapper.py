from __future__ import annotations

import datetime as dt


def _parse_city(city: str | None) -> tuple[str | None, str | None]:
    if not city:
        return None, None
    parts = [p.strip() for p in city.split(" - ")]
    province = parts[-1] if len(parts) == 3 else None
    return city, province


def _parse_created_at(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _parse_weight_kg(value: str | None) -> int | None:
    if not value:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    return int(digits) if digits else None


def extract_dealer(ld: dict) -> dict | None:
    seller = ld.get("seller") or {}
    if not seller.get("isDealer") or seller.get("id") is None:
        return None
    ratings = ld.get("ratings") or {}
    return {
        "id": seller["id"],
        "company_name": seller.get("companyName"),
        "ratings_stars": ratings.get("ratingsStars"),
        "ratings_count": ratings.get("ratingsCount"),
        "recommend_percentage": ratings.get("recommendPercentage"),
    }


def map_detail_listing(ld: dict) -> dict:
    vehicle = ld.get("vehicle") or {}
    location = ld.get("location") or {}
    seller = ld.get("seller") or {}
    identifier = ld.get("identifier") or {}
    prices_public = (ld.get("prices") or {}).get("public") or {}
    top_price = ld.get("price") or {}
    dpv_statistics = ld.get("dpvStatistics") or {}

    city, province = _parse_city(location.get("city"))
    first_registration_raw = vehicle.get("firstRegistrationDateRaw")

    return {
        "id": ld["id"],
        "cross_reference_id": identifier.get("crossReferenceId"),
        "brand": vehicle.get("make"),
        "model": vehicle.get("model"),
        "model_group": vehicle.get("modelGroup"),
        "variant": vehicle.get("variant"),
        "motor_type_name": vehicle.get("motorTypeName"),
        "version_input": vehicle.get("modelVersionInput"),
        "transmission": vehicle.get("transmissionType"),
        "fuel": (vehicle.get("fuelCategory") or {}).get("formatted"),
        "first_registration": dt.date.fromisoformat(first_registration_raw) if first_registration_raw else None,
        "mileage_km": vehicle.get("mileageInKmRaw"),
        "power_kw": vehicle.get("rawPowerInKw"),
        "power_cv": vehicle.get("rawPowerInHp"),
        "displacement_ccm": vehicle.get("rawDisplacementInCCM"),
        "body_type": vehicle.get("bodyType"),
        "body_color": vehicle.get("bodyColorRaw") or vehicle.get("bodyColor"),
        "num_seats": vehicle.get("numberOfSeats"),
        "num_doors": vehicle.get("numberOfDoors"),
        "num_previous_owners": vehicle.get("noOfPreviousOwners"),
        "had_accident": vehicle.get("hadAccident"),
        "has_full_service_history": vehicle.get("hasFullServiceHistory"),
        "gears": vehicle.get("gears"),
        "drive_train": vehicle.get("driveTrain"),
        "cylinders": vehicle.get("cylinders"),
        "weight_kg": _parse_weight_kg(vehicle.get("weight")),
        "co2_emissions_g_km": (vehicle.get("co2emissionInGramPerKmWithFallback") or {}).get("raw"),
        "fuel_consumption_combined": (vehicle.get("fuelConsumptionCombined") or {}).get("raw"),
        "fuel_consumption_urban": (vehicle.get("fuelConsumptionUrban") or {}).get("raw"),
        "fuel_consumption_extra_urban": (vehicle.get("fuelConsumptionExtraUrban") or {}).get("raw"),
        "emission_class": (vehicle.get("environmentEuDirective") or {}).get("formatted"),
        "upholstery": vehicle.get("upholstery"),
        "upholstery_color": vehicle.get("upholsteryColor"),
        "new_driver_suitable": vehicle.get("newDriverSuitable"),
        "seller_type": seller.get("type"),
        "seller_company_name": seller.get("companyName"),
        "city": city,
        "province": province,
        "zip_code": location.get("zip"),
        "latitude": location.get("latitude"),
        "longitude": location.get("longitude"),
        "price": prices_public.get("priceRaw"),
        "vat_exposed": prices_public.get("taxDeductible"),
        "price_evaluation_category": prices_public.get("category"),
        "price_evaluation_median": prices_public.get("median"),
        "is_conditional_price": top_price.get("isConditionalPrice"),
        "interaction_count": dpv_statistics.get("interaction"),
        "favorites_count": dpv_statistics.get("favorites"),
        "url": ld.get("webPage"),
        "source_status": ld.get("status"),
        "created_at_source": _parse_created_at(ld.get("createdTimestampWithOffset")),
        "dealer": extract_dealer(ld),
        "raw_detail": ld,
    }
