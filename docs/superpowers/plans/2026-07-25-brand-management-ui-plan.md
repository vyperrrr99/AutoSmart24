# Brand Management, Per-Brand Scheduling, and Live Monitoring — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hardcoded 5-brand `MVP_BRANDS` list with a database-backed brand catalog (all ~290 brands autoscout24.it exposes) and a user-managed "tracked brands" selection, each with its own registration-year filter and day/hour schedule, all editable from the dashboard with immediate effect on the live scheduler — plus faster dashboard polling while a run is active, for near-real-time progress visibility.

**Architecture:** Two new tables — `brand_catalog` (the full site catalog, refreshed on demand from a single page fetch) and `tracked_brands` (the user's selection, replacing `MVP_BRANDS`). The scheduler (`BrandScheduler`) becomes DB-driven: every mutating API call (add/update/remove/pause/resume) updates the DB row and the live APScheduler job in the same request, so changes take effect immediately, no restart needed. The registration-year filter is re-read from the database at the start of every sweep (not baked into the job at schedule time), so editing it also affects an already-scheduled recurring job. `BrandConfig` (the existing lightweight dataclass already used by `run_manager.py`/`scheduler.py`) stays the runtime value type passed around; `TrackedBrand` ORM rows are converted to it at the point of use, keeping the ORM out of code that doesn't need it.

**Tech Stack:** Same as the existing project — no new dependencies. Scheduling uses APScheduler's `CronTrigger` (already a transitive dependency of the pinned `APScheduler==3.10.4`), replacing the current `IntervalTrigger`.

## Global Constraints

- Search-query splitting criterion must NEVER be price — only model and registration year (unchanged, unaffected by this plan).
- "Sold" status requires explicit detail-page confirmation — never inferred from absence (unchanged).
- The dashboard is the sole monitoring/notification channel — no email/Telegram (unchanged).
- Single machine, single IP, no IP rotation (unchanged) — this plan does not add concurrency beyond the existing `SCRAPE_CONCURRENCY`/`SCRAPE_SESSION_REFRESH_REQUESTS`/`SCRAPE_MIN_DELAY_SECONDS`/`SCRAPE_MAX_DELAY_SECONDS` knobs; tracking many brands simultaneously is still bound by the same one-IP posture.
- Scheduling is deliberately simple (optional day-of-week + hour + minute, translating directly to one `CronTrigger`), not full cron syntax — a dropdown and a time picker are buildable in a UI; a cron expression field is not friendly to build or use here.
- The slug used to build search URLs for a catalog brand is derived automatically from its display name (lowercase, non-alphanumeric runs collapsed to a single hyphen, trimmed) and **must be validated against a representative sample of real catalog data, not assumed correct for all ~290 entries** — Task 2 includes this validation as an explicit step, not an afterthought.
- On first startup after this plan ships, if `tracked_brands` is empty, it is seeded from the existing `MVP_BRANDS` (same 5 brands, same `make_id`/slug) so today's behavior is not silently lost. The seeded schedule (daily at 03:00) is not identical to today's `SCRAPE_INTERVAL_DAYS=4` — this is a deliberate, disclosed behavior change (see Task 6) since interval-days and day/hour scheduling are different paradigms; the user can adjust it immediately from the new UI.
- Base URL: `https://www.autoscout24.it`.

---

## Task 1: Database models and migration (`brand_catalog`, `tracked_brands`)

**Files:**
- Modify: `scraper/src/autosmart24/db/models.py`
- Create: `scraper/migrations/versions/0004_brand_catalog_and_tracked_brands.py`
- Create: `scraper/tests/test_brand_models.py`

**Interfaces:**
- Produces: `autosmart24.db.models.BrandCatalog` (columns: `make_id: int` PK, `display_name: str`, `slug: str`, `synced_at: datetime`), `autosmart24.db.models.TrackedBrand` (columns: `make_id: int` PK/FK to `brand_catalog.make_id`, `slug: str` unique, `display_name: str`, `paused: bool`, `year_from_years: int | None`, `schedule_day_of_week: str | None`, `schedule_hour: int`, `schedule_minute: int`, `created_at: datetime`) — consumed by every later task.

- [ ] **Step 1: Read the current file**

Read `scraper/src/autosmart24/db/models.py` in full — confirm `Base`, `Listing`, `PriceHistory`, `ScrapeRun`, `ScrapeEvent` are present and unchanged since the province-width fix (migration `0003_widen_province`).

- [ ] **Step 2: Write the failing tests**

`scraper/tests/test_brand_models.py`:

```python
import datetime as dt

import pytest

from autosmart24.db.models import BrandCatalog, TrackedBrand


def test_brand_catalog_round_trips(db_session):
    db_session.add(BrandCatalog(make_id=28, display_name="Fiat", slug="fiat", synced_at=dt.datetime.utcnow()))
    db_session.commit()

    row = db_session.get(BrandCatalog, 28)
    assert row is not None
    assert row.display_name == "Fiat"
    assert row.slug == "fiat"


def test_tracked_brand_round_trips(db_session):
    db_session.add(BrandCatalog(make_id=28, display_name="Fiat", slug="fiat", synced_at=dt.datetime.utcnow()))
    db_session.commit()

    db_session.add(
        TrackedBrand(
            make_id=28, slug="fiat", display_name="Fiat", paused=False,
            year_from_years=5, schedule_day_of_week=None, schedule_hour=3, schedule_minute=0,
            created_at=dt.datetime.utcnow(),
        )
    )
    db_session.commit()

    row = db_session.get(TrackedBrand, 28)
    assert row is not None
    assert row.year_from_years == 5
    assert row.schedule_day_of_week is None
    assert row.schedule_hour == 3
    assert row.paused is False


def test_tracked_brand_slug_must_be_unique(db_session):
    db_session.add(BrandCatalog(make_id=28, display_name="Fiat", slug="fiat", synced_at=dt.datetime.utcnow()))
    db_session.add(BrandCatalog(make_id=29, display_name="Fiat Professional", slug="fiat", synced_at=dt.datetime.utcnow()))
    db_session.commit()

    db_session.add(
        TrackedBrand(
            make_id=28, slug="fiat", display_name="Fiat", paused=False,
            year_from_years=None, schedule_day_of_week=None, schedule_hour=3, schedule_minute=0,
            created_at=dt.datetime.utcnow(),
        )
    )
    db_session.commit()

    db_session.add(
        TrackedBrand(
            make_id=29, slug="fiat", display_name="Fiat Professional", paused=False,
            year_from_years=None, schedule_day_of_week=None, schedule_hour=3, schedule_minute=0,
            created_at=dt.datetime.utcnow(),
        )
    )
    with pytest.raises(Exception):
        db_session.commit()
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd scraper && pytest tests/test_brand_models.py -v`
Expected: FAIL with `ImportError: cannot import name 'BrandCatalog'`

- [ ] **Step 3: Add the models**

Add to `scraper/src/autosmart24/db/models.py`, after the existing `ScrapeEvent` class:

```python
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
```

- [ ] **Step 4: Run to confirm pass**

Run: `cd scraper && pytest tests/test_brand_models.py -v`
Expected: `3 passed`

- [ ] **Step 5: Write the migration**

Read `scraper/migrations/versions/0003_widen_province.py` first for the exact style. Create `scraper/migrations/versions/0004_brand_catalog_and_tracked_brands.py`:

```python
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_brand_catalog_and_tracked_brands"
down_revision = "0003_widen_province"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "brand_catalog",
        sa.Column("make_id", sa.Integer(), primary_key=True),
        sa.Column("display_name", sa.String(64), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "tracked_brands",
        sa.Column("make_id", sa.Integer(), sa.ForeignKey("brand_catalog.make_id"), primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column("display_name", sa.String(64), nullable=False),
        sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("year_from_years", sa.Integer(), nullable=True),
        sa.Column("schedule_day_of_week", sa.String(3), nullable=True),
        sa.Column("schedule_hour", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("schedule_minute", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("tracked_brands")
    op.drop_table("brand_catalog")
```

- [ ] **Step 6: Verify against the real running Postgres**

Docker Desktop is running; the `postgres` container is up on host port 5434 (`autosmart24`/`autosmart24`). This database currently holds real data from tonight's live scraping test — the migration only adds two new tables, it does not touch `listings` or anything else, so this is safe.

Run from `scraper/`: `DATABASE_URL=postgresql+psycopg://autosmart24:autosmart24@localhost:5434/autosmart24 python -m alembic upgrade head`
Expected: reaches `0004_brand_catalog_and_tracked_brands` with no errors.

Confirm: `docker exec autoscout-postgres-1 psql -U autosmart24 -d autosmart24 -c "\dt"` — expect `brand_catalog` and `tracked_brands` alongside the existing tables, and `docker exec autoscout-postgres-1 psql -U autosmart24 -d autosmart24 -c "SELECT count(*) FROM listings;"` to confirm existing data is untouched (should still show the same large row count from tonight's test).

- [ ] **Step 7: Commit**

```bash
git add scraper/src/autosmart24/db/models.py scraper/migrations/versions/0004_brand_catalog_and_tracked_brands.py scraper/tests/test_brand_models.py
git commit -m "Add brand_catalog and tracked_brands tables"
```

---

## Task 2: Brand catalog fetch and slug derivation (`brand_catalog.py`)

**Files:**
- Create: `scraper/src/autosmart24/scraping/brand_catalog.py`
- Create: `scraper/tests/test_brand_catalog.py`

**Interfaces:**
- Consumes: `fetch_page_data` (`scraping/crawler.py`), `RateLimitedClient` (`scraping/http_client.py`), `build_search_url` (`scraping/search_query.py`).
- Produces: `autosmart24.scraping.brand_catalog.CatalogEntry` (dataclass: `make_id: int`, `display_name: str`, `slug: str`), `.derive_slug(display_name: str) -> str`, `.fetch_brand_catalog(client: RateLimitedClient) -> list[CatalogEntry]` — consumed by `api/app.py` (Task 6).

- [ ] **Step 1: Write the failing tests**

`scraper/tests/test_brand_catalog.py`:

```python
import json

import httpx
import respx

from autosmart24.scraping.brand_catalog import derive_slug, fetch_brand_catalog
from autosmart24.scraping.http_client import RateLimitedClient
from autosmart24.scraping.search_query import build_search_url


def test_derive_slug_single_word():
    assert derive_slug("Fiat") == "fiat"


def test_derive_slug_two_words():
    assert derive_slug("Alfa Romeo") == "alfa-romeo"


def test_derive_slug_already_hyphenated():
    assert derive_slug("Mercedes-Benz") == "mercedes-benz"


def test_derive_slug_collapses_multiple_separators():
    assert derive_slug("Land  Rover") == "land-rover"


def test_derive_slug_strips_leading_trailing_punctuation():
    assert derive_slug(" DS Automobiles ") == "ds-automobiles"


def _next_data_html(page_props: dict) -> str:
    payload = {"props": {"pageProps": page_props}}
    return f'<html><body><script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script></body></html>'


@respx.mock
def test_fetch_brand_catalog_parses_all_makes():
    page_props = {
        "taxonomy": {
            "makes": {
                "6": {"label": "Alfa Romeo", "value": 6},
                "28": {"label": "Fiat", "value": 28},
                "47": {"label": "Mercedes-Benz", "value": 47},
            }
        }
    }
    url = build_search_url("fiat", page=1, make_id=28)
    respx.get(url).mock(return_value=httpx.Response(200, text=_next_data_html(page_props)))

    client = RateLimitedClient(min_delay_seconds=0, max_delay_seconds=0, sleep_fn=lambda _: None)
    entries = fetch_brand_catalog(client)

    by_make_id = {e.make_id: e for e in entries}
    assert len(entries) == 3
    assert by_make_id[6].display_name == "Alfa Romeo"
    assert by_make_id[6].slug == "alfa-romeo"
    assert by_make_id[28].slug == "fiat"
    assert by_make_id[47].slug == "mercedes-benz"
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd scraper && pytest tests/test_brand_catalog.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autosmart24.scraping.brand_catalog'`

- [ ] **Step 3: Implement brand_catalog.py**

`scraper/src/autosmart24/scraping/brand_catalog.py`:

```python
from __future__ import annotations

import re
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


def derive_slug(display_name: str) -> str:
    slug = _SLUG_SEPARATOR_RE.sub("-", display_name.strip().lower())
    return slug.strip("-")


def fetch_brand_catalog(client: RateLimitedClient) -> list[CatalogEntry]:
    url = build_search_url(ANCHOR_BRAND_SLUG, page=1, make_id=ANCHOR_MAKE_ID)
    page_props = fetch_page_data(client, url)
    makes = page_props["taxonomy"]["makes"]
    return [
        CatalogEntry(make_id=int(entry["value"]), display_name=entry["label"], slug=derive_slug(entry["label"]))
        for entry in makes.values()
    ]
```

- [ ] **Step 4: Run to confirm pass**

Run: `cd scraper && pytest tests/test_brand_catalog.py -v`
Expected: `9 passed`

- [ ] **Step 5: Validate slug derivation against the real, full catalog**

This is the one part of this plan not proven against real data yet — do not skip it. From `scraper/`, run:

```bash
python -c "
from autosmart24.scraping.brand_catalog import fetch_brand_catalog
from autosmart24.scraping.http_client import RateLimitedClient
client = RateLimitedClient(min_delay_seconds=3, max_delay_seconds=8)
entries = fetch_brand_catalog(client)
print(f'{len(entries)} brands found')
for e in sorted(entries, key=lambda e: e.display_name):
    print(f'{e.make_id:>6}  {e.display_name!r:35}  -> {e.slug}')
client.close()
"
```

Expected: around 290 entries. Read through the full printed list. Look specifically for: names that collapse to the same slug as a different brand (a genuine collision — note it, this needs a manual `slug` correction after catalog refresh, out of scope to auto-resolve here), any slug that looks obviously wrong (e.g. empty string, or containing characters `build_search_url` wouldn't handle cleanly in a URL path segment). Record what you found in the report — if everything looks sane, say so explicitly; if you find genuine collisions or malformed slugs, list them (don't silently ignore, and don't try to fix the derivation algorithm to handle every case perfectly — a rare bad slug is correctable manually later via the `brand_catalog` table, per the design's stated risk).

- [ ] **Step 6: Commit**

```bash
git add scraper/src/autosmart24/scraping/brand_catalog.py scraper/tests/test_brand_catalog.py
git commit -m "Add brand catalog fetch (taxonomy.makes) and slug derivation"
```

---

## Task 3: Cron-based scheduling in `BrandScheduler`

**Files:**
- Modify: `scraper/src/autosmart24/scheduler.py`
- Modify: `scraper/tests/test_scheduler.py`

**Interfaces:**
- Produces: `autosmart24.scheduler.BrandScheduler.schedule_brand(brand: BrandConfig, run_fn, day_of_week: str | None = None, hour: int = 3, minute: int = 0) -> None` (signature change: `interval_days` replaced by `day_of_week`/`hour`/`minute`), `.remove_brand_job(brand_slug: str) -> None` — consumed by `api/main.py` (Task 5), `api/app.py` (Task 6).

- [ ] **Step 1: Read the current file**

Read `scraper/src/autosmart24/scheduler.py` in full — confirm `BrandRunGuard` (unchanged by this task) and the current `IntervalTrigger`-based `schedule_brand`.

- [ ] **Step 2: Update the existing tests and add new ones**

In `scraper/tests/test_scheduler.py`, update the two existing scheduling tests (`BrandRunGuard` tests are untouched). Replace:

```python
def test_schedule_brand_registers_job():
    scheduler = BrandScheduler(BackgroundScheduler())
    scheduler.schedule_brand(BRAND, interval_days=4, run_fn=lambda brand: None)

    job = scheduler.scheduler.get_job("fiat")
    assert job is not None
    assert scheduler.is_paused("fiat") is False


def test_pause_and_resume_brand():
    scheduler = BrandScheduler(BackgroundScheduler())
    scheduler.schedule_brand(BRAND, interval_days=4, run_fn=lambda brand: None)

    scheduler.pause_brand("fiat")
    assert scheduler.is_paused("fiat") is True

    scheduler.resume_brand("fiat")
    assert scheduler.is_paused("fiat") is False
```

with:

```python
def test_schedule_brand_registers_job():
    scheduler = BrandScheduler(BackgroundScheduler())
    scheduler.schedule_brand(BRAND, run_fn=lambda brand: None, hour=3, minute=0)

    job = scheduler.scheduler.get_job("fiat")
    assert job is not None
    assert scheduler.is_paused("fiat") is False


def test_pause_and_resume_brand():
    scheduler = BrandScheduler(BackgroundScheduler())
    scheduler.schedule_brand(BRAND, run_fn=lambda brand: None, hour=3, minute=0)

    scheduler.pause_brand("fiat")
    assert scheduler.is_paused("fiat") is True

    scheduler.resume_brand("fiat")
    assert scheduler.is_paused("fiat") is False
```

Then append these new tests to the end of the file:

```python
def test_schedule_brand_with_no_day_of_week_is_unrestricted():
    scheduler = BrandScheduler(BackgroundScheduler())
    scheduler.schedule_brand(BRAND, run_fn=lambda brand: None, day_of_week=None, hour=3, minute=0)

    job = scheduler.scheduler.get_job("fiat")
    day_field = next(f for f in job.trigger.fields if f.name == "day_of_week")
    assert day_field.is_default is True


def test_schedule_brand_with_day_of_week_restricts_to_that_day():
    scheduler = BrandScheduler(BackgroundScheduler())
    scheduler.schedule_brand(BRAND, run_fn=lambda brand: None, day_of_week="mon", hour=3, minute=0)

    job = scheduler.scheduler.get_job("fiat")
    day_field = next(f for f in job.trigger.fields if f.name == "day_of_week")
    assert day_field.is_default is False


def test_schedule_brand_replaces_existing_job_for_the_same_brand():
    scheduler = BrandScheduler(BackgroundScheduler())
    scheduler.schedule_brand(BRAND, run_fn=lambda brand: None, day_of_week=None, hour=3, minute=0)
    scheduler.schedule_brand(BRAND, run_fn=lambda brand: None, day_of_week="mon", hour=4, minute=30)

    jobs = [j for j in scheduler.scheduler.get_jobs() if j.id == "fiat"]
    assert len(jobs) == 1
    job = jobs[0]
    assert job.trigger.fields[job.trigger.FIELD_NAMES.index("hour")].expressions[0].first == 4


def test_remove_brand_job_removes_an_existing_job():
    scheduler = BrandScheduler(BackgroundScheduler())
    scheduler.schedule_brand(BRAND, run_fn=lambda brand: None, hour=3, minute=0)

    scheduler.remove_brand_job("fiat")

    assert scheduler.scheduler.get_job("fiat") is None


def test_remove_brand_job_is_a_no_op_for_an_unknown_brand():
    scheduler = BrandScheduler(BackgroundScheduler())

    scheduler.remove_brand_job("does-not-exist")  # must not raise
```

- [ ] **Step 3: Run to confirm failure**

Run: `cd scraper && pytest tests/test_scheduler.py -v`
Expected: FAIL — `schedule_brand()` does not yet accept `day_of_week`/`hour`/`minute`, and `remove_brand_job` does not exist.

- [ ] **Step 4: Update scheduler.py**

Replace the imports and `schedule_brand` method, and add `remove_brand_job`:

```python
from __future__ import annotations

import threading

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from autosmart24.config import BrandConfig


class BrandRunGuard:
    """Thread-safe guard preventing concurrent sweeps of the same brand.

    APScheduler's per-job single-instance protection only applies within a
    single job id. A manual "run now" job (a distinct job id per invocation)
    can otherwise execute concurrently with the recurring scheduled job for
    the same brand, or with another manual trigger. This guard tracks
    in-progress brands independently of job ids.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running: set[str] = set()

    def try_acquire(self, brand_slug: str) -> bool:
        with self._lock:
            if brand_slug in self._running:
                return False
            self._running.add(brand_slug)
            return True

    def release(self, brand_slug: str) -> None:
        with self._lock:
            self._running.discard(brand_slug)


class BrandScheduler:
    def __init__(self, scheduler: BackgroundScheduler | None = None):
        self.scheduler = scheduler or BackgroundScheduler()

    def schedule_brand(
        self,
        brand: BrandConfig,
        run_fn,
        day_of_week: str | None = None,
        hour: int = 3,
        minute: int = 0,
    ) -> None:
        self.scheduler.add_job(
            run_fn,
            trigger=CronTrigger(day_of_week=day_of_week, hour=hour, minute=minute),
            id=brand.slug,
            replace_existing=True,
            args=[brand],
        )

    def remove_brand_job(self, brand_slug: str) -> None:
        if self.scheduler.get_job(brand_slug) is not None:
            self.scheduler.remove_job(brand_slug)

    def pause_brand(self, brand_slug: str) -> None:
        self.scheduler.pause_job(brand_slug)

    def resume_brand(self, brand_slug: str) -> None:
        self.scheduler.resume_job(brand_slug)

    def is_paused(self, brand_slug: str) -> bool:
        job = self.scheduler.get_job(brand_slug)
        if job is None:
            return False
        try:
            return job.next_run_time is None
        except AttributeError:
            return False

    def start(self) -> None:
        self.scheduler.start()

    def shutdown(self) -> None:
        self.scheduler.shutdown(wait=False)
```

- [ ] **Step 5: Run to confirm pass**

Run: `cd scraper && pytest tests/test_scheduler.py -v`
Expected: `9 passed` (5 pre-existing, 2 updated + 4 new — note `test_schedule_brand_replaces_existing_job_for_the_same_brand` and the 4 `BrandRunGuard` tests were already there, so: 2 updated scheduling tests + 4 unchanged guard tests + 4 new = 10; if your count differs from 10, read the file and reconcile before moving on — state the actual number and why in your report rather than assuming 9 or 10 is "close enough").

If `day_field.is_default` raises `AttributeError` (i.e. this APScheduler version's `BaseField` doesn't expose that attribute), that is a real finding — stop, report it, and inspect `apscheduler.triggers.cron.fields.BaseField`'s actual public surface in the installed version (`python -c "import apscheduler; print(apscheduler.__file__)"` to locate it) to find the correct equivalent check, rather than guessing further.

- [ ] **Step 6: Commit**

```bash
git add scraper/src/autosmart24/scheduler.py scraper/tests/test_scheduler.py
git commit -m "Switch BrandScheduler to day/hour/minute CronTrigger scheduling; add remove_brand_job"
```

---

## Task 4: API schemas for brand catalog and tracked-brand management

**Files:**
- Modify: `scraper/src/autosmart24/api/schemas.py`

**Interfaces:**
- Produces: `autosmart24.api.schemas.BrandCatalogEntryOut` (`make_id: int`, `display_name: str`, `slug: str`), extends `BrandStatusOut` with `make_id: int`, `year_from_years: int | None`, `schedule_day_of_week: str | None`, `schedule_hour: int`, `schedule_minute: int`; `.AddBrandsRequest` (`make_ids: list[int]`, `year_from_years: int | None = None`, `schedule_day_of_week: str | None = None`, `schedule_hour: int = 3`, `schedule_minute: int = 0`); `.UpdateBrandRequest` and `.ApplyDefaultsRequest` (both: `year_from_years: int | None = None`, `schedule_day_of_week: str | None = None`, `schedule_hour: int | None = None`, `schedule_minute: int | None = None` — all fields optional, presence tracked via Pydantic's `model_fields_set` so "omitted" and "explicitly set to null" are distinguishable) — consumed by `api/main.py` (Task 5).

No dedicated test file for this task — these are plain data classes exercised by Task 5's endpoint tests.

- [ ] **Step 1: Read the current file**

Read `scraper/src/autosmart24/api/schemas.py` in full — confirm `RunOut`, `EventOut`, `BrandStatusOut` are present and unchanged.

- [ ] **Step 2: Update schemas.py**

Full replacement:

```python
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
```

`BrandStatusOut` intentionally drops its `from_attributes` config and is no longer built via `model_validate` from a single ORM row — `api/main.py` (Task 5) constructs it explicitly from a `TrackedBrand` row plus a separately-queried `last_run`, since the two don't come from the same query.

- [ ] **Step 3: Verify the module still imports cleanly**

Run: `cd scraper && python -c "import autosmart24.api.schemas; print('ok')"`
Expected: prints `ok`, no traceback.

- [ ] **Step 4: Commit**

```bash
git add scraper/src/autosmart24/api/schemas.py
git commit -m "Add brand-catalog and tracked-brand-management API schemas"
```

---

## Task 5: API endpoints for brand catalog and tracked-brand management

This is the largest task — `create_app`'s signature changes (two new required callables) and every existing endpoint that referenced `MVP_BRANDS` now reads from the database instead. Handle the file and its test suite as one cohesive change.

**Files:**
- Modify: `scraper/src/autosmart24/api/main.py`
- Modify: `scraper/tests/test_api.py`

**Interfaces:**
- Consumes: `BrandCatalog`, `TrackedBrand` (Task 1), `CatalogEntry`, `fetch_brand_catalog` (Task 2), `BrandScheduler.schedule_brand`/`remove_brand_job` (Task 3), the schemas from Task 4.
- Produces: `autosmart24.api.main.create_app(session_factory, scheduler: BrandScheduler, run_now_fn: Callable[[BrandConfig], None], run_fn: Callable[[BrandConfig], None], refresh_catalog_fn: Callable[[], list[CatalogEntry]]) -> FastAPI` (signature change: two new required parameters) — consumed by `api/app.py` (Task 6).

- [ ] **Step 1: Read the current files**

Read `scraper/src/autosmart24/api/main.py` and `scraper/tests/test_api.py` in full.

- [ ] **Step 2: Rewrite main.py**

Full replacement of `scraper/src/autosmart24/api/main.py`:

```python
from __future__ import annotations

import datetime as dt
import os
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import Session

from autosmart24.api.schemas import (
    AddBrandsRequest,
    ApplyDefaultsRequest,
    BrandCatalogEntryOut,
    BrandStatusOut,
    EventOut,
    RunOut,
    UpdateBrandRequest,
)
from autosmart24.config import BrandConfig
from autosmart24.db.models import BrandCatalog, ScrapeEvent, ScrapeRun, TrackedBrand
from autosmart24.scheduler import BrandScheduler
from autosmart24.scraping.brand_catalog import CatalogEntry

DEFAULT_CORS_ALLOW_ORIGINS = "http://localhost:5173"


def _to_brand_config(row: TrackedBrand) -> BrandConfig:
    return BrandConfig(slug=row.slug, make_id=row.make_id, display_name=row.display_name)


def _find_tracked_brand(session: Session, brand_slug: str) -> TrackedBrand:
    row = session.execute(select(TrackedBrand).where(TrackedBrand.slug == brand_slug)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Brand not tracked: {brand_slug}")
    return row


def _to_brand_status(session: Session, row: TrackedBrand) -> BrandStatusOut:
    last_run = session.execute(
        select(ScrapeRun).where(ScrapeRun.brand == row.display_name).order_by(ScrapeRun.started_at.desc()).limit(1)
    ).scalar_one_or_none()
    return BrandStatusOut(
        make_id=row.make_id,
        brand=row.display_name,
        slug=row.slug,
        paused=row.paused,
        year_from_years=row.year_from_years,
        schedule_day_of_week=row.schedule_day_of_week,
        schedule_hour=row.schedule_hour,
        schedule_minute=row.schedule_minute,
        last_run=RunOut.model_validate(last_run) if last_run else None,
    )


def create_app(
    session_factory,
    scheduler: BrandScheduler,
    run_now_fn: Callable[[BrandConfig], None],
    run_fn: Callable[[BrandConfig], None],
    refresh_catalog_fn: Callable[[], list[CatalogEntry]],
) -> FastAPI:
    app = FastAPI(title="AutoSmart24 Scraper API")

    allow_origins = [
        origin.strip()
        for origin in os.environ.get("CORS_ALLOW_ORIGINS", DEFAULT_CORS_ALLOW_ORIGINS).split(",")
        if origin.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["*"],
    )

    def get_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _reschedule(row: TrackedBrand) -> None:
        scheduler.schedule_brand(
            _to_brand_config(row),
            run_fn=run_fn,
            day_of_week=row.schedule_day_of_week,
            hour=row.schedule_hour,
            minute=row.schedule_minute,
        )
        if row.paused:
            scheduler.pause_brand(row.slug)

    @app.get("/brand-catalog", response_model=list[BrandCatalogEntryOut])
    def get_brand_catalog(session: Session = Depends(get_session)):
        rows = session.execute(select(BrandCatalog).order_by(BrandCatalog.display_name)).scalars().all()
        return [BrandCatalogEntryOut.model_validate(row) for row in rows]

    @app.post("/brand-catalog/refresh")
    def refresh_brand_catalog(session: Session = Depends(get_session)):
        entries = refresh_catalog_fn()
        now = dt.datetime.utcnow()
        for entry in entries:
            existing = session.get(BrandCatalog, entry.make_id)
            if existing is not None:
                existing.display_name = entry.display_name
                existing.slug = entry.slug
                existing.synced_at = now
            else:
                session.add(
                    BrandCatalog(make_id=entry.make_id, display_name=entry.display_name, slug=entry.slug, synced_at=now)
                )
        session.commit()
        return {"count": len(entries)}

    @app.get("/brands", response_model=list[BrandStatusOut])
    def list_brands(session: Session = Depends(get_session)):
        rows = session.execute(select(TrackedBrand)).scalars().all()
        return [_to_brand_status(session, row) for row in rows]

    @app.post("/brands/bulk", response_model=list[BrandStatusOut])
    def add_brands(body: AddBrandsRequest, session: Session = Depends(get_session)):
        now = dt.datetime.utcnow()
        touched: list[TrackedBrand] = []
        for make_id in body.make_ids:
            catalog_entry = session.get(BrandCatalog, make_id)
            if catalog_entry is None:
                raise HTTPException(status_code=400, detail=f"Unknown make_id in catalog: {make_id}")
            row = session.get(TrackedBrand, make_id)
            if row is None:
                row = TrackedBrand(
                    make_id=make_id,
                    slug=catalog_entry.slug,
                    display_name=catalog_entry.display_name,
                    paused=False,
                    year_from_years=body.year_from_years,
                    schedule_day_of_week=body.schedule_day_of_week,
                    schedule_hour=body.schedule_hour,
                    schedule_minute=body.schedule_minute,
                    created_at=now,
                )
                session.add(row)
                session.flush()
            _reschedule(row)
            touched.append(row)
        session.commit()
        return [_to_brand_status(session, row) for row in touched]

    @app.patch("/brands/apply-defaults", response_model=list[BrandStatusOut])
    def apply_defaults(body: ApplyDefaultsRequest, session: Session = Depends(get_session)):
        fields = body.model_fields_set
        rows = session.execute(select(TrackedBrand)).scalars().all()
        for row in rows:
            if "year_from_years" in fields:
                row.year_from_years = body.year_from_years
            if "schedule_day_of_week" in fields:
                row.schedule_day_of_week = body.schedule_day_of_week
            if "schedule_hour" in fields:
                row.schedule_hour = body.schedule_hour
            if "schedule_minute" in fields:
                row.schedule_minute = body.schedule_minute
            _reschedule(row)
        session.commit()
        return [_to_brand_status(session, row) for row in rows]

    @app.patch("/brands/{brand_slug}", response_model=BrandStatusOut)
    def update_brand(brand_slug: str, body: UpdateBrandRequest, session: Session = Depends(get_session)):
        row = _find_tracked_brand(session, brand_slug)
        fields = body.model_fields_set
        if "year_from_years" in fields:
            row.year_from_years = body.year_from_years
        if "schedule_day_of_week" in fields:
            row.schedule_day_of_week = body.schedule_day_of_week
        if "schedule_hour" in fields:
            row.schedule_hour = body.schedule_hour
        if "schedule_minute" in fields:
            row.schedule_minute = body.schedule_minute
        _reschedule(row)
        session.commit()
        return _to_brand_status(session, row)

    @app.delete("/brands/{brand_slug}")
    def delete_brand(brand_slug: str, session: Session = Depends(get_session)):
        row = _find_tracked_brand(session, brand_slug)
        scheduler.remove_brand_job(row.slug)
        session.delete(row)
        session.commit()
        return {"deleted": True}

    @app.get("/brands/{brand_slug}/runs", response_model=list[RunOut])
    def brand_runs(brand_slug: str, session: Session = Depends(get_session)):
        brand = _find_tracked_brand(session, brand_slug)
        rows = session.execute(
            select(ScrapeRun).where(ScrapeRun.brand == brand.display_name).order_by(ScrapeRun.started_at.desc())
        ).scalars().all()
        return [RunOut.model_validate(row) for row in rows]

    @app.get("/brands/{brand_slug}/events", response_model=list[EventOut])
    def brand_events(brand_slug: str, session: Session = Depends(get_session)):
        brand = _find_tracked_brand(session, brand_slug)
        rows = session.execute(
            select(ScrapeEvent).where(ScrapeEvent.brand == brand.display_name).order_by(ScrapeEvent.created_at.desc())
        ).scalars().all()
        return [EventOut.model_validate(row) for row in rows]

    @app.post("/brands/{brand_slug}/pause")
    def pause_brand(brand_slug: str, session: Session = Depends(get_session)):
        row = _find_tracked_brand(session, brand_slug)
        row.paused = True
        session.commit()
        scheduler.pause_brand(brand_slug)
        return {"paused": True}

    @app.post("/brands/{brand_slug}/resume")
    def resume_brand(brand_slug: str, session: Session = Depends(get_session)):
        row = _find_tracked_brand(session, brand_slug)
        row.paused = False
        session.commit()
        scheduler.resume_brand(brand_slug)
        return {"paused": False}

    @app.post("/brands/{brand_slug}/run-now")
    def run_now(brand_slug: str, session: Session = Depends(get_session)):
        row = _find_tracked_brand(session, brand_slug)
        run_now_fn(_to_brand_config(row))
        return {"triggered": True}

    return app
```

- [ ] **Step 3: Rewrite test_api.py**

Full replacement of `scraper/tests/test_api.py`:

```python
import datetime as dt

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi.testclient import TestClient

from autosmart24.api.main import create_app
from autosmart24.db.models import BrandCatalog, ScrapeRun, TrackedBrand
from autosmart24.scheduler import BrandScheduler
from autosmart24.scraping.brand_catalog import CatalogEntry

FIAT_CATALOG = CatalogEntry(make_id=28, display_name="Fiat", slug="fiat")
BMW_CATALOG = CatalogEntry(make_id=13, display_name="BMW", slug="bmw")


def _seed_catalog(db_session, entries=(FIAT_CATALOG, BMW_CATALOG)):
    now = dt.datetime.utcnow()
    for entry in entries:
        db_session.add(BrandCatalog(make_id=entry.make_id, display_name=entry.display_name, slug=entry.slug, synced_at=now))
    db_session.commit()


def _seed_tracked(db_session, entry=FIAT_CATALOG, **overrides):
    defaults = dict(
        make_id=entry.make_id, slug=entry.slug, display_name=entry.display_name,
        paused=False, year_from_years=None, schedule_day_of_week=None,
        schedule_hour=3, schedule_minute=0, created_at=dt.datetime.utcnow(),
    )
    defaults.update(overrides)
    row = TrackedBrand(**defaults)
    db_session.add(row)
    db_session.commit()
    return row


def _app_with_session(db_session, run_now_fn=None, run_fn=None, refresh_catalog_fn=None):
    scheduler = BrandScheduler(BackgroundScheduler())
    for row in db_session.query(TrackedBrand).all():
        scheduler.schedule_brand(row, run_fn=lambda brand: None, hour=row.schedule_hour, minute=row.schedule_minute)

    app = create_app(
        session_factory=lambda: db_session,
        scheduler=scheduler,
        run_now_fn=run_now_fn or (lambda brand: None),
        run_fn=run_fn or (lambda brand: None),
        refresh_catalog_fn=refresh_catalog_fn or (lambda: []),
    )
    return app, scheduler


def test_list_brands_returns_all_tracked_brands_with_slug(db_session):
    _seed_catalog(db_session)
    _seed_tracked(db_session, FIAT_CATALOG)
    _seed_tracked(db_session, BMW_CATALOG)

    app, _ = _app_with_session(db_session)
    client = TestClient(app)

    response = client.get("/brands")

    assert response.status_code == 200
    slugs = {row["slug"] for row in response.json()}
    assert slugs == {"fiat", "bmw"}


def test_list_brands_reports_last_run(db_session):
    _seed_catalog(db_session)
    _seed_tracked(db_session, FIAT_CATALOG)
    now = dt.datetime.utcnow()
    db_session.add(ScrapeRun(brand="Fiat", started_at=now, finished_at=now, status="success"))
    db_session.commit()

    app, _ = _app_with_session(db_session)
    client = TestClient(app)

    response = client.get("/brands")
    fiat_row = next(row for row in response.json() if row["slug"] == "fiat")
    assert fiat_row["last_run"]["status"] == "success"


def test_list_brands_includes_year_and_schedule(db_session):
    _seed_catalog(db_session)
    _seed_tracked(db_session, FIAT_CATALOG, year_from_years=5, schedule_day_of_week="mon", schedule_hour=4, schedule_minute=30)

    app, _ = _app_with_session(db_session)
    client = TestClient(app)

    fiat_row = next(row for row in client.get("/brands").json() if row["slug"] == "fiat")
    assert fiat_row["year_from_years"] == 5
    assert fiat_row["schedule_day_of_week"] == "mon"
    assert fiat_row["schedule_hour"] == 4
    assert fiat_row["schedule_minute"] == 30


def test_get_brand_catalog_returns_seeded_entries(db_session):
    _seed_catalog(db_session)

    app, _ = _app_with_session(db_session)
    client = TestClient(app)

    response = client.get("/brand-catalog")

    assert response.status_code == 200
    slugs = {row["slug"] for row in response.json()}
    assert slugs == {"fiat", "bmw"}


def test_refresh_brand_catalog_upserts_entries(db_session):
    called = []

    def fake_refresh():
        called.append(1)
        return [FIAT_CATALOG, BMW_CATALOG]

    app, _ = _app_with_session(db_session, refresh_catalog_fn=fake_refresh)
    client = TestClient(app)

    response = client.post("/brand-catalog/refresh")

    assert response.status_code == 200
    assert response.json() == {"count": 2}
    assert called == [1]
    assert {row["slug"] for row in client.get("/brand-catalog").json()} == {"fiat", "bmw"}


def test_add_brands_bulk_creates_tracked_rows_and_schedules_jobs(db_session):
    _seed_catalog(db_session)

    app, scheduler = _app_with_session(db_session)
    client = TestClient(app)

    response = client.post(
        "/brands/bulk",
        json={"make_ids": [28, 13], "year_from_years": 5, "schedule_hour": 4, "schedule_minute": 15},
    )

    assert response.status_code == 200
    body = response.json()
    assert {row["slug"] for row in body} == {"fiat", "bmw"}
    assert all(row["year_from_years"] == 5 for row in body)
    assert scheduler.scheduler.get_job("fiat") is not None
    assert scheduler.scheduler.get_job("bmw") is not None


def test_add_brands_bulk_rejects_unknown_make_id(db_session):
    _seed_catalog(db_session)

    app, _ = _app_with_session(db_session)
    client = TestClient(app)

    response = client.post("/brands/bulk", json={"make_ids": [999999]})

    assert response.status_code == 400


def test_update_brand_patches_only_provided_fields(db_session):
    _seed_catalog(db_session)
    _seed_tracked(db_session, FIAT_CATALOG, year_from_years=5, schedule_hour=3, schedule_minute=0)

    app, _ = _app_with_session(db_session)
    client = TestClient(app)

    response = client.patch("/brands/fiat", json={"schedule_hour": 7})

    assert response.status_code == 200
    body = response.json()
    assert body["schedule_hour"] == 7
    assert body["year_from_years"] == 5  # untouched, since it was not in the request body


def test_update_brand_can_explicitly_clear_year_filter(db_session):
    _seed_catalog(db_session)
    _seed_tracked(db_session, FIAT_CATALOG, year_from_years=5)

    app, _ = _app_with_session(db_session)
    client = TestClient(app)

    response = client.patch("/brands/fiat", json={"year_from_years": None})

    assert response.status_code == 200
    assert response.json()["year_from_years"] is None


def test_apply_defaults_overwrites_all_tracked_brands(db_session):
    _seed_catalog(db_session)
    _seed_tracked(db_session, FIAT_CATALOG, year_from_years=5)
    _seed_tracked(db_session, BMW_CATALOG, year_from_years=10)

    app, _ = _app_with_session(db_session)
    client = TestClient(app)

    response = client.patch("/brands/apply-defaults", json={"year_from_years": 3, "schedule_hour": 2, "schedule_minute": 0})

    assert response.status_code == 200
    body = response.json()
    assert all(row["year_from_years"] == 3 for row in body)
    assert all(row["schedule_hour"] == 2 for row in body)


def test_delete_brand_removes_row_and_job(db_session):
    _seed_catalog(db_session)
    _seed_tracked(db_session, FIAT_CATALOG)

    app, scheduler = _app_with_session(db_session)
    client = TestClient(app)

    response = client.delete("/brands/fiat")

    assert response.status_code == 200
    assert client.get("/brands/fiat/runs").status_code == 404
    assert scheduler.scheduler.get_job("fiat") is None


def test_pause_and_resume_brand_via_api(db_session):
    _seed_catalog(db_session)
    _seed_tracked(db_session, FIAT_CATALOG)

    app, scheduler = _app_with_session(db_session)
    client = TestClient(app)

    response = client.post("/brands/fiat/pause")
    assert response.status_code == 200
    assert scheduler.is_paused("fiat") is True

    response = client.post("/brands/fiat/resume")
    assert response.status_code == 200
    assert scheduler.is_paused("fiat") is False


def test_pause_persists_to_the_tracked_brand_row(db_session):
    _seed_catalog(db_session)
    _seed_tracked(db_session, FIAT_CATALOG)

    app, _ = _app_with_session(db_session)
    client = TestClient(app)

    client.post("/brands/fiat/pause")

    row = db_session.get(TrackedBrand, 28)
    assert row.paused is True


def test_run_now_triggers_callback(db_session):
    _seed_catalog(db_session)
    _seed_tracked(db_session, FIAT_CATALOG)
    triggered = []

    app, _ = _app_with_session(db_session, run_now_fn=lambda brand: triggered.append(brand.slug))
    client = TestClient(app)

    response = client.post("/brands/fiat/run-now")

    assert response.status_code == 200
    assert triggered == ["fiat"]


def test_unknown_brand_returns_404(db_session):
    app, _ = _app_with_session(db_session)
    client = TestClient(app)

    response = client.get("/brands/unknown-brand/runs")
    assert response.status_code == 404


def test_cors_header_present_for_allowed_origin(db_session):
    app, _ = _app_with_session(db_session)
    client = TestClient(app)

    response = client.get("/brands", headers={"Origin": "http://localhost:5173"})

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"
```

Note `_app_with_session`'s bootstrap loop calls `scheduler.schedule_brand(row, ...)` passing the `TrackedBrand` ORM row directly where a `BrandConfig` is expected — this works because `BrandScheduler.schedule_brand` only reads `brand.slug` (for the job id and to pass as the job's `args`), and a `TrackedBrand` row has that attribute too via duck typing. This is acceptable for test bootstrap convenience; production code (`api/app.py`, Task 6) always converts explicitly via `_to_brand_config`/`BrandConfig(...)` for clarity and to avoid passing a live ORM instance into a background job's closure.

- [ ] **Step 4: Run to confirm pass**

Run: `cd scraper && pytest tests/test_api.py -v`
Expected: all tests pass (17 total: 3 pre-existing unchanged in intent + updates + new coverage for catalog/bulk/patch/apply-defaults/delete/pause-persistence).

- [ ] **Step 5: Run the full backend suite**

Run: `cd scraper && python -m pytest -q`
Expected: all pass. `api/app.py` still calls `create_app` with the OLD signature at this point — Task 6 fixes that. No test currently imports `api/app.py` directly (confirmed in earlier work this session), so this is expected and not a regression to chase down now.

- [ ] **Step 6: Commit**

```bash
git add scraper/src/autosmart24/api/main.py scraper/tests/test_api.py
git commit -m "Add brand-catalog and tracked-brand-management endpoints; read brands from the database"
```

---

## Task 6: Wire the database-driven scheduler into `api/app.py`

**Files:**
- Modify: `scraper/src/autosmart24/api/app.py`
- Modify: `.env.example`
- Modify: `docker-compose.yml`

**Interfaces:**
- Consumes: everything from Tasks 1-5.

- [ ] **Step 1: Read the current file**

Read `scraper/src/autosmart24/api/app.py` in full.

- [ ] **Step 2: Rewrite app.py**

Full replacement:

```python
from __future__ import annotations

import datetime as dt
import logging
import os
import time

from sqlalchemy import select

from autosmart24.api.main import create_app
from autosmart24.config import BrandConfig, MVP_BRANDS
from autosmart24.db.models import BrandCatalog, ScrapeEvent, TrackedBrand
from autosmart24.db.session import make_engine, make_session_factory
from autosmart24.run_manager import run_brand_sweep
from autosmart24.scheduler import BrandRunGuard, BrandScheduler
from autosmart24.scraping.brand_catalog import fetch_brand_catalog
from autosmart24.scraping.http_client import make_client
from autosmart24.scraping.rate_control import BlockRateTracker

logger = logging.getLogger(__name__)

MIN_DELAY_SECONDS = float(os.environ.get("SCRAPE_MIN_DELAY_SECONDS", "3"))
MAX_DELAY_SECONDS = float(os.environ.get("SCRAPE_MAX_DELAY_SECONDS", "8"))
CONCURRENCY = max(1, int(os.environ.get("SCRAPE_CONCURRENCY", "6")))
SESSION_REFRESH_REQUESTS = max(1, int(os.environ.get("SCRAPE_SESSION_REFRESH_REQUESTS", "30")))
# Used only to seed the initial per-brand year filter on first startup (see
# _seed_tracked_brands_if_empty) -- after that, each brand's own
# year_from_years column in tracked_brands is authoritative, editable from
# the dashboard.
SEED_MAX_LISTING_AGE_YEARS = int(os.environ.get("SCRAPE_MAX_LISTING_AGE_YEARS", "5"))

engine = make_engine()
session_factory = make_session_factory(engine)


def _on_backoff_change(multiplier: float) -> None:
    """Surface adaptive-backoff transitions on the dashboard, which is this
    project's only monitoring channel."""
    if multiplier > 1.0:
        message = f"Adaptive backoff engaged: request delays multiplied by {multiplier}"
    else:
        message = "Adaptive backoff released: request delays back to normal"
    logger.warning(message)
    session = session_factory()
    try:
        session.add(
            ScrapeEvent(
                run_id=None, brand=None, level="warning",
                message=message, url=None, created_at=dt.datetime.utcnow(),
            )
        )
        session.commit()
    except Exception:
        logger.exception("Failed to record backoff event")
        session.rollback()
    finally:
        session.close()


rate_controller = BlockRateTracker(on_backoff_change=_on_backoff_change)


def _client_factory():
    return make_client(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS, rate_controller=rate_controller)


scheduler = BrandScheduler()
run_guard = BrandRunGuard()


def _run_fn(brand: BrandConfig) -> None:
    if not run_guard.try_acquire(brand.slug):
        logger.warning("Skipping sweep for brand %s: a sweep is already in progress", brand.slug)
        return
    try:
        session = session_factory()
        try:
            tracked = session.execute(
                select(TrackedBrand).where(TrackedBrand.slug == brand.slug)
            ).scalar_one_or_none()
            year_from = None
            if tracked is not None and tracked.year_from_years is not None:
                year_from = dt.date.today().year - tracked.year_from_years
            run_brand_sweep(
                session, _client_factory, brand,
                concurrency=CONCURRENCY, year_from=year_from, session_refresh_requests=SESSION_REFRESH_REQUESTS,
            )
        finally:
            session.close()
    finally:
        run_guard.release(brand.slug)


def _run_now_fn(brand: BrandConfig) -> None:
    scheduler.scheduler.add_job(_run_fn, args=[brand], trigger="date", id=f"manual-{brand.slug}-{int(time.time())}")


def _refresh_catalog_fn():
    client = _client_factory()
    try:
        return fetch_brand_catalog(client)
    finally:
        client.close()


app = create_app(
    session_factory=session_factory,
    scheduler=scheduler,
    run_now_fn=_run_now_fn,
    run_fn=_run_fn,
    refresh_catalog_fn=_refresh_catalog_fn,
)


def _seed_tracked_brands_if_empty(session) -> None:
    """Preserve today's 5-brand behavior on first startup after this feature
    ships. Daily-at-03:00 is not equivalent to the old SCRAPE_INTERVAL_DAYS=4
    -- interval-days and day/hour scheduling are different paradigms with no
    faithful conversion -- this is a disclosed, one-time default the user can
    change immediately from the dashboard."""
    already_seeded = session.execute(select(TrackedBrand.make_id)).first()
    if already_seeded is not None:
        return
    now = dt.datetime.utcnow()
    for brand in MVP_BRANDS:
        if session.get(BrandCatalog, brand.make_id) is None:
            session.add(
                BrandCatalog(make_id=brand.make_id, display_name=brand.display_name, slug=brand.slug, synced_at=now)
            )
        session.add(
            TrackedBrand(
                make_id=brand.make_id, slug=brand.slug, display_name=brand.display_name,
                paused=False, year_from_years=SEED_MAX_LISTING_AGE_YEARS,
                schedule_day_of_week=None, schedule_hour=3, schedule_minute=0, created_at=now,
            )
        )
    session.commit()


@app.on_event("startup")
def _start_scheduler():
    session = session_factory()
    try:
        _seed_tracked_brands_if_empty(session)
        rows = session.execute(select(TrackedBrand)).scalars().all()
        for row in rows:
            brand = BrandConfig(slug=row.slug, make_id=row.make_id, display_name=row.display_name)
            scheduler.schedule_brand(
                brand, run_fn=_run_fn,
                day_of_week=row.schedule_day_of_week, hour=row.schedule_hour, minute=row.schedule_minute,
            )
            if row.paused:
                scheduler.pause_brand(row.slug)
    finally:
        session.close()
    scheduler.start()


@app.on_event("shutdown")
def _stop_scheduler():
    scheduler.shutdown()
```

`SCRAPE_INTERVAL_DAYS` is gone — scheduling is now per-brand day/hour/minute stored in the database, not a single global interval.

- [ ] **Step 3: Update the app-wiring smoke test**

The current `scraper/tests/test_app_wiring.py` has 3 tests: `test_app_module_imports_successfully`, `test_client_factory_is_a_factory_not_an_instance` (both keep working unchanged — they don't touch `_year_from`/`MAX_LISTING_AGE_YEARS`), and `test_year_from_uses_configured_max_listing_age`, which asserts on `imported_app_module.MAX_LISTING_AGE_YEARS`/`._year_from()` — both removed by this task's `app.py` rewrite (year filtering is now per-brand, read from the database inside `_run_fn`, not a module-level global). Full replacement of `scraper/tests/test_app_wiring.py`:

```python
"""Smoke tests for the production entrypoint `autosmart24.api.app`.

`api/app.py` builds a real SQLAlchemy engine at *module import time*, so it
cannot be imported like a normal module in the test suite -- doing so would
either require a live DATABASE_URL or blow up. We work around this by
pointing DATABASE_URL at an in-memory SQLite database before import and
importing the module fresh via importlib, popping it back out of
sys.modules afterwards so no other test in the suite (which may run in a
different order) observes a module that was built against this test's
environment variables.

These tests exist because api/app.py previously had zero coverage: it kept
passing a `RateLimitedClient` *instance* to `run_brand_sweep` for four tasks
after the function was changed to expect a zero-argument *client factory*
callable, which broke every production sweep with
`TypeError: 'RateLimitedClient' object is not callable`. The
test_client_factory_is_a_factory_not_an_instance test below is written
specifically to catch that regression again.
"""

from __future__ import annotations

import importlib
import sys

import pytest
from sqlalchemy import select

MODULE_NAME = "autosmart24.api.app"


@pytest.fixture()
def imported_app_module(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("SCRAPE_MAX_LISTING_AGE_YEARS", "5")

    # Force a fresh execution of the module body (which reads the env vars
    # above) rather than reusing a cached import from elsewhere.
    previous = sys.modules.pop(MODULE_NAME, None)
    module = importlib.import_module(MODULE_NAME)
    try:
        yield module
    finally:
        sys.modules.pop(MODULE_NAME, None)
        if previous is not None:
            sys.modules[MODULE_NAME] = previous


def test_app_module_imports_successfully(imported_app_module):
    assert imported_app_module.app is not None


def test_client_factory_is_a_factory_not_an_instance(imported_app_module):
    from autosmart24.scraping.http_client import RateLimitedClient

    factory = imported_app_module._client_factory
    assert callable(factory)

    # This is the precise regression that broke production: at HEAD before
    # this fix, `_client_factory` would have been a `RateLimitedClient`
    # instance (no `__call__` method), so calling it here would raise
    # `TypeError: 'RateLimitedClient' object is not callable` -- the same
    # error `run_brand_sweep` hit the moment a sweep started.
    client = factory()
    try:
        assert isinstance(client, RateLimitedClient)
    finally:
        client.close()

    # Each call must build an independent client, not return a shared
    # singleton -- that's the whole point of passing a factory instead of
    # an instance to run_brand_sweep.
    other = factory()
    try:
        assert other is not client
    finally:
        other.close()


def test_seeds_tracked_brands_from_mvp_brands_on_first_startup(imported_app_module):
    from autosmart24.db.models import TrackedBrand
    from autosmart24.db.session import init_db

    module = imported_app_module
    init_db(module.engine)
    session = module.session_factory()
    try:
        module._seed_tracked_brands_if_empty(session)
        rows = session.execute(select(TrackedBrand)).scalars().all()
        assert {row.slug for row in rows} == {"fiat", "volkswagen", "bmw", "audi", "mercedes-benz"}
        assert all(row.year_from_years == module.SEED_MAX_LISTING_AGE_YEARS for row in rows)

        module._seed_tracked_brands_if_empty(session)  # must be idempotent
        rows_again = session.execute(select(TrackedBrand)).scalars().all()
        assert len(rows_again) == 5
    finally:
        session.close()


def test_seed_is_skipped_when_tracked_brands_already_populated(imported_app_module):
    import datetime as dt

    from autosmart24.db.models import BrandCatalog, TrackedBrand
    from autosmart24.db.session import init_db

    module = imported_app_module
    init_db(module.engine)
    session = module.session_factory()
    try:
        now = dt.datetime.utcnow()
        session.add(BrandCatalog(make_id=999, display_name="Custom", slug="custom", synced_at=now))
        session.add(
            TrackedBrand(
                make_id=999, slug="custom", display_name="Custom", paused=False,
                year_from_years=None, schedule_day_of_week=None, schedule_hour=3, schedule_minute=0,
                created_at=now,
            )
        )
        session.commit()

        module._seed_tracked_brands_if_empty(session)

        rows = session.execute(select(TrackedBrand)).scalars().all()
        assert {row.slug for row in rows} == {"custom"}  # MVP_BRANDS was NOT seeded on top
    finally:
        session.close()
```

- [ ] **Step 4: Run to confirm pass**

Run: `cd scraper && DATABASE_URL=sqlite:///:memory: python -c "import autosmart24.api.app; print('ok')"`
Expected: prints `ok`.

Run: `cd scraper && pytest tests/test_app_wiring.py -v`
Expected: `4 passed`.

- [ ] **Step 5: Run the full backend suite**

Run: `cd scraper && python -m pytest -q`
Expected: all pass, no regressions.

- [ ] **Step 6: Update .env.example and docker-compose.yml**

`.env.example` — remove the now-unused `SCRAPE_INTERVAL_DAYS` line (scheduling is per-brand now, stored in the database, not an env var); keep `SCRAPE_MAX_LISTING_AGE_YEARS` (still used as the one-time seed default). Full replacement:

```
DATABASE_URL=postgresql+psycopg://autosmart24:autosmart24@localhost:5434/autosmart24
SCRAPE_MIN_DELAY_SECONDS=3
SCRAPE_MAX_DELAY_SECONDS=8
SCRAPE_CONCURRENCY=6
SCRAPE_MAX_LISTING_AGE_YEARS=5
SCRAPE_SESSION_REFRESH_REQUESTS=30
VITE_API_BASE_URL=http://localhost:8001
```

In `docker-compose.yml`, remove the `SCRAPE_INTERVAL_DAYS: "4"` line from the `app:` service's `environment:` block (keep everything else there unchanged).

- [ ] **Step 7: Validate the compose file**

Run: `cd "C:\App AI\Autoscout" && docker compose config --quiet`
Expected: no output, exit code 0.

- [ ] **Step 8: Commit**

```bash
git add scraper/src/autosmart24/api/app.py scraper/tests/test_app_wiring.py .env.example docker-compose.yml
git commit -m "Wire database-driven scheduler startup, seeding, and per-brand year lookup into app.py"
```

---

## Task 7: Frontend types and API client additions

**Files:**
- Modify: `dashboard/src/types.ts`
- Modify: `dashboard/src/api.ts`

**Interfaces:**
- Produces: extended `BrandStatusOut` (adds `make_id: number`, `year_from_years: number | null`, `schedule_day_of_week: string | null`, `schedule_hour: number`, `schedule_minute: number`), new `BrandCatalogEntryOut` interface, and `fetchBrandCatalog`, `refreshBrandCatalog`, `addBrands`, `updateBrand`, `applyDefaultsToAllBrands`, `removeBrand` functions in `api.ts` — consumed by `ManageBrands.tsx` (Task 8) and `App.tsx` (Task 9).

- [ ] **Step 1: Read the current files**

Read `dashboard/src/types.ts` and `dashboard/src/api.ts` in full.

- [ ] **Step 2: Update types.ts**

Full replacement:

```ts
export interface RunOut {
  id: number;
  brand: string;
  started_at: string;
  finished_at: string | null;
  status: string;
  listings_seen: number;
  new_listings: number;
  price_changes: number;
  sold_detected: number;
  errors_count: number;
}

export interface EventOut {
  id: number;
  run_id: number | null;
  brand: string | null;
  level: string;
  message: string;
  url: string | null;
  created_at: string;
}

export interface BrandStatusOut {
  make_id: number;
  brand: string;
  slug: string;
  paused: boolean;
  year_from_years: number | null;
  schedule_day_of_week: string | null;
  schedule_hour: number;
  schedule_minute: number;
  last_run: RunOut | null;
}

export interface BrandCatalogEntryOut {
  make_id: number;
  display_name: string;
  slug: string;
}

export interface BrandDefaultsPatch {
  year_from_years?: number | null;
  schedule_day_of_week?: string | null;
  schedule_hour?: number;
  schedule_minute?: number;
}
```

- [ ] **Step 3: Update api.ts**

Full replacement:

```ts
import type { BrandCatalogEntryOut, BrandDefaultsPatch, BrandStatusOut, EventOut, RunOut } from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`);
  if (!response.ok) {
    throw new Error(`Request to ${path} failed with status ${response.status}`);
  }
  return response.json() as Promise<T>;
}

async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(`Request to ${path} failed with status ${response.status}`);
  }
  return response.json() as Promise<T>;
}

async function patchJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(`Request to ${path} failed with status ${response.status}`);
  }
  return response.json() as Promise<T>;
}

async function deleteJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, { method: "DELETE" });
  if (!response.ok) {
    throw new Error(`Request to ${path} failed with status ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function fetchBrands(): Promise<BrandStatusOut[]> {
  return getJson<BrandStatusOut[]>("/brands");
}

export function fetchBrandRuns(brandSlug: string): Promise<RunOut[]> {
  return getJson<RunOut[]>(`/brands/${brandSlug}/runs`);
}

export function fetchBrandEvents(brandSlug: string): Promise<EventOut[]> {
  return getJson<EventOut[]>(`/brands/${brandSlug}/events`);
}

export function pauseBrand(brandSlug: string): Promise<{ paused: boolean }> {
  return postJson(`/brands/${brandSlug}/pause`);
}

export function resumeBrand(brandSlug: string): Promise<{ paused: boolean }> {
  return postJson(`/brands/${brandSlug}/resume`);
}

export function runBrandNow(brandSlug: string): Promise<{ triggered: boolean }> {
  return postJson(`/brands/${brandSlug}/run-now`);
}

export function fetchBrandCatalog(): Promise<BrandCatalogEntryOut[]> {
  return getJson<BrandCatalogEntryOut[]>("/brand-catalog");
}

export function refreshBrandCatalog(): Promise<{ count: number }> {
  return postJson("/brand-catalog/refresh");
}

export function addBrands(makeIds: number[], defaults: BrandDefaultsPatch): Promise<BrandStatusOut[]> {
  return postJson("/brands/bulk", { make_ids: makeIds, ...defaults });
}

export function updateBrand(brandSlug: string, patch: BrandDefaultsPatch): Promise<BrandStatusOut> {
  return patchJson(`/brands/${brandSlug}`, patch);
}

export function applyDefaultsToAllBrands(patch: BrandDefaultsPatch): Promise<BrandStatusOut[]> {
  return patchJson("/brands/apply-defaults", patch);
}

export function removeBrand(brandSlug: string): Promise<{ deleted: boolean }> {
  return deleteJson(`/brands/${brandSlug}`);
}
```

`postJson` gains an optional `body` parameter (backward compatible — every existing call site passes none, matching current behavior of a bodyless POST).

- [ ] **Step 4: Run the dashboard test suite**

Run: `cd dashboard && npx vitest run`
Expected: all pass (existing `BrandCard`/`BrandDetail` tests use fixture objects shaped like the old, narrower `BrandStatusOut` — TypeScript structural typing means extra required fields on the interface WILL break those fixtures at compile time; fix them now by adding the five new fields to every `BrandStatusOut`-shaped literal in `BrandCard.test.tsx` — read that file and add `make_id: 28, year_from_years: null, schedule_day_of_week: null, schedule_hour: 3, schedule_minute: 0` to the existing `brand` fixture object, matching its current field style).

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/types.ts dashboard/src/api.ts dashboard/src/components/BrandCard.test.tsx
git commit -m "Extend BrandStatusOut and add brand-catalog/tracked-brand-management API client functions"
```

---

## Task 8: `ManageBrands` component

**Files:**
- Create: `dashboard/src/components/ManageBrands.tsx`
- Create: `dashboard/src/components/ManageBrands.test.tsx`
- Modify: `dashboard/src/index.css`

**Interfaces:**
- Consumes: `fetchBrandCatalog`, `refreshBrandCatalog`, `addBrands`, `updateBrand`, `applyDefaultsToAllBrands`, `removeBrand` (Task 7).
- Produces: `<ManageBrands trackedBrands: BrandStatusOut[], onBrandsChanged: () => void>` — consumed by `App.tsx` (Task 9).

- [ ] **Step 1: Write the failing tests**

`dashboard/src/components/ManageBrands.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ManageBrands } from "./ManageBrands";
import type { BrandStatusOut } from "../types";
import * as api from "../api";

vi.mock("../api");

const trackedFiat: BrandStatusOut = {
  make_id: 28, brand: "Fiat", slug: "fiat", paused: false,
  year_from_years: 5, schedule_day_of_week: null, schedule_hour: 3, schedule_minute: 0,
  last_run: null,
};

describe("ManageBrands", () => {
  it("loads and displays the catalog, excluding already-tracked brands", async () => {
    vi.mocked(api.fetchBrandCatalog).mockResolvedValue([
      { make_id: 28, display_name: "Fiat", slug: "fiat" },
      { make_id: 13, display_name: "BMW", slug: "bmw" },
    ]);

    render(<ManageBrands trackedBrands={[trackedFiat]} onBrandsChanged={vi.fn()} />);

    await waitFor(() => expect(screen.getByText("BMW")).toBeInTheDocument());
    expect(screen.queryByText("Fiat", { selector: "li *" })).not.toBeInTheDocument();
  });

  it("filters the catalog by search text", async () => {
    vi.mocked(api.fetchBrandCatalog).mockResolvedValue([
      { make_id: 13, display_name: "BMW", slug: "bmw" },
      { make_id: 6, display_name: "Alfa Romeo", slug: "alfa-romeo" },
    ]);

    render(<ManageBrands trackedBrands={[]} onBrandsChanged={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("BMW")).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText("Cerca marca..."), { target: { value: "alfa" } });

    expect(screen.queryByText("BMW")).not.toBeInTheDocument();
    expect(screen.getByText("Alfa Romeo")).toBeInTheDocument();
  });

  it("adds selected brands with the current defaults", async () => {
    vi.mocked(api.fetchBrandCatalog).mockResolvedValue([{ make_id: 13, display_name: "BMW", slug: "bmw" }]);
    vi.mocked(api.addBrands).mockResolvedValue([]);
    const onBrandsChanged = vi.fn();

    render(<ManageBrands trackedBrands={[]} onBrandsChanged={onBrandsChanged} />);
    await waitFor(() => expect(screen.getByText("BMW")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByText(/Aggiungi selezionate/));

    await waitFor(() => expect(api.addBrands).toHaveBeenCalledWith([13], {
      year_from_years: 5, schedule_day_of_week: null, schedule_hour: 3, schedule_minute: 0,
    }));
    expect(onBrandsChanged).toHaveBeenCalled();
  });

  it("applies defaults to all tracked brands", async () => {
    vi.mocked(api.fetchBrandCatalog).mockResolvedValue([]);
    vi.mocked(api.applyDefaultsToAllBrands).mockResolvedValue([]);
    const onBrandsChanged = vi.fn();

    render(<ManageBrands trackedBrands={[trackedFiat]} onBrandsChanged={onBrandsChanged} />);

    fireEvent.click(screen.getByText(/Applica a tutte le marche monitorate/));

    await waitFor(() =>
      expect(api.applyDefaultsToAllBrands).toHaveBeenCalledWith({
        year_from_years: 5, schedule_day_of_week: null, schedule_hour: 3, schedule_minute: 0,
      })
    );
    expect(onBrandsChanged).toHaveBeenCalled();
  });

  it("saves an individual tracked brand's edited year and schedule", async () => {
    vi.mocked(api.fetchBrandCatalog).mockResolvedValue([]);
    vi.mocked(api.updateBrand).mockResolvedValue(trackedFiat);
    const onBrandsChanged = vi.fn();

    render(<ManageBrands trackedBrands={[trackedFiat]} onBrandsChanged={onBrandsChanged} />);

    fireEvent.change(screen.getByLabelText("Anno Fiat"), { target: { value: "10" } });
    fireEvent.click(screen.getByTestId("tracked-brand-fiat").querySelector("button")!);

    await waitFor(() =>
      expect(api.updateBrand).toHaveBeenCalledWith("fiat", {
        year_from_years: 10, schedule_day_of_week: null, schedule_hour: 3, schedule_minute: 0,
      })
    );
    expect(onBrandsChanged).toHaveBeenCalled();
  });

  it("removes a tracked brand", async () => {
    vi.mocked(api.fetchBrandCatalog).mockResolvedValue([]);
    vi.mocked(api.removeBrand).mockResolvedValue({ deleted: true });
    const onBrandsChanged = vi.fn();

    render(<ManageBrands trackedBrands={[trackedFiat]} onBrandsChanged={onBrandsChanged} />);

    fireEvent.click(screen.getByText("Rimuovi"));

    await waitFor(() => expect(api.removeBrand).toHaveBeenCalledWith("fiat"));
    expect(onBrandsChanged).toHaveBeenCalled();
  });

  it("refreshes the catalog and reloads the list", async () => {
    vi.mocked(api.fetchBrandCatalog).mockResolvedValueOnce([]).mockResolvedValueOnce([
      { make_id: 13, display_name: "BMW", slug: "bmw" },
    ]);
    vi.mocked(api.refreshBrandCatalog).mockResolvedValue({ count: 1 });

    render(<ManageBrands trackedBrands={[]} onBrandsChanged={vi.fn()} />);

    fireEvent.click(screen.getByText("Aggiorna catalogo"));

    await waitFor(() => expect(screen.getByText("BMW")).toBeInTheDocument());
    expect(api.refreshBrandCatalog).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd dashboard && npx vitest run src/components/ManageBrands.test.tsx`
Expected: FAIL — `Cannot find module './ManageBrands'`

- [ ] **Step 3: Implement ManageBrands.tsx**

`dashboard/src/components/ManageBrands.tsx`:

```tsx
import { useEffect, useState } from "react";
import {
  addBrands,
  applyDefaultsToAllBrands,
  fetchBrandCatalog,
  refreshBrandCatalog,
  removeBrand,
  updateBrand,
} from "../api";
import type { BrandCatalogEntryOut, BrandDefaultsPatch, BrandStatusOut } from "../types";

interface ManageBrandsProps {
  trackedBrands: BrandStatusOut[];
  onBrandsChanged: () => void;
}

const DAYS: { value: string; label: string }[] = [
  { value: "", label: "Ogni giorno" },
  { value: "mon", label: "Lunedì" },
  { value: "tue", label: "Martedì" },
  { value: "wed", label: "Mercoledì" },
  { value: "thu", label: "Giovedì" },
  { value: "fri", label: "Venerdì" },
  { value: "sat", label: "Sabato" },
  { value: "sun", label: "Domenica" },
];

function dayLabel(day: string | null): string {
  return DAYS.find((d) => d.value === (day ?? ""))?.label ?? "Ogni giorno";
}

interface TrackedBrandRowProps {
  brand: BrandStatusOut;
  onSave: (slug: string, patch: BrandDefaultsPatch) => void;
  onRemove: (slug: string) => void;
}

function TrackedBrandRow({ brand, onSave, onRemove }: TrackedBrandRowProps) {
  const [year, setYear] = useState(brand.year_from_years === null ? "" : String(brand.year_from_years));
  const [day, setDay] = useState(brand.schedule_day_of_week ?? "");
  const [hour, setHour] = useState(String(brand.schedule_hour));
  const [minute, setMinute] = useState(String(brand.schedule_minute));

  function handleSave() {
    onSave(brand.slug, {
      year_from_years: year === "" ? null : Number(year),
      schedule_day_of_week: day === "" ? null : day,
      schedule_hour: Number(hour),
      schedule_minute: Number(minute),
    });
  }

  return (
    <li data-testid={`tracked-brand-${brand.slug}`}>
      <span>{brand.brand}</span>
      <label>
        Anno {brand.brand}
        <input
          type="number"
          min={0}
          aria-label={`Anno ${brand.brand}`}
          value={year}
          onChange={(e) => setYear(e.target.value)}
        />
      </label>
      <select aria-label={`Giorno ${brand.brand}`} value={day} onChange={(e) => setDay(e.target.value)}>
        {DAYS.map((d) => (
          <option key={d.value} value={d.value}>{d.label}</option>
        ))}
      </select>
      <input type="number" min={0} max={23} aria-label={`Ora ${brand.brand}`} value={hour} onChange={(e) => setHour(e.target.value)} />
      <input type="number" min={0} max={59} aria-label={`Minuto ${brand.brand}`} value={minute} onChange={(e) => setMinute(e.target.value)} />
      <button onClick={handleSave}>Salva</button>
      <button onClick={() => onRemove(brand.slug)}>Rimuovi</button>
    </li>
  );
}

export function ManageBrands({ trackedBrands, onBrandsChanged }: ManageBrandsProps) {
  const [catalog, setCatalog] = useState<BrandCatalogEntryOut[]>([]);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [defaultYear, setDefaultYear] = useState("5");
  const [defaultDay, setDefaultDay] = useState("");
  const [defaultHour, setDefaultHour] = useState("3");
  const [defaultMinute, setDefaultMinute] = useState("0");

  async function loadCatalog() {
    setCatalog(await fetchBrandCatalog());
  }

  useEffect(() => {
    loadCatalog();
  }, []);

  const trackedMakeIds = new Set(trackedBrands.map((b) => b.make_id));
  const filtered = catalog.filter(
    (entry) => !trackedMakeIds.has(entry.make_id) && entry.display_name.toLowerCase().includes(query.toLowerCase())
  );

  function toggleSelected(makeId: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(makeId)) next.delete(makeId);
      else next.add(makeId);
      return next;
    });
  }

  function currentDefaults(): BrandDefaultsPatch {
    return {
      year_from_years: defaultYear === "" ? null : Number(defaultYear),
      schedule_day_of_week: defaultDay === "" ? null : defaultDay,
      schedule_hour: Number(defaultHour),
      schedule_minute: Number(defaultMinute),
    };
  }

  async function handleRefreshCatalog() {
    await refreshBrandCatalog();
    await loadCatalog();
  }

  async function handleAddSelected() {
    if (selected.size === 0) return;
    await addBrands(Array.from(selected), currentDefaults());
    setSelected(new Set());
    onBrandsChanged();
  }

  async function handleApplyDefaults() {
    await applyDefaultsToAllBrands(currentDefaults());
    onBrandsChanged();
  }

  async function handleSaveBrand(slug: string, patch: BrandDefaultsPatch) {
    await updateBrand(slug, patch);
    onBrandsChanged();
  }

  async function handleRemoveBrand(slug: string) {
    await removeBrand(slug);
    onBrandsChanged();
  }

  return (
    <div className="manage-brands">
      <h2>Gestisci marche</h2>

      <section className="brand-defaults">
        <h3>Predefiniti</h3>
        <label>
          Anno (ultimi N anni, vuoto = nessun filtro)
          <input type="number" min={0} value={defaultYear} onChange={(e) => setDefaultYear(e.target.value)} />
        </label>
        <label>
          Giorno
          <select value={defaultDay} onChange={(e) => setDefaultDay(e.target.value)}>
            {DAYS.map((d) => (
              <option key={d.value} value={d.value}>{d.label}</option>
            ))}
          </select>
        </label>
        <label>
          Ora
          <input type="number" min={0} max={23} value={defaultHour} onChange={(e) => setDefaultHour(e.target.value)} />
        </label>
        <label>
          Minuto
          <input type="number" min={0} max={59} value={defaultMinute} onChange={(e) => setDefaultMinute(e.target.value)} />
        </label>
        <button onClick={handleApplyDefaults} disabled={trackedBrands.length === 0}>
          Applica a tutte le marche monitorate
        </button>
      </section>

      <section className="brand-picker">
        <h3>Aggiungi marche</h3>
        <button onClick={handleRefreshCatalog}>Aggiorna catalogo</button>
        <input type="text" placeholder="Cerca marca..." value={query} onChange={(e) => setQuery(e.target.value)} />
        <ul className="catalog-list">
          {filtered.map((entry) => (
            <li key={entry.make_id}>
              <label>
                <input
                  type="checkbox"
                  checked={selected.has(entry.make_id)}
                  onChange={() => toggleSelected(entry.make_id)}
                />
                {entry.display_name}
              </label>
            </li>
          ))}
        </ul>
        <button onClick={handleAddSelected} disabled={selected.size === 0}>
          Aggiungi selezionate ({selected.size})
        </button>
      </section>

      <section className="tracked-list">
        <h3>Marche monitorate</h3>
        <ul>
          {trackedBrands.map((brand) => (
            <TrackedBrandRow key={brand.slug} brand={brand} onSave={handleSaveBrand} onRemove={handleRemoveBrand} />
          ))}
        </ul>
      </section>
    </div>
  );
}

export { dayLabel };
```

`dayLabel` is exported for potential reuse by `App.tsx`/`BrandCard.tsx` in a later iteration; it is not itself under test here since `ManageBrands.test.tsx` exercises it only indirectly through rendered day labels.

- [ ] **Step 4: Run to confirm pass**

Run: `cd dashboard && npx vitest run src/components/ManageBrands.test.tsx`
Expected: `7 passed`

- [ ] **Step 5: Add minimal styling**

Append to `dashboard/src/index.css`:

```css
.manage-brands section {
  background: #1a1d24;
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 16px;
}

.manage-brands label {
  display: inline-flex;
  flex-direction: column;
  margin-right: 12px;
  font-size: 0.85em;
}

.manage-brands input[type="number"],
.manage-brands input[type="text"],
.manage-brands select {
  margin-top: 4px;
}

.catalog-list {
  max-height: 240px;
  overflow-y: auto;
  list-style: none;
  padding: 0;
}

.tracked-list li {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 0;
  border-bottom: 1px solid #2a2e37;
}
```

- [ ] **Step 6: Run the full dashboard suite**

Run: `cd dashboard && npx vitest run`
Expected: all pass, no regressions.

- [ ] **Step 7: Commit**

```bash
git add dashboard/src/components/ManageBrands.tsx dashboard/src/components/ManageBrands.test.tsx dashboard/src/index.css
git commit -m "Add ManageBrands: catalog search, bulk add, apply-defaults, per-brand edit/remove"
```

---

## Task 9: Wire `ManageBrands` into `App.tsx`; adaptive polling while a run is active

**Files:**
- Modify: `dashboard/src/App.tsx`

**Interfaces:** None new — this is the integration point.

- [ ] **Step 1: Read the current file**

Read `dashboard/src/App.tsx` in full.

- [ ] **Step 2: Rewrite App.tsx**

Full replacement:

```tsx
import { useEffect, useState } from "react";
import { BrandCard } from "./components/BrandCard";
import { BrandDetail } from "./components/BrandDetail";
import { ManageBrands } from "./components/ManageBrands";
import { fetchBrands, pauseBrand, resumeBrand, runBrandNow } from "./api";
import type { BrandStatusOut } from "./types";

const POLL_INTERVAL_ACTIVE_MS = 3000;
const POLL_INTERVAL_IDLE_MS = 15000;

export function App() {
  const [brands, setBrands] = useState<BrandStatusOut[]>([]);
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
  const [view, setView] = useState<"overview" | "manage">("overview");

  async function reload() {
    setBrands(await fetchBrands());
  }

  useEffect(() => {
    reload();
  }, []);

  useEffect(() => {
    const hasActiveRun = brands.some((b) => b.last_run?.status === "running");
    const interval = hasActiveRun ? POLL_INTERVAL_ACTIVE_MS : POLL_INTERVAL_IDLE_MS;
    const timer = setInterval(reload, interval);
    return () => clearInterval(timer);
  }, [brands]);

  async function handlePause(slug: string) {
    await pauseBrand(slug);
    await reload();
  }

  async function handleResume(slug: string) {
    await resumeBrand(slug);
    await reload();
  }

  async function handleRunNow(slug: string) {
    await runBrandNow(slug);
    await reload();
  }

  return (
    <div className="app">
      <h1>AutoSmart24 — Monitoraggio Scraper</h1>
      <nav className="view-nav">
        <button onClick={() => setView("overview")} disabled={view === "overview"}>
          Panoramica
        </button>
        <button onClick={() => setView("manage")} disabled={view === "manage"}>
          Gestisci marche
        </button>
      </nav>
      {view === "overview" && (
        <>
          <div className="brand-grid">
            {brands.map((brand) => (
              <BrandCard
                key={brand.slug}
                brand={brand}
                onPause={handlePause}
                onResume={handleResume}
                onRunNow={handleRunNow}
                onSelect={setSelectedSlug}
              />
            ))}
          </div>
          {selectedSlug && <BrandDetail brandSlug={selectedSlug} onClose={() => setSelectedSlug(null)} />}
        </>
      )}
      {view === "manage" && <ManageBrands trackedBrands={brands} onBrandsChanged={reload} />}
    </div>
  );
}
```

- [ ] **Step 3: Run the full dashboard suite**

Run: `cd dashboard && npx vitest run`
Expected: all pass. No existing test exercises `App.tsx` directly (confirmed: only `BrandCard.test.tsx` and `BrandDetail.test.tsx`/`ManageBrands.test.tsx` exist), so this integration is not covered by an automated test — Task 10's live verification covers it manually.

- [ ] **Step 4: Commit**

```bash
git add dashboard/src/App.tsx
git commit -m "Add brand-management navigation to App.tsx; poll faster while a run is active"
```

---

## Task 10: Live verification

Not a TDD task — rebuild the stack and verify the new brand-management flow works end to end against the real site.

**Files:** None.

- [ ] **Step 1: Full test suite baseline**

Run: `cd scraper && python -m pytest -q` and `cd dashboard && npx vitest run`
Expected: all pass.

- [ ] **Step 2: Rebuild and restart the stack**

Run (from `C:\App AI\Autoscout`): `docker compose up -d --build`
Expected: images rebuild; migration `0004_brand_catalog_and_tracked_brands` applies on `app` startup (check `docker compose logs app --tail 20`); all three containers `Up`.

- [ ] **Step 3: Confirm the seed ran and the 5 original brands are intact**

Run: `curl -s http://localhost:8001/brands | python -m json.tool`
Expected: 5 brands (Fiat, Volkswagen, BMW, Audi, Mercedes-Benz), each with `year_from_years: 5`, `schedule_day_of_week: null`, `schedule_hour: 3`, `schedule_minute: 0`.

- [ ] **Step 4: Refresh the catalog and confirm the full brand list is there**

Run: `curl -s -X POST http://localhost:8001/brand-catalog/refresh`
Expected: `{"count": <a number around 290>}`

Run: `curl -s http://localhost:8001/brand-catalog | python -c "import json,sys; print(len(json.load(sys.stdin)))"`
Expected: matches the refresh count.

- [ ] **Step 5: Add a new brand via the API and confirm it schedules immediately**

Pick a brand from the catalog output that is not one of the original 5 (e.g. BMW's `make_id` is already tracked — pick something like Peugeot or Renault, whatever the real catalog contains; read the Step 4 output to choose a real `make_id`).

Run: `curl -s -X POST http://localhost:8001/brands/bulk -H "Content-Type: application/json" -d '{"make_ids": [<chosen make_id>], "year_from_years": 3, "schedule_hour": 2, "schedule_minute": 0}'`
Expected: the new brand appears in the response with `year_from_years: 3`.

Run: `curl -s http://localhost:8001/brands | python -m json.tool` — confirm the new brand is now listed alongside the original 5.

- [ ] **Step 6: Update a brand's year filter and confirm it takes effect on the next run without a restart**

Run: `curl -s -X PATCH http://localhost:8001/brands/fiat -H "Content-Type: application/json" -d '{"year_from_years": 2}'`
Expected: response shows `year_from_years: 2`.

Run: `curl -s -X POST http://localhost:8001/brands/fiat/run-now`, wait ~30s, then check the live app logs for the search URLs it's requesting (`docker compose logs app --tail 40 | grep fregfrom` is not directly visible from HTTP client logs — instead, confirm indirectly: query `docker exec autoscout-postgres-1 psql -U autosmart24 -d autosmart24 -c "SELECT min(first_registration) FROM listings WHERE brand='Fiat' AND first_seen_at > now() - interval '5 minutes';"` after the run has been going a few minutes — expect no date earlier than 2 years before today, confirming the just-edited `year_from_years=2` is what this run actually used, not a stale value baked in earlier).

- [ ] **Step 7: Open the dashboard in a browser and confirm the new screen works**

Navigate to `http://localhost:5173`. Click "Gestisci marche". Confirm: the catalog search box filters as you type; the tracked-brands list shows all current brands with editable year/day/hour/minute fields; "Applica a tutte le marche monitorate" and an individual "Salva" both work without a page reload (the list updates); "Rimuovi" removes a brand from the list.

- [ ] **Step 8: Confirm faster polling while a run is active**

With a run in progress (e.g. from Step 6), open the browser's network tab on the overview screen and confirm requests to `/brands` are firing roughly every 3 seconds, not 15.

- [ ] **Step 9: Stop the stack**

Run: `docker compose down`
Expected: all containers removed cleanly.

## Self-review notes

- **Spec coverage:** §2 (catalog discovery) → Task 2; §3 (schema) → Task 1; §4 (dynamic scheduler) → Task 3 + Task 6; §5 (API) → Task 4 + Task 5; §6 (UI: search/select/bulk-add/apply-defaults/per-brand edit, faster polling) → Task 8 + Task 9; §7 (error handling: unknown make_id, catalog refresh failure surfaced not swallowed) → Task 5 (`HTTPException` on unknown `make_id`; `refresh_catalog_fn()` is called synchronously inside the endpoint, so a network/parse failure propagates as a 500 rather than silently leaving the catalog stale-but-reported-successful); §8 (testing) → each task's own TDD steps; §9 (risks: slug validation, single-IP posture, polling-not-push) → Task 2 Step 5 (empirical slug validation), Global Constraints (single-IP unchanged), Task 9 (polling, not WebSocket).
- **Placeholder scan:** no TBD/TODO; every step has runnable code.
- **Type consistency verified:** `BrandConfig` (unchanged dataclass) remains the type `run_manager.run_brand_sweep`, `scheduler.schedule_brand`, and `run_now_fn`/`run_fn` all accept; `TrackedBrand` rows are converted to it via `_to_brand_config` at every call site in `api/main.py`, and via direct `BrandConfig(...)` construction in `api/app.py`'s startup loop — same shape, no drift. `BrandStatusOut`'s five new fields (Task 4) are populated by `_to_brand_status` (Task 5) and consumed identically by the frontend's extended `BrandStatusOut` interface (Task 7) and by `ManageBrands`/`TrackedBrandRow` (Task 8). `BrandDefaultsPatch` (Task 7) matches the shape `AddBrandsRequest`/`UpdateBrandRequest`/`ApplyDefaultsRequest` (Task 4) expect field-for-field.
- **Known, disclosed behavior change:** the one-time seed schedule (daily 03:00) does not reproduce `SCRAPE_INTERVAL_DAYS=4` exactly — documented in Global Constraints and in Task 6's seed function docstring, not silently glossed over.
