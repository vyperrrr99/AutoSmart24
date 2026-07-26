# Structured Detail-Page Fields, Dealers Table, and Raw-JSON Retirement — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract a set of currently-discarded detail-page fields (accident/service history, technical specs, emissions/consumption, popularity, dealer ratings) into structured columns, add a normalized `dealers` table so dealer statistics are not repeated per listing, backfill every already-enriched listing from its already-stored raw JSON, verify the backfill, and only then drop the `raw_detail`/`raw_snippet` JSON columns that today account for 87% of database size.

**Architecture:** All new fields are extracted by the single existing `map_detail_listing` function (detail_mapper.py) — the backfill script reuses this exact function against already-stored `raw_detail` JSON rather than re-implementing parsing a second time, so there is one source of truth for JSON→field mapping shared between live scraping and the one-time backfill. Dealer upsert logic is similarly extracted into one shared helper (`db/dealers.py`) used by both the live scrape path (`run_manager.py`) and the backfill script. The migration is deliberately split into two migrations separated by a mandatory backfill-and-verify step: schema-add (Task 1) → code wiring (Tasks 2-3) → one-time backfill from already-stored JSON (Task 4) → live verification against production (Task 5) → schema-drop (Task 6) → final live deployment (Task 7). This order is not interchangeable — it is the explicit, binding requirement from the design spec.

**Tech Stack:** Same as the existing project — Python 3.12, SQLAlchemy 2.0, Alembic, pytest. No new dependencies.

## Global Constraints

- **Binding migration order (from the design spec, non-negotiable):** new columns must be added and backfilled from the JSON *already stored in the database* — no new HTTP requests — and the backfill must be verified complete before `raw_detail`/`raw_snippet` are ever dropped. Dropping first and backfilling after is not an option: it would make the already-scraped, already-expired listings' data unrecoverable.
- Equipment (`vehicle.equipment`, 146 items across 4 categories), the free-text seller description, photos, financing/leasing widgets, and ad-tracking parameters are explicitly **out of scope** — never extracted, never stored.
- Price history is explicitly **out of scope** for this plan — it is not present in the unauthenticated JSON this scraper collects, and a separate authenticated one-time tool is a future, unplanned effort.
- Dealer rows are created only for `seller.isDealer == true` with a non-null `seller.id` — private sellers keep using the existing `seller_type`/`seller_company_name` columns on `Listing`, unchanged.
- `ratings_stars` must come from `ratings.ratingsStars` (the rounded value actually displayed as stars, e.g. `4`, `4.5`) — **not** `ratings.ratingsAverage` (a more precise, comma-decimal string, not requested).
- `emission_class` uses `vehicle.environmentEuDirective.formatted` (e.g. `"Euro 6d"`), matching the existing convention already used for `fuel` (`vehicle.fuelCategory.formatted`) — not the machine-code `.label` field.
- Base URL: `https://www.autoscout24.it` (unaffected by this plan).

---

## Task 1: `Dealer` model, new `Listing` columns, and additive migration

**Files:**
- Modify: `scraper/src/autosmart24/db/models.py`
- Create: `scraper/migrations/versions/0005_detail_fields_and_dealers.py`
- Create: `scraper/tests/test_dealer_and_detail_fields_models.py`

**Interfaces:**
- Produces: `autosmart24.db.models.Dealer` (columns: `id: int` PK, `company_name: str | None`, `ratings_stars: float | None`, `ratings_count: int | None`, `recommend_percentage: int | None`, `synced_at: datetime`); `Listing` gains 17 new nullable columns (`had_accident`, `has_full_service_history`, `gears`, `drive_train`, `cylinders`, `weight_kg`, `co2_emissions_g_km`, `fuel_consumption_combined`, `fuel_consumption_urban`, `fuel_consumption_extra_urban`, `emission_class`, `upholstery`, `upholstery_color`, `is_conditional_price`, `interaction_count`, `favorites_count`, `new_driver_suitable`, `dealer_id: int | None` FK to `dealers.id`) — consumed by Tasks 2-6.

- [ ] **Step 1: Read the current file**

Read `scraper/src/autosmart24/db/models.py` in full — confirm the current `Listing` model ends at `raw_detail` and that `BrandCatalog`/`TrackedBrand` are the last two classes, unchanged since the brand-management-ui plan.

- [ ] **Step 2: Write the failing tests**

`scraper/tests/test_dealer_and_detail_fields_models.py`:

```python
import datetime as dt

import pytest

from autosmart24.db.models import Dealer, Listing


def _base_listing(listing_id: str = "abc-123") -> Listing:
    now = dt.datetime.utcnow()
    return Listing(
        id=listing_id, brand="Fiat", url="https://www.autoscout24.it/annunci/abc-123",
        first_seen_at=now, last_seen_at=now, last_checked_at=now, status="active",
        detail_scraped=True,
    )


def test_dealer_round_trips(db_session):
    db_session.add(
        Dealer(
            id=46936034, company_name="Puntocar di Tarantino Andrea - Bricherasio",
            ratings_stars=5, ratings_count=25, recommend_percentage=92,
            synced_at=dt.datetime.utcnow(),
        )
    )
    db_session.commit()

    row = db_session.get(Dealer, 46936034)
    assert row is not None
    assert row.company_name == "Puntocar di Tarantino Andrea - Bricherasio"
    assert row.ratings_stars == 5
    assert row.ratings_count == 25
    assert row.recommend_percentage == 92


def test_listing_new_detail_fields_round_trip(db_session):
    db_session.add(
        Dealer(id=1, company_name="Test Dealer", ratings_stars=4.5, ratings_count=10,
               recommend_percentage=80, synced_at=dt.datetime.utcnow())
    )
    listing = _base_listing()
    listing.had_accident = False
    listing.has_full_service_history = True
    listing.gears = 6
    listing.drive_train = "Anteriore"
    listing.cylinders = 3
    listing.weight_kg = 1159
    listing.co2_emissions_g_km = 109.0
    listing.fuel_consumption_combined = 5.4
    listing.fuel_consumption_urban = 6.1
    listing.fuel_consumption_extra_urban = 4.8
    listing.emission_class = "Euro 6d"
    listing.upholstery = "Altro"
    listing.upholstery_color = "Nero"
    listing.is_conditional_price = True
    listing.interaction_count = 10670
    listing.favorites_count = 193
    listing.new_driver_suitable = True
    listing.dealer_id = 1
    db_session.add(listing)
    db_session.commit()

    row = db_session.get(Listing, "abc-123")
    assert row.had_accident is False
    assert row.has_full_service_history is True
    assert row.gears == 6
    assert row.drive_train == "Anteriore"
    assert row.cylinders == 3
    assert row.weight_kg == 1159
    assert row.co2_emissions_g_km == 109.0
    assert row.fuel_consumption_combined == 5.4
    assert row.emission_class == "Euro 6d"
    assert row.upholstery == "Altro"
    assert row.upholstery_color == "Nero"
    assert row.is_conditional_price is True
    assert row.interaction_count == 10670
    assert row.favorites_count == 193
    assert row.new_driver_suitable is True
    assert row.dealer_id == 1


def test_listing_new_fields_default_to_null(db_session):
    db_session.add(_base_listing("def-456"))
    db_session.commit()

    row = db_session.get(Listing, "def-456")
    assert row.had_accident is None
    assert row.gears is None
    assert row.dealer_id is None
```

- [ ] **Step 3: Run to confirm failure**

Run: `cd scraper && pytest tests/test_dealer_and_detail_fields_models.py -v`
Expected: FAIL with `ImportError: cannot import name 'Dealer'`

- [ ] **Step 4: Add the model and columns**

In `scraper/src/autosmart24/db/models.py`, add a new `Dealer` class **immediately before** the `Listing` class (matching this codebase's existing convention of defining a referenced table before the class whose FK points at it):

```python
class Dealer(Base):
    __tablename__ = "dealers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    ratings_stars: Mapped[float | None] = mapped_column(Float, nullable=True)
    ratings_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recommend_percentage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    synced_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)
```

Then add these 17 columns to `Listing`, immediately after the existing `num_previous_owners` column and before the `seller_type`/`seller_company_name` block:

```python
    had_accident: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    has_full_service_history: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    gears: Mapped[int | None] = mapped_column(Integer, nullable=True)
    drive_train: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cylinders: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weight_kg: Mapped[int | None] = mapped_column(Integer, nullable=True)
    co2_emissions_g_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    fuel_consumption_combined: Mapped[float | None] = mapped_column(Float, nullable=True)
    fuel_consumption_urban: Mapped[float | None] = mapped_column(Float, nullable=True)
    fuel_consumption_extra_urban: Mapped[float | None] = mapped_column(Float, nullable=True)
    emission_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    upholstery: Mapped[str | None] = mapped_column(String(64), nullable=True)
    upholstery_color: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_conditional_price: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    interaction_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    favorites_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_driver_suitable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    dealer_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("dealers.id"), nullable=True)
```

- [ ] **Step 5: Run to confirm pass**

Run: `cd scraper && pytest tests/test_dealer_and_detail_fields_models.py -v`
Expected: `3 passed`

- [ ] **Step 6: Write the migration**

Read `scraper/migrations/versions/0004_brand_catalog_and_tracked_brands.py` first — its actual shipped `revision` value is `"0004_brand_tables"` (shortened from the filename during Task 1 of the prior plan, because Postgres's `alembic_version.version_num` column is `VARCHAR(32)`). Your `down_revision` must reference that exact string.

Create `scraper/migrations/versions/0005_detail_fields_and_dealers.py`:

```python
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_detail_fields_dealers"
down_revision = "0004_brand_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dealers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_name", sa.String(256), nullable=True),
        sa.Column("ratings_stars", sa.Float(), nullable=True),
        sa.Column("ratings_count", sa.Integer(), nullable=True),
        sa.Column("recommend_percentage", sa.Integer(), nullable=True),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
    )

    op.add_column("listings", sa.Column("had_accident", sa.Boolean(), nullable=True))
    op.add_column("listings", sa.Column("has_full_service_history", sa.Boolean(), nullable=True))
    op.add_column("listings", sa.Column("gears", sa.Integer(), nullable=True))
    op.add_column("listings", sa.Column("drive_train", sa.String(64), nullable=True))
    op.add_column("listings", sa.Column("cylinders", sa.Integer(), nullable=True))
    op.add_column("listings", sa.Column("weight_kg", sa.Integer(), nullable=True))
    op.add_column("listings", sa.Column("co2_emissions_g_km", sa.Float(), nullable=True))
    op.add_column("listings", sa.Column("fuel_consumption_combined", sa.Float(), nullable=True))
    op.add_column("listings", sa.Column("fuel_consumption_urban", sa.Float(), nullable=True))
    op.add_column("listings", sa.Column("fuel_consumption_extra_urban", sa.Float(), nullable=True))
    op.add_column("listings", sa.Column("emission_class", sa.String(32), nullable=True))
    op.add_column("listings", sa.Column("upholstery", sa.String(64), nullable=True))
    op.add_column("listings", sa.Column("upholstery_color", sa.String(64), nullable=True))
    op.add_column("listings", sa.Column("is_conditional_price", sa.Boolean(), nullable=True))
    op.add_column("listings", sa.Column("interaction_count", sa.Integer(), nullable=True))
    op.add_column("listings", sa.Column("favorites_count", sa.Integer(), nullable=True))
    op.add_column("listings", sa.Column("new_driver_suitable", sa.Boolean(), nullable=True))
    op.add_column(
        "listings",
        sa.Column("dealer_id", sa.Integer(), sa.ForeignKey("dealers.id"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("listings", "dealer_id")
    op.drop_column("listings", "new_driver_suitable")
    op.drop_column("listings", "favorites_count")
    op.drop_column("listings", "interaction_count")
    op.drop_column("listings", "is_conditional_price")
    op.drop_column("listings", "upholstery_color")
    op.drop_column("listings", "upholstery")
    op.drop_column("listings", "emission_class")
    op.drop_column("listings", "fuel_consumption_extra_urban")
    op.drop_column("listings", "fuel_consumption_urban")
    op.drop_column("listings", "fuel_consumption_combined")
    op.drop_column("listings", "co2_emissions_g_km")
    op.drop_column("listings", "weight_kg")
    op.drop_column("listings", "cylinders")
    op.drop_column("listings", "drive_train")
    op.drop_column("listings", "gears")
    op.drop_column("listings", "has_full_service_history")
    op.drop_column("listings", "had_accident")
    op.drop_table("dealers")
```

- [ ] **Step 7: Verify against the real running Postgres**

The `postgres` container holds real, currently-growing scrape data (Fiat/Audi/Volkswagen/BMW/Renault). This migration only adds nullable columns and one new table — it cannot lose data, but confirm this directly rather than assuming.

Run from `scraper/`: `DATABASE_URL=postgresql+psycopg://autosmart24:autosmart24@localhost:5434/autosmart24 python -m alembic upgrade head`
Expected: reaches `0005_detail_fields_dealers` with no errors.

Confirm: `docker exec autoscout-postgres-1 psql -U autosmart24 -d autosmart24 -c "SELECT count(*) FROM listings;"` shows the same row count as before the migration, and `docker exec autoscout-postgres-1 psql -U autosmart24 -d autosmart24 -c "\d listings"` shows all 17 new columns plus `dealer_id`.

- [ ] **Step 8: Commit**

```bash
git add scraper/src/autosmart24/db/models.py scraper/migrations/versions/0005_detail_fields_and_dealers.py scraper/tests/test_dealer_and_detail_fields_models.py
git commit -m "Add Dealer model and 17 new structured Listing columns (additive migration)"
```

---

## Task 2: Extract new fields and dealer info in `detail_mapper.py`

**Files:**
- Modify: `scraper/src/autosmart24/scraping/detail_mapper.py`
- Modify: `scraper/tests/test_detail_mapper.py`

**Interfaces:**
- Consumes: nothing new (pure JSON parsing).
- Produces: `map_detail_listing(ld)`'s returned dict gains 17 new keys (matching Task 1's column names exactly) plus a `"dealer"` key (`dict | None` — `{"id", "company_name", "ratings_stars", "ratings_count", "recommend_percentage"}` or `None` for non-dealer/private sellers); new public function `extract_dealer(ld: dict) -> dict | None` — consumed by Task 3 (`run_manager.py`) and Task 4 (backfill script).

- [ ] **Step 1: Read the current files**

Read `scraper/src/autosmart24/scraping/detail_mapper.py` and `scraper/tests/test_detail_mapper.py` in full.

- [ ] **Step 2: Write the failing tests**

Add these to `scraper/tests/test_detail_mapper.py` (keep the 3 existing tests unchanged):

```python
def test_map_detail_listing_extracts_new_structured_fields():
    ld = _listing_details()
    mapped = map_detail_listing(ld)

    assert mapped["had_accident"] is False
    assert mapped["has_full_service_history"] is False
    assert mapped["gears"] == 6
    assert mapped["drive_train"] == "Anteriore"
    assert mapped["cylinders"] == 3
    assert mapped["weight_kg"] == 1159
    assert mapped["co2_emissions_g_km"] is None
    assert mapped["fuel_consumption_combined"] is None
    assert mapped["fuel_consumption_urban"] is None
    assert mapped["fuel_consumption_extra_urban"] is None
    assert mapped["emission_class"] == "Euro 6d"
    assert mapped["upholstery"] == "Altro"
    assert mapped["upholstery_color"] is None
    assert mapped["is_conditional_price"] is True
    assert mapped["interaction_count"] == 10670
    assert mapped["favorites_count"] == 193
    assert mapped["new_driver_suitable"] is True


def test_map_detail_listing_extracts_dealer_info_for_a_dealer_seller():
    ld = _listing_details()
    mapped = map_detail_listing(ld)

    assert mapped["dealer"] == {
        "id": 46936034,
        "company_name": "Puntocar di Tarantino Andrea - Bricherasio",
        "ratings_stars": 5,
        "ratings_count": 25,
        "recommend_percentage": 92,
    }


def test_map_detail_listing_dealer_is_none_for_missing_seller_info():
    ld = {
        "id": "zzz",
        "identifier": {},
        "vehicle": {},
        "location": {},
        "seller": {},
        "prices": {},
        "price": {},
        "webPage": "https://www.autoscout24.it/annunci/zzz",
        "status": "Active",
        "createdTimestampWithOffset": None,
    }
    mapped = map_detail_listing(ld)

    assert mapped["dealer"] is None
    assert mapped["had_accident"] is None
    assert mapped["gears"] is None
    assert mapped["is_conditional_price"] is None


def test_extract_dealer_returns_none_for_private_seller():
    ld = {"seller": {"type": "Private", "isDealer": False}}
    assert extract_dealer(ld) is None


def test_extract_dealer_returns_none_when_dealer_has_no_id():
    ld = {"seller": {"isDealer": True}}
    assert extract_dealer(ld) is None


def test_extract_dealer_extracts_ratings_for_a_real_dealer():
    ld = {
        "seller": {"id": 999, "isDealer": True, "companyName": "Auto Test Srl"},
        "ratings": {"ratingsStars": 4.5, "ratingsCount": 30, "recommendPercentage": 88},
    }
    assert extract_dealer(ld) == {
        "id": 999,
        "company_name": "Auto Test Srl",
        "ratings_stars": 4.5,
        "ratings_count": 30,
        "recommend_percentage": 88,
    }


def test_parse_weight_kg_handles_thousands_separator_and_none():
    assert _parse_weight_kg("1.159 kg") == 1159
    assert _parse_weight_kg("800 kg") == 800
    assert _parse_weight_kg(None) is None
    assert _parse_weight_kg("") is None
```

Update the import line at the top of the test file to also import the new names:

```python
from autosmart24.scraping.detail_mapper import extract_dealer, map_detail_listing, _parse_weight_kg
```

Also update the existing `test_map_detail_listing_handles_missing_city_gracefully` test to additionally assert `mapped["dealer"] is None` (its `ld` already has `"seller": {}`, which already satisfies this — just add the one assertion line).

- [ ] **Step 3: Run to confirm failure**

Run: `cd scraper && pytest tests/test_detail_mapper.py -v`
Expected: FAIL — `ImportError` (`extract_dealer`, `_parse_weight_kg` don't exist yet) or `KeyError` once import is fixed manually to isolate.

- [ ] **Step 4: Update detail_mapper.py**

Full replacement of `scraper/src/autosmart24/scraping/detail_mapper.py`:

```python
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
```

Note `"raw_detail": ld` is deliberately still present here — Task 6 removes it, not this task. Removing it now would break every currently-passing test and the live scraper before the backfill (Task 4) has run.

- [ ] **Step 5: Run to confirm pass**

Run: `cd scraper && pytest tests/test_detail_mapper.py -v`
Expected: `13 passed` (3 pre-existing + 10 new).

- [ ] **Step 6: Commit**

```bash
git add scraper/src/autosmart24/scraping/detail_mapper.py scraper/tests/test_detail_mapper.py
git commit -m "Extract accident/service/technical/emissions/popularity/dealer fields from detail JSON"
```

---

## Task 3: Wire new fields and dealer upsert into `run_manager.py`

**Files:**
- Create: `scraper/src/autosmart24/db/dealers.py`
- Create: `scraper/tests/test_dealers.py`
- Modify: `scraper/src/autosmart24/run_manager.py`
- Modify: `scraper/tests/test_run_manager.py`

**Interfaces:**
- Consumes: `extract_dealer` (Task 2, indirectly via `map_detail_listing`'s `"dealer"` key), `Dealer` model (Task 1).
- Produces: `autosmart24.db.dealers.upsert_dealer(session, dealer_info: dict | None, now: datetime) -> int | None` — consumed by `run_manager.py` (this task) and the backfill script (Task 4).

- [ ] **Step 1: Read the current files**

Read `scraper/src/autosmart24/run_manager.py` and `scraper/tests/test_run_manager.py` in full.

**Important cross-cutting fact established by grepping the current test file:** `_fake_detail_data` (the shared helper used by most `process_detail_backlog`-exercising tests via `_noop_fetch_detail`) and one standalone inline dict inside `test_run_brand_sweep_enriches_pending_detail_backlog` are the **only two** literal "detail data" dicts that flow through `process_detail_backlog`'s field-assignment code — both must gain the 17 new keys (with `None` values) or every test using them will fail with `KeyError` once Step 3 below adds direct-index reads for the new fields. The two other `DetailResult(sold=False, data={"source_status": "Active"})` literals (in `test_run_brand_sweep_keeps_active_when_detail_still_active` and `test_run_brand_sweep_errors_count_reflects_anomalies`) are for the *missing-listings* path in `run_brand_sweep`, which never reads any field off `result.data` beyond checking `result.sold` — leave those two untouched.

- [ ] **Step 2: Write the failing tests for `upsert_dealer`**

`scraper/tests/test_dealers.py`:

```python
import datetime as dt

from autosmart24.db.dealers import upsert_dealer
from autosmart24.db.models import Dealer


def test_upsert_dealer_returns_none_for_none_input(db_session):
    assert upsert_dealer(db_session, None, dt.datetime.utcnow()) is None


def test_upsert_dealer_creates_a_new_row(db_session):
    now = dt.datetime.utcnow()
    dealer_id = upsert_dealer(
        db_session,
        {"id": 42, "company_name": "Auto Test Srl", "ratings_stars": 4.5, "ratings_count": 10, "recommend_percentage": 80},
        now,
    )
    db_session.commit()

    assert dealer_id == 42
    row = db_session.get(Dealer, 42)
    assert row.company_name == "Auto Test Srl"
    assert row.ratings_stars == 4.5
    assert row.synced_at == now


def test_upsert_dealer_updates_an_existing_row_not_duplicate(db_session):
    now1 = dt.datetime.utcnow()
    upsert_dealer(db_session, {"id": 42, "company_name": "Old Name", "ratings_stars": 4.0, "ratings_count": 5, "recommend_percentage": 70}, now1)
    db_session.commit()

    now2 = now1 + dt.timedelta(days=1)
    upsert_dealer(db_session, {"id": 42, "company_name": "New Name", "ratings_stars": 4.8, "ratings_count": 12, "recommend_percentage": 90}, now2)
    db_session.commit()

    rows = db_session.query(Dealer).filter_by(id=42).all()
    assert len(rows) == 1
    assert rows[0].company_name == "New Name"
    assert rows[0].ratings_count == 12
    assert rows[0].synced_at == now2
```

- [ ] **Step 3: Run to confirm failure**

Run: `cd scraper && pytest tests/test_dealers.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autosmart24.db.dealers'`

- [ ] **Step 4: Implement `db/dealers.py`**

```python
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from autosmart24.db.models import Dealer


def upsert_dealer(session: Session, dealer_info: dict | None, now: dt.datetime) -> int | None:
    if dealer_info is None:
        return None
    dealer = session.get(Dealer, dealer_info["id"])
    if dealer is None:
        dealer = Dealer(id=dealer_info["id"], synced_at=now)
        session.add(dealer)
    dealer.company_name = dealer_info["company_name"]
    dealer.ratings_stars = dealer_info["ratings_stars"]
    dealer.ratings_count = dealer_info["ratings_count"]
    dealer.recommend_percentage = dealer_info["recommend_percentage"]
    dealer.synced_at = now
    return dealer.id
```

- [ ] **Step 5: Run to confirm pass**

Run: `cd scraper && pytest tests/test_dealers.py -v`
Expected: `3 passed`

- [ ] **Step 6: Update `_fake_detail_data` and the one inline dict in `test_run_manager.py`**

In `scraper/tests/test_run_manager.py`, replace the `_fake_detail_data` function (currently lines 89-96) with:

```python
def _fake_detail_data(listing_id: str) -> dict:
    return {
        "price": None, "power_kw": None, "power_cv": None, "displacement_ccm": None,
        "body_type": None, "body_color": None, "num_seats": None, "num_doors": None,
        "num_previous_owners": None, "province": None, "latitude": None, "longitude": None,
        "vat_exposed": None, "price_evaluation_category": None, "price_evaluation_median": None,
        "created_at_source": None, "raw_detail": {"id": listing_id},
        "had_accident": None, "has_full_service_history": None, "gears": None, "drive_train": None,
        "cylinders": None, "weight_kg": None, "co2_emissions_g_km": None,
        "fuel_consumption_combined": None, "fuel_consumption_urban": None, "fuel_consumption_extra_urban": None,
        "emission_class": None, "upholstery": None, "upholstery_color": None,
        "is_conditional_price": None, "interaction_count": None, "favorites_count": None,
        "new_driver_suitable": None, "dealer": None,
    }
```

And in `test_run_brand_sweep_enriches_pending_detail_backlog`, add the same 17 keys to its inline `data={...}` dict (currently lines 228-234), so it reads:

```python
    def fake_fetch_detail(client, url):
        return DetailResult(
            sold=False,
            data={
                "price": 10500, "power_kw": 74, "power_cv": 101, "displacement_ccm": 1199,
                "body_type": "Berlina", "body_color": None, "num_seats": 5, "num_doors": 5,
                "num_previous_owners": None, "province": "TO", "latitude": 44.8, "longitude": 7.3,
                "vat_exposed": False, "price_evaluation_category": 1, "price_evaluation_median": 16100,
                "created_at_source": dt.datetime.utcnow(), "raw_detail": {"id": "pending-1"},
                "had_accident": None, "has_full_service_history": None, "gears": 6, "drive_train": "Anteriore",
                "cylinders": 3, "weight_kg": 1159, "co2_emissions_g_km": None,
                "fuel_consumption_combined": None, "fuel_consumption_urban": None, "fuel_consumption_extra_urban": None,
                "emission_class": "Euro 6d", "upholstery": "Altro", "upholstery_color": None,
                "is_conditional_price": True, "interaction_count": 500, "favorites_count": 20,
                "new_driver_suitable": True, "dealer": None,
            },
        )
```

(This test doesn't assert on the new fields — it's included here purely so the existing assertions on `power_kw`/`price` keep passing without a `KeyError` on the new keys.)

- [ ] **Step 7: Write the failing tests for `process_detail_backlog` picking up the new fields and dealer**

Append to `scraper/tests/test_run_manager.py`:

```python
def test_process_detail_backlog_persists_new_structured_fields(db_session):
    db_session.add(_existing_listing("detail-fields-1", 10000, detail_scraped=False))
    db_session.commit()
    run = ScrapeRun(brand="Fiat", started_at=dt.datetime.utcnow(), status="running")
    db_session.add(run)
    db_session.flush()

    def fake_fetch_detail(client, url):
        data = _fake_detail_data("detail-fields-1")
        data.update({
            "had_accident": False, "has_full_service_history": True, "gears": 6,
            "drive_train": "Anteriore", "cylinders": 3, "weight_kg": 1159,
            "co2_emissions_g_km": 109.0, "fuel_consumption_combined": 5.4,
            "emission_class": "Euro 6d", "upholstery": "Altro", "upholstery_color": "Nero",
            "is_conditional_price": True, "interaction_count": 10670, "favorites_count": 193,
            "new_driver_suitable": True, "dealer": None,
        })
        return DetailResult(sold=False, data=data)

    process_detail_backlog(db_session, _client, BRAND, run, fetch_detail_fn=fake_fetch_detail)

    listing = db_session.get(Listing, "detail-fields-1")
    assert listing.had_accident is False
    assert listing.has_full_service_history is True
    assert listing.gears == 6
    assert listing.drive_train == "Anteriore"
    assert listing.cylinders == 3
    assert listing.weight_kg == 1159
    assert listing.co2_emissions_g_km == 109.0
    assert listing.fuel_consumption_combined == 5.4
    assert listing.emission_class == "Euro 6d"
    assert listing.upholstery == "Altro"
    assert listing.upholstery_color == "Nero"
    assert listing.is_conditional_price is True
    assert listing.interaction_count == 10670
    assert listing.favorites_count == 193
    assert listing.new_driver_suitable is True
    assert listing.dealer_id is None


def test_process_detail_backlog_upserts_dealer_and_links_listing(db_session):
    db_session.add(_existing_listing("detail-dealer-1", 10000, detail_scraped=False))
    db_session.commit()
    run = ScrapeRun(brand="Fiat", started_at=dt.datetime.utcnow(), status="running")
    db_session.add(run)
    db_session.flush()

    def fake_fetch_detail(client, url):
        data = _fake_detail_data("detail-dealer-1")
        data["dealer"] = {
            "id": 555, "company_name": "Test Dealer Srl",
            "ratings_stars": 4.5, "ratings_count": 20, "recommend_percentage": 85,
        }
        return DetailResult(sold=False, data=data)

    process_detail_backlog(db_session, _client, BRAND, run, fetch_detail_fn=fake_fetch_detail)

    listing = db_session.get(Listing, "detail-dealer-1")
    assert listing.dealer_id == 555
    dealer = db_session.get(Dealer, 555)
    assert dealer is not None
    assert dealer.company_name == "Test Dealer Srl"
    assert dealer.ratings_stars == 4.5
```

Add `Dealer` to the existing `from autosmart24.db.models import ...` import line at the top of the file.

- [ ] **Step 8: Run to confirm failure**

Run: `cd scraper && pytest tests/test_run_manager.py -v -k "new_structured_fields or upserts_dealer"`
Expected: FAIL — `AttributeError: 'Listing' object has no attribute 'had_accident'` is impossible (Task 1 added the column); actual expected failure is the ASSERTION values being `None`/unset because `process_detail_backlog` doesn't yet write them. Confirm the failures are assertion failures on the new fields, not crashes.

- [ ] **Step 9: Wire the new fields and dealer upsert into `run_manager.py`**

In `scraper/src/autosmart24/run_manager.py`:

Add to the imports:
```python
from autosmart24.db.dealers import upsert_dealer
from autosmart24.db.models import Dealer, Listing, PriceHistory, ScrapeEvent, ScrapeRun
```

In `process_detail_backlog`'s worker-result loop, immediately after the existing line `row.created_at_source = detail["created_at_source"]` and immediately before `row.raw_detail = detail["raw_detail"]`, insert:

```python
                row.had_accident = detail["had_accident"]
                row.has_full_service_history = detail["has_full_service_history"]
                row.gears = detail["gears"]
                row.drive_train = detail["drive_train"]
                row.cylinders = detail["cylinders"]
                row.weight_kg = detail["weight_kg"]
                row.co2_emissions_g_km = detail["co2_emissions_g_km"]
                row.fuel_consumption_combined = detail["fuel_consumption_combined"]
                row.fuel_consumption_urban = detail["fuel_consumption_urban"]
                row.fuel_consumption_extra_urban = detail["fuel_consumption_extra_urban"]
                row.emission_class = detail["emission_class"]
                row.upholstery = detail["upholstery"]
                row.upholstery_color = detail["upholstery_color"]
                row.is_conditional_price = detail["is_conditional_price"]
                row.interaction_count = detail["interaction_count"]
                row.favorites_count = detail["favorites_count"]
                row.new_driver_suitable = detail["new_driver_suitable"]
                row.dealer_id = upsert_dealer(session, detail["dealer"], now)
```

(`now` is already in scope at that point in the function, set at the top of the `while True:` loop body.)

- [ ] **Step 10: Run to confirm pass**

Run: `cd scraper && pytest tests/test_run_manager.py -v`
Expected: all tests in the file pass (the two new ones plus every pre-existing one, now that the two literal dicts have the required keys).

- [ ] **Step 11: Run the full backend suite**

Run: `cd scraper && python -m pytest -q`
Expected: all pass, no regressions.

- [ ] **Step 12: Commit**

```bash
git add scraper/src/autosmart24/db/dealers.py scraper/tests/test_dealers.py scraper/src/autosmart24/run_manager.py scraper/tests/test_run_manager.py
git commit -m "Persist new structured detail fields and upsert dealers during live scraping"
```

---

## Task 4: One-time backfill script for already-enriched listings

**Files:**
- Create: `scraper/src/autosmart24/db/backfill_detail_fields.py`
- Create: `scraper/tests/test_backfill_detail_fields.py`

**Interfaces:**
- Consumes: `map_detail_listing` (Task 2), `upsert_dealer` (Task 3).
- Produces: `autosmart24.db.backfill_detail_fields.backfill_detail_fields(session: Session, batch_size: int = 500) -> int` (returns count of listings processed) — consumed directly by Task 5's live execution, and by a `__main__` entrypoint for real invocation.

- [ ] **Step 1: Write the failing tests**

`scraper/tests/test_backfill_detail_fields.py`:

```python
import datetime as dt
import json
from pathlib import Path

import pytest

from autosmart24.db.backfill_detail_fields import backfill_detail_fields
from autosmart24.db.models import Dealer, Listing
from autosmart24.scraping.next_data import extract_next_data

FIXTURES = Path(__file__).parent / "fixtures"


def _real_listing_details() -> dict:
    html = (FIXTURES / "detail_fiat_grande_panda.html").read_text(encoding="utf-8")
    data = extract_next_data(html)
    return data["props"]["pageProps"]["listingDetails"]


def _enriched_listing(listing_id: str, raw_detail: dict, detail_scraped: bool = True) -> Listing:
    now = dt.datetime.utcnow()
    return Listing(
        id=listing_id, brand="Fiat", url=f"https://www.autoscout24.it/annunci/{listing_id}",
        first_seen_at=now, last_seen_at=now, last_checked_at=now, status="active",
        detail_scraped=detail_scraped, raw_detail=raw_detail,
    )


def test_backfill_populates_new_fields_from_stored_raw_detail(db_session):
    ld = _real_listing_details()
    db_session.add(_enriched_listing("bf-1", ld))
    db_session.commit()

    processed = backfill_detail_fields(db_session)

    assert processed == 1
    row = db_session.get(Listing, "bf-1")
    assert row.had_accident is False
    assert row.gears == 6
    assert row.drive_train == "Anteriore"
    assert row.weight_kg == 1159
    assert row.emission_class == "Euro 6d"
    assert row.is_conditional_price is True
    assert row.interaction_count == 10670
    assert row.new_driver_suitable is True


def test_backfill_upserts_the_dealer_and_links_the_listing(db_session):
    ld = _real_listing_details()
    db_session.add(_enriched_listing("bf-2", ld))
    db_session.commit()

    backfill_detail_fields(db_session)

    row = db_session.get(Listing, "bf-2")
    assert row.dealer_id == 46936034
    dealer = db_session.get(Dealer, 46936034)
    assert dealer is not None
    assert dealer.company_name == "Puntocar di Tarantino Andrea - Bricherasio"


def test_backfill_skips_listings_without_raw_detail(db_session):
    now = dt.datetime.utcnow()
    db_session.add(Listing(
        id="bf-no-detail", brand="Fiat", url="https://www.autoscout24.it/annunci/bf-no-detail",
        first_seen_at=now, last_seen_at=now, last_checked_at=now, status="active",
        detail_scraped=False, raw_detail=None,
    ))
    db_session.commit()

    processed = backfill_detail_fields(db_session)

    assert processed == 0
    row = db_session.get(Listing, "bf-no-detail")
    assert row.gears is None


def test_backfill_is_idempotent(db_session):
    ld = _real_listing_details()
    db_session.add(_enriched_listing("bf-3", ld))
    db_session.commit()

    first = backfill_detail_fields(db_session)
    second = backfill_detail_fields(db_session)

    assert first == 1
    assert second == 1  # re-processes the same row; must not error or duplicate
    dealers = db_session.query(Dealer).filter_by(id=46936034).all()
    assert len(dealers) == 1


def test_backfill_paginates_across_multiple_batches(db_session):
    ld = _real_listing_details()
    for suffix in ("a", "b", "c"):
        listing_id = f"bf-page-{suffix}"
        raw = dict(ld)
        raw["id"] = listing_id
        db_session.add(_enriched_listing(listing_id, raw))
    db_session.commit()

    processed = backfill_detail_fields(db_session, batch_size=1)

    assert processed == 3
    for suffix in ("a", "b", "c"):
        row = db_session.get(Listing, f"bf-page-{suffix}")
        assert row.gears == 6
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd scraper && pytest tests/test_backfill_detail_fields.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autosmart24.db.backfill_detail_fields'`

- [ ] **Step 3: Implement the backfill script**

```python
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from autosmart24.db.dealers import upsert_dealer
from autosmart24.db.models import Listing
from autosmart24.scraping.detail_mapper import map_detail_listing

logger = logging.getLogger(__name__)


def backfill_detail_fields(session: Session, batch_size: int = 500) -> int:
    """One-time migration: populate the new structured detail-page columns
    (and the dealers table) for every already-enriched listing, by re-reading
    the raw_detail JSON already stored in the database -- no new HTTP
    requests. Must run to completion and be verified BEFORE raw_detail is
    ever dropped (see the design spec's binding migration order)."""
    now = dt.datetime.utcnow()
    processed = 0
    last_id = ""

    while True:
        rows = session.execute(
            select(Listing)
            .where(Listing.detail_scraped.is_(True), Listing.raw_detail.is_not(None), Listing.id > last_id)
            .order_by(Listing.id)
            .limit(batch_size)
        ).scalars().all()
        if not rows:
            break

        for row in rows:
            mapped = map_detail_listing(row.raw_detail)
            row.had_accident = mapped["had_accident"]
            row.has_full_service_history = mapped["has_full_service_history"]
            row.gears = mapped["gears"]
            row.drive_train = mapped["drive_train"]
            row.cylinders = mapped["cylinders"]
            row.weight_kg = mapped["weight_kg"]
            row.co2_emissions_g_km = mapped["co2_emissions_g_km"]
            row.fuel_consumption_combined = mapped["fuel_consumption_combined"]
            row.fuel_consumption_urban = mapped["fuel_consumption_urban"]
            row.fuel_consumption_extra_urban = mapped["fuel_consumption_extra_urban"]
            row.emission_class = mapped["emission_class"]
            row.upholstery = mapped["upholstery"]
            row.upholstery_color = mapped["upholstery_color"]
            row.is_conditional_price = mapped["is_conditional_price"]
            row.interaction_count = mapped["interaction_count"]
            row.favorites_count = mapped["favorites_count"]
            row.new_driver_suitable = mapped["new_driver_suitable"]
            row.dealer_id = upsert_dealer(session, mapped["dealer"], now)
            processed += 1
            last_id = row.id

        session.commit()
        logger.info("Backfilled %d listings so far (last id=%s)", processed, last_id)

    return processed


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from autosmart24.db.session import make_engine, make_session_factory

    engine = make_engine()
    session_factory = make_session_factory(engine)
    session = session_factory()
    try:
        total = backfill_detail_fields(session)
        print(f"Backfill complete: {total} listings processed.")
    finally:
        session.close()
```

Note: `map_detail_listing(row.raw_detail)` re-derives every field the function knows how to extract (including ones already correctly set, like `brand`/`model`), but this script only assigns the 17 *new* fields plus `dealer_id` — it must never touch `brand`, `model`, `price`, etc., which are already correct and could subtly drift if blindly overwritten from a re-parse. Only copy the fields listed above, nothing else.

- [ ] **Step 4: Run to confirm pass**

Run: `cd scraper && pytest tests/test_backfill_detail_fields.py -v`
Expected: `5 passed`

- [ ] **Step 5: Run the full backend suite**

Run: `cd scraper && python -m pytest -q`
Expected: all pass, no regressions.

- [ ] **Step 6: Commit**

```bash
git add scraper/src/autosmart24/db/backfill_detail_fields.py scraper/tests/test_backfill_detail_fields.py
git commit -m "Add one-time backfill script for new detail fields and dealers, sourced from stored raw_detail"
```

---

## Task 5: Run and verify the backfill against the real database

Not a TDD task — this executes Task 4's script against the real, currently-growing production database and verifies its results before Task 6 is allowed to proceed.

**Files:** None.

**CRITICAL operational note:** if a scrape sweep is running when this task starts, either wait for it to reach a natural stopping point or confirm with whoever is operating the scraper that it's safe to proceed — the backfill only reads/writes already-enriched rows and does not conflict with an active sweep at the database level, but running both under close observation is safer than assuming.

- [ ] **Step 1: Snapshot current state**

Run: `docker exec autoscout-postgres-1 psql -U autosmart24 -d autosmart24 -c "SELECT brand, count(*) AS total, count(*) FILTER (WHERE detail_scraped) AS enriched FROM listings GROUP BY brand ORDER BY 2 DESC;"`

Record the enriched count per brand — this is the number of rows the backfill is expected to process.

- [ ] **Step 2: Run the backfill**

Run from `scraper/`: `DATABASE_URL=postgresql+psycopg://autosmart24:autosmart24@localhost:5434/autosmart24 python -m autosmart24.db.backfill_detail_fields`

Expected: prints periodic progress lines and a final `Backfill complete: N listings processed.` where N matches the sum of `enriched` counts from Step 1.

- [ ] **Step 3: Verify field coverage**

Run: `docker exec autoscout-postgres-1 psql -U autosmart24 -d autosmart24 -c "SELECT count(*) AS total, count(gears) AS has_gears, count(drive_train) AS has_drive_train, count(emission_class) AS has_emission_class, count(dealer_id) AS has_dealer, count(*) FILTER (WHERE is_conditional_price IS NOT NULL) AS has_price_flag FROM listings WHERE detail_scraped = true;"`

Expected: `total` matches the enriched count from Step 1. The other counts will be *less* than `total` for fields legitimately absent from some listings' JSON (e.g. `emission_class`/consumption fields are frequently null for older or electric listings) — that is correct, not a bug. `has_dealer` should be non-zero but likely also less than `total` (private-seller listings never get a `dealer_id`).

- [ ] **Step 4: Spot-check correctness against the actual JSON**

Run: `docker exec autoscout-postgres-1 psql -U autosmart24 -d autosmart24 -c "SELECT id, gears, drive_train, weight_kg, raw_detail->'vehicle'->>'gears' AS json_gears, raw_detail->'vehicle'->>'driveTrain' AS json_drive_train, raw_detail->'vehicle'->>'weight' AS json_weight FROM listings WHERE detail_scraped=true AND gears IS NOT NULL LIMIT 10;"`

Confirm for each of the 10 rows: `gears` matches `json_gears` exactly, `drive_train` matches `json_drive_train` exactly, and `weight_kg` is the correctly-parsed integer from `json_weight` (e.g. `json_weight = "1.226 kg"` → `weight_kg = 1226`).

- [ ] **Step 5: Verify dealers table**

Run: `docker exec autoscout-postgres-1 psql -U autosmart24 -d autosmart24 -c "SELECT count(*) FROM dealers;"` and `docker exec autoscout-postgres-1 psql -U autosmart24 -d autosmart24 -c "SELECT id, company_name, ratings_stars, ratings_count, recommend_percentage FROM dealers ORDER BY ratings_count DESC LIMIT 5;"`

Confirm the row count is plausible (hundreds to low thousands, far fewer than the number of listings) and the top rows show sane values (stars 0-5, percentage 0-100).

- [ ] **Step 6: Confirm existing data untouched**

Run: `docker exec autoscout-postgres-1 psql -U autosmart24 -d autosmart24 -c "SELECT brand, count(*), count(*) FILTER (WHERE detail_scraped) FROM listings GROUP BY brand ORDER BY 2 DESC;"`

Confirm identical counts to Step 1 — the backfill must not have changed which rows exist or their `detail_scraped` status, only added values to the new columns.

**Do not proceed to Task 6 until every check above passes.** If any check fails, stop and diagnose — do not drop `raw_detail`/`raw_snippet` with an unverified or incomplete backfill.

---

## Task 6: Drop `raw_detail` and `raw_snippet`

**Files:**
- Modify: `scraper/src/autosmart24/db/models.py`
- Create: `scraper/migrations/versions/0006_drop_raw_json.py`
- Modify: `scraper/src/autosmart24/scraping/detail_mapper.py`
- Modify: `scraper/src/autosmart24/scraping/snippet_mapper.py`
- Modify: `scraper/src/autosmart24/run_manager.py`
- Modify: `scraper/tests/test_detail_mapper.py`
- Modify: `scraper/tests/test_snippet_mapper.py`
- Modify: `scraper/tests/test_run_manager.py`
- Modify: `scraper/tests/test_backfill_detail_fields.py`

**Interfaces:** None new — this task removes surface area only.

**Do not start this task until Task 5's verification has passed.**

- [ ] **Step 1: Remove the columns from the model**

In `scraper/src/autosmart24/db/models.py`, delete these two lines from `Listing`:
```python
    raw_snippet: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    raw_detail: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
```

If `JSONVariant` and the `JSONB`/`JSON` imports become unused as a result, leave them — `JSONVariant` is a module-level constant that costs nothing to keep and removing it is out of this task's scope (no other model uses JSON columns today, but that's a future concern, not this one).

- [ ] **Step 2: Write the migration**

`scraper/migrations/versions/0006_drop_raw_json.py`:

```python
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006_drop_raw_json"
down_revision = "0005_detail_fields_dealers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("listings", "raw_detail")
    op.drop_column("listings", "raw_snippet")


def downgrade() -> None:
    op.add_column("listings", sa.Column("raw_snippet", JSONB(), nullable=True))
    op.add_column("listings", sa.Column("raw_detail", JSONB(), nullable=True))
```

(Downgrade restores the columns' structure only — the JSON data itself is not recoverable once dropped, which is the explicitly-accepted tradeoff from the design spec.)

- [ ] **Step 3: Remove `raw_detail`/`raw_snippet` from the mappers**

In `scraper/src/autosmart24/scraping/detail_mapper.py`, delete the line `"raw_detail": ld,` from the dict `map_detail_listing` returns.

In `scraper/src/autosmart24/scraping/snippet_mapper.py`, delete the line `"raw_snippet": raw,` from the dict `map_snippet_listing` returns.

- [ ] **Step 4: Remove references in `run_manager.py`**

In `scraper/src/autosmart24/run_manager.py`:
- In `process_detail_backlog`'s worker-result loop, delete the line `row.raw_detail = detail["raw_detail"]`.
- In `run_brand_sweep`'s new-listing `Listing(...)` construction, delete the line `raw_snippet=snippet["raw_snippet"],`.
- In `run_brand_sweep`'s relist-update branch, delete the line `row.raw_snippet = snippet["raw_snippet"]`.

- [ ] **Step 5: Update the test files**

In `scraper/tests/test_detail_mapper.py`: delete the assertion `assert mapped["raw_detail"] == ld` from `test_map_detail_listing_extracts_full_fields`.

In `scraper/tests/test_snippet_mapper.py`: delete the assertion `assert mapped["raw_snippet"] == raw`.

In `scraper/tests/test_run_manager.py`:
- Remove `"raw_detail": {"id": listing_id},` from `_fake_detail_data`.
- Remove `"raw_detail": {"id": "pending-1"},` from the inline dict in `test_run_brand_sweep_enriches_pending_detail_backlog`.
- Remove `"raw_snippet": {"id": listing_id},` from `_fake_snippet`.

In `scraper/tests/test_backfill_detail_fields.py`: the `_enriched_listing` helper constructs `Listing(..., raw_detail=raw_detail)` — since the column no longer exists, the backfill script itself (Task 4) also no longer has a `raw_detail` column to read from. **This makes the backfill script permanently non-functional after this task, which is correct and expected** — its only purpose was the one-time migration completed and verified in Task 5. Delete `scraper/tests/test_backfill_detail_fields.py` entirely in this step (the script's tests exercised behavior that depended on a column that no longer exists) and leave `scraper/src/autosmart24/db/backfill_detail_fields.py` in place as a historical record of how the migration was performed, but do not attempt to keep it passing — it is dead code by design once `raw_detail` is gone. Add a one-line comment at the top of `backfill_detail_fields.py`:

```python
# HISTORICAL: this script populated the detail-fields/dealers schema added in
# migration 0005 from the raw_detail JSON that existed at the time. It cannot
# run anymore after migration 0006 dropped that column -- kept only as a
# record of how the one-time backfill was performed.
```

- [ ] **Step 6: Run the full backend suite**

Run: `cd scraper && python -m pytest -q`
Expected: all pass (the deleted `test_backfill_detail_fields.py` tests are gone, not failing).

- [ ] **Step 7: Apply the migration to the real database**

Run from `scraper/`: `DATABASE_URL=postgresql+psycopg://autosmart24:autosmart24@localhost:5434/autosmart24 python -m alembic upgrade head`
Expected: reaches `0006_drop_raw_json`.

Confirm: `docker exec autoscout-postgres-1 psql -U autosmart24 -d autosmart24 -c "\d listings"` no longer lists `raw_detail`/`raw_snippet`. Confirm the database shrank: `docker exec autoscout-postgres-1 psql -U autosmart24 -d autosmart24 -c "SELECT pg_size_pretty(pg_database_size('autosmart24'));"` and compare against the ~1.38 GB baseline measured during design — expect a substantial drop (VACUUM may be needed for the OS-visible file size to shrink immediately; `pg_database_size` may still show a large number until autovacuum reclaims space, which is expected Postgres behavior, not a bug).

- [ ] **Step 8: Commit**

```bash
git add scraper/src/autosmart24/db/models.py scraper/migrations/versions/0006_drop_raw_json.py scraper/src/autosmart24/scraping/detail_mapper.py scraper/src/autosmart24/scraping/snippet_mapper.py scraper/src/autosmart24/run_manager.py scraper/tests/test_detail_mapper.py scraper/tests/test_snippet_mapper.py scraper/tests/test_run_manager.py scraper/src/autosmart24/db/backfill_detail_fields.py
git rm scraper/tests/test_backfill_detail_fields.py
git commit -m "Drop raw_detail/raw_snippet now that the backfill (Task 5) is verified complete"
```

---

## Task 7: Live verification

Not a TDD task — confirms the live scraper still works end to end with the new schema, after everything above has shipped.

**Files:** None.

- [ ] **Step 1: Full test suite baseline**

Run: `cd scraper && python -m pytest -q`
Expected: all pass.

- [ ] **Step 2: Rebuild and restart the app container**

Run (from `C:\App AI\Autoscout`): `docker compose up -d --build app`
Expected: the app container rebuilds and restarts cleanly; `docker compose logs app --tail 20` shows the migration chain reaching `0006_drop_raw_json` and no startup errors. Do not rebuild `postgres` or `dashboard` — only `app` changed.

**Do not run this step while a scrape sweep the user cares about is mid-run** — restarting the container ends any in-progress sweep (the same tradeoff already accepted earlier in this project when deploying the retry fix). Confirm with whoever is operating the scraper, or check `docker exec autoscout-postgres-1 psql -U autosmart24 -d autosmart24 -c "SELECT id, brand, status FROM scrape_runs WHERE status='running';"` first and time this step for when nothing is running.

- [ ] **Step 3: Trigger a small live enrichment and confirm new fields populate**

Pick any tracked brand with pending detail backlog (`detail_scraped = false` rows), or trigger a fresh `run-now` on one via `curl -s -X POST http://localhost:8001/brands/{slug}/run-now`. Let it run briefly, then check:

`docker exec autoscout-postgres-1 psql -U autosmart24 -d autosmart24 -c "SELECT id, gears, drive_train, emission_class, dealer_id FROM listings WHERE detail_scraped=true ORDER BY last_checked_at DESC LIMIT 5;"`

Expected: at least some of the 5 most-recently-checked rows show non-null values in the new columns (some nulls are expected and correct where the source JSON itself lacks that field).

- [ ] **Step 4: Confirm no exceptions in the logs**

Run: `docker compose logs app --tail 50 | grep -iE "error|exception|traceback"`
Expected: no output, or only pre-existing benign warnings already known from earlier in this project (deprecation notices) — no new tracebacks.

## Self-review notes

- **Spec coverage:** §3 (new Listing columns) → Task 1 + Task 2 (extraction) + Task 3 (persistence); §4 (dealers table) → Task 1 (schema) + Task 3 (live upsert) + Task 4 (backfill upsert); §5 (binding migration order) → Tasks 1→2/3→4→5→6, in that exact sequence, with Task 5 as an explicit gate before Task 6 can start; §6 (size estimate) → verified empirically in Task 6 Step 7 rather than only assumed; §7 (testing) → each task's own TDD steps, including the cross-cutting `_fake_detail_data`/inline-dict fix in Task 3 that a less careful plan would have missed and left as a surprise `KeyError` wave; §8 (risks: weight parsing, equipment reversibility, price history) → weight parsing tested explicitly (Task 2), equipment/price-history correctly left untouched and out of scope everywhere.
- **Placeholder scan:** no TBD/TODO; every step has runnable code grounded in the actual current file contents (read at plan-writing time, not assumed) and the actual test fixture's real field values (measured from `detail_fiat_grande_panda.html`, not invented).
- **Type consistency verified:** `map_detail_listing`'s new dict keys (Task 2) match `Listing`'s new column names (Task 1) one-for-one; `extract_dealer`'s returned dict shape (`id`/`company_name`/`ratings_stars`/`ratings_count`/`recommend_percentage`) matches exactly what `upsert_dealer` (Task 3) expects and what `Dealer`'s columns (Task 1) hold; the backfill script (Task 4) consumes the identical `map_detail_listing`/`upsert_dealer` functions the live path uses (Task 3), so there is exactly one JSON-parsing implementation and one dealer-upsert implementation shared by both, not two that could drift.
- **Irreversibility acknowledged explicitly:** Task 6's migration downgrade restores column structure only, not data — matching the design spec's accepted tradeoff, stated again in the migration file's own downgrade comment so a future reader isn't surprised.
