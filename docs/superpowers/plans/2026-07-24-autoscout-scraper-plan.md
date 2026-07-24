# Scraper Autoscout24 (AutoSmart24) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Autoscout24 scraper (MVP): a Python service that discovers, tracks and prices car listings for 5 brands via the site's own embedded JSON data, stores them in Postgres with full price history and sold-detection, and exposes an advanced React monitoring dashboard.

**Architecture:** A single Python service (`scraper/`) combines a scheduled scraping engine (httpx + APScheduler) and a FastAPI backend in one process (so pause/resume/run-now controls act on the same in-memory scheduler that runs the jobs). Data is server-side-rendered JSON embedded in every autoscout24.it page (`__NEXT_DATA__`) — no browser automation needed. Postgres stores listings, price history, run/event logs. A separate React (Vite) dashboard consumes the API. Everything runs via Docker Compose on a single machine.

**Tech Stack:** Python 3.12, httpx, SQLAlchemy 2.0 + Alembic, PostgreSQL 16, APScheduler 3.10, FastAPI + uvicorn, pytest + respx; React 18 + TypeScript + Vite + recharts + Vitest; Docker Compose.

## Global Constraints

- Deployment: Docker Desktop, single machine, single IP, no IP rotation (per approved spec).
- Search-query splitting criterion must NEVER be price — only model (`mmmv` param) and registration year (`fregfrom`/`fregto`) may be used to stay under the ~4000-result pagination cap (200 pages × ~20/page).
- "Sold" status requires explicit detail-page confirmation (HTTP 404/410, or JSON `status` field ≠ `"Active"`) — never inferred from absence in a sweep alone.
- The dashboard is the sole monitoring/notification channel — no email/Telegram.
- MVP brands (fixed): Fiat (makeId 28, slug `fiat`), Volkswagen (74, `volkswagen`), BMW (13, `bmw`), Audi (9, `audi`), Mercedes-Benz (47, `mercedes-benz`).
- Rate-limit delay (`SCRAPE_MIN_DELAY_SECONDS`/`SCRAPE_MAX_DELAY_SECONDS`) and run cadence (`SCRAPE_INTERVAL_DAYS`) must be environment-configurable, so calibration (spec §6) is done by adjusting config and observing the dashboard, without code changes.
- Base URL: `https://www.autoscout24.it`. Real fixture pages (`scraper/tests/fixtures/search_fiat_page1.html`, `detail_fiat_grande_panda.html`) were already fetched and committed — use them, don't re-fetch.

---

## Task 1: Repo scaffolding

**Files:**
- Create: `scraper/requirements.txt`
- Create: `scraper/pytest.ini`
- Create: `scraper/src/autosmart24/__init__.py`
- Create: `scraper/src/autosmart24/config.py`
- Create: `docker-compose.yml`
- Create: `.env.example`

**Interfaces:**
- Produces: `autosmart24.config.BrandConfig` (dataclass: `slug: str`, `make_id: int`, `display_name: str`), `autosmart24.config.MVP_BRANDS: list[BrandConfig]`, `autosmart24.config.BASE_URL: str`, `autosmart24.config.MAX_RESULTS_PER_QUERY: int`.

- [ ] **Step 1: Create requirements.txt**

```
httpx==0.27.2
respx==0.21.1
SQLAlchemy==2.0.35
psycopg[binary]==3.2.3
alembic==1.13.3
APScheduler==3.10.4
fastapi==0.115.0
uvicorn[standard]==0.30.6
pytest==8.3.3
```

- [ ] **Step 2: Create pytest.ini**

```ini
[pytest]
pythonpath = src
testpaths = tests
```

- [ ] **Step 3: Create the config module with brand definitions**

`scraper/src/autosmart24/config.py`:

```python
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
```

`scraper/src/autosmart24/__init__.py` — empty file.

- [ ] **Step 4: Create docker-compose.yml with Postgres only (expanded in later tasks)**

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: autosmart24
      POSTGRES_PASSWORD: autosmart24
      POSTGRES_DB: autosmart24
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data

volumes:
  postgres_data:
```

- [ ] **Step 5: Create .env.example**

```
DATABASE_URL=postgresql+psycopg://autosmart24:autosmart24@localhost:5432/autosmart24
SCRAPE_INTERVAL_DAYS=4
SCRAPE_MIN_DELAY_SECONDS=3
SCRAPE_MAX_DELAY_SECONDS=8
```

- [ ] **Step 6: Verify Python package imports cleanly**

Run: `cd scraper && python -c "from autosmart24.config import MVP_BRANDS; print(len(MVP_BRANDS))"`
Expected: `5`

- [ ] **Step 7: Commit**

```bash
git add scraper/requirements.txt scraper/pytest.ini scraper/src/autosmart24/__init__.py scraper/src/autosmart24/config.py docker-compose.yml .env.example
git commit -m "Scaffold scraper package, brand config, and docker-compose skeleton"
```

---

## Task 2: DB models and Alembic migration

**Files:**
- Create: `scraper/src/autosmart24/db/__init__.py`
- Create: `scraper/src/autosmart24/db/models.py`
- Create: `scraper/src/autosmart24/db/session.py`
- Create: `scraper/alembic.ini`
- Create: `scraper/migrations/env.py`
- Create: `scraper/migrations/script.py.mako`
- Create: `scraper/migrations/versions/0001_initial.py`

**Interfaces:**
- Produces: `autosmart24.db.models.Base`, `.Listing`, `.PriceHistory`, `.ScrapeRun`, `.ScrapeEvent` (SQLAlchemy 2.0 declarative models — see field list below, consumed by every later task).
- Produces: `autosmart24.db.session.make_engine(database_url: str | None = None)`, `.make_session_factory(engine) -> sessionmaker`, `.init_db(engine) -> None` (test-only schema creation; production schema is owned by Alembic).

- [ ] **Step 1: Write the DB models**

`scraper/src/autosmart24/db/models.py`:

```python
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
    cross_reference_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

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
    province: Mapped[str | None] = mapped_column(String(8), nullable=True)
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
```

`scraper/src/autosmart24/db/__init__.py` — empty file.

- [ ] **Step 2: Write the session helpers**

`scraper/src/autosmart24/db/session.py`:

```python
from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from autosmart24.db.models import Base


def make_engine(database_url: str | None = None) -> Engine:
    url = database_url or os.environ["DATABASE_URL"]
    return create_engine(url, future=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)
```

- [ ] **Step 3: Verify models import and create tables against SQLite**

Run: `cd scraper && python -c "from autosmart24.db.session import init_db, make_engine; e = make_engine('sqlite:///:memory:'); init_db(e); print(e.table_names() if hasattr(e, 'table_names') else 'ok')"`
Expected: no traceback (prints `ok` or similar) — confirms models + JSON variant column compile cleanly.

- [ ] **Step 4: Write Alembic configuration**

`scraper/alembic.ini`:

```ini
[alembic]
script_location = migrations
sqlalchemy.url =

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

`scraper/migrations/script.py.mako`:

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

`scraper/migrations/env.py`:

```python
from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from autosmart24.db.models import Base

config = context.config
fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section), prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 5: Write the initial migration (matches models.py exactly)**

`scraper/migrations/versions/0001_initial.py`:

```python
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "listings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("cross_reference_id", sa.String(32), nullable=True),
        sa.Column("brand", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("model_group", sa.String(128), nullable=True),
        sa.Column("variant", sa.String(128), nullable=True),
        sa.Column("motor_type_name", sa.String(128), nullable=True),
        sa.Column("version_input", sa.String(256), nullable=True),
        sa.Column("transmission", sa.String(64), nullable=True),
        sa.Column("fuel", sa.String(64), nullable=True),
        sa.Column("first_registration", sa.Date(), nullable=True),
        sa.Column("mileage_km", sa.Integer(), nullable=True),
        sa.Column("power_kw", sa.Integer(), nullable=True),
        sa.Column("power_cv", sa.Integer(), nullable=True),
        sa.Column("displacement_ccm", sa.Integer(), nullable=True),
        sa.Column("body_type", sa.String(64), nullable=True),
        sa.Column("body_color", sa.String(64), nullable=True),
        sa.Column("num_seats", sa.Integer(), nullable=True),
        sa.Column("num_doors", sa.Integer(), nullable=True),
        sa.Column("num_previous_owners", sa.Integer(), nullable=True),
        sa.Column("seller_type", sa.String(32), nullable=True),
        sa.Column("seller_company_name", sa.String(256), nullable=True),
        sa.Column("city", sa.String(256), nullable=True),
        sa.Column("province", sa.String(8), nullable=True),
        sa.Column("zip_code", sa.String(16), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("price", sa.Integer(), nullable=True),
        sa.Column("vat_exposed", sa.Boolean(), nullable=True),
        sa.Column("price_evaluation_category", sa.Integer(), nullable=True),
        sa.Column("price_evaluation_median", sa.Integer(), nullable=True),
        sa.Column("url", sa.String(1024), nullable=False),
        sa.Column("created_at_source", sa.DateTime(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("sold_at", sa.DateTime(), nullable=True),
        sa.Column("detail_scraped", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("raw_snippet", JSONB(), nullable=True),
        sa.Column("raw_detail", JSONB(), nullable=True),
    )
    op.create_index("ix_listings_brand", "listings", ["brand"])

    op.create_table(
        "price_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("listing_id", sa.String(36), sa.ForeignKey("listings.id"), nullable=False),
        sa.Column("price", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_price_history_listing_id", "price_history", ["listing_id"])

    op.create_table(
        "scrape_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("brand", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("listings_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("new_listings", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("price_changes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sold_detected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("errors_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_scrape_runs_brand", "scrape_runs", ["brand"])

    op.create_table(
        "scrape_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("scrape_runs.id"), nullable=True),
        sa.Column("brand", sa.String(64), nullable=True),
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("message", sa.String(2048), nullable=False),
        sa.Column("url", sa.String(1024), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_scrape_events_run_id", "scrape_events", ["run_id"])
    op.create_index("ix_scrape_events_brand", "scrape_events", ["brand"])


def downgrade() -> None:
    op.drop_table("scrape_events")
    op.drop_table("scrape_runs")
    op.drop_table("price_history")
    op.drop_table("listings")
```

- [ ] **Step 6: Verify the migration against a real Postgres**

Run: `docker compose up -d postgres` then, from `scraper/`:
`DATABASE_URL=postgresql+psycopg://autosmart24:autosmart24@localhost:5432/autosmart24 python -m alembic upgrade head`
Expected: output ending in `Running upgrade  -> 0001_initial, ...` with no errors.

- [ ] **Step 7: Commit**

```bash
git add scraper/src/autosmart24/db scraper/alembic.ini scraper/migrations
git commit -m "Add DB models (listings, price_history, scrape_runs, scrape_events) and initial Alembic migration"
```

---

## Task 3: Test DB fixture and smoke test

**Files:**
- Create: `scraper/tests/__init__.py`
- Create: `scraper/tests/conftest.py`
- Create: `scraper/tests/test_db_smoke.py`

**Interfaces:**
- Produces: pytest fixture `db_session` (a `Session` bound to a fresh in-memory SQLite DB with all tables created) — consumed by every later test module that touches the DB.

- [ ] **Step 1: Write the failing smoke test**

`scraper/tests/test_db_smoke.py`:

```python
import datetime as dt

from autosmart24.db.models import Listing


def test_db_session_round_trips_a_listing(db_session):
    now = dt.datetime.utcnow()
    db_session.add(
        Listing(
            id="11111111-1111-1111-1111-111111111111",
            brand="Fiat",
            price=15000,
            url="https://www.autoscout24.it/annunci/example",
            first_seen_at=now,
            last_seen_at=now,
            last_checked_at=now,
            status="active",
            detail_scraped=False,
        )
    )
    db_session.commit()

    fetched = db_session.get(Listing, "11111111-1111-1111-1111-111111111111")
    assert fetched is not None
    assert fetched.brand == "Fiat"
    assert fetched.price == 15000
```

`scraper/tests/__init__.py` — empty file.

- [ ] **Step 2: Run it to confirm it fails (no conftest yet)**

Run: `cd scraper && pytest tests/test_db_smoke.py -v`
Expected: FAIL with `fixture 'db_session' not found`

- [ ] **Step 3: Write conftest.py providing the db_session fixture**

`scraper/tests/conftest.py`:

```python
import pytest
from sqlalchemy.orm import Session

from autosmart24.db.session import init_db, make_engine, make_session_factory


@pytest.fixture()
def db_session() -> Session:
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    factory = make_session_factory(engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()
```

- [ ] **Step 4: Run the test again to confirm it passes**

Run: `cd scraper && pytest tests/test_db_smoke.py -v`
Expected: `1 passed`

- [ ] **Step 5: Commit**

```bash
git add scraper/tests/__init__.py scraper/tests/conftest.py scraper/tests/test_db_smoke.py
git commit -m "Add SQLite-backed db_session test fixture and smoke test"
```

---

## Task 4: Extract embedded JSON from autoscout24 pages (`next_data.py`)

Real fixture pages are already committed at `scraper/tests/fixtures/search_fiat_page1.html` and `scraper/tests/fixtures/detail_fiat_grande_panda.html` — both real HTML fetched from the live site, each containing a `<script id="__NEXT_DATA__" type="application/json">...</script>` tag with the full page data.

**Files:**
- Create: `scraper/src/autosmart24/scraping/__init__.py`
- Create: `scraper/src/autosmart24/scraping/next_data.py`
- Create: `scraper/tests/test_next_data.py`

**Interfaces:**
- Produces: `autosmart24.scraping.next_data.extract_next_data(html: str) -> dict` (raises `NextDataNotFoundError` if the tag is absent) — consumed by every later scraping task that fetches a page.

- [ ] **Step 1: Write the failing tests**

`scraper/tests/test_next_data.py`:

```python
from pathlib import Path

import pytest

from autosmart24.scraping.next_data import NextDataNotFoundError, extract_next_data

FIXTURES = Path(__file__).parent / "fixtures"


def test_extract_next_data_from_search_page():
    html = (FIXTURES / "search_fiat_page1.html").read_text(encoding="utf-8")
    data = extract_next_data(html)
    page_props = data["props"]["pageProps"]
    assert page_props["numberOfResults"] > 0
    assert isinstance(page_props["listings"], list)
    assert len(page_props["listings"]) == 20


def test_extract_next_data_from_detail_page():
    html = (FIXTURES / "detail_fiat_grande_panda.html").read_text(encoding="utf-8")
    data = extract_next_data(html)
    listing_details = data["props"]["pageProps"]["listingDetails"]
    assert listing_details["vehicle"]["make"] == "Fiat"


def test_extract_next_data_raises_when_missing():
    with pytest.raises(NextDataNotFoundError):
        extract_next_data("<html><body>no data here</body></html>")
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd scraper && pytest tests/test_next_data.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autosmart24.scraping'`

- [ ] **Step 3: Implement next_data.py**

`scraper/src/autosmart24/scraping/__init__.py` — empty file.

`scraper/src/autosmart24/scraping/next_data.py`:

```python
from __future__ import annotations

import json
import re

NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)


class NextDataNotFoundError(Exception):
    pass


def extract_next_data(html: str) -> dict:
    match = NEXT_DATA_RE.search(html)
    if not match:
        raise NextDataNotFoundError("__NEXT_DATA__ script tag not found in page")
    return json.loads(match.group(1))
```

- [ ] **Step 4: Run to confirm pass**

Run: `cd scraper && pytest tests/test_next_data.py -v`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add scraper/src/autosmart24/scraping/__init__.py scraper/src/autosmart24/scraping/next_data.py scraper/tests/test_next_data.py
git commit -m "Add __NEXT_DATA__ JSON extraction from autoscout24 pages"
```

---

## Task 5: Map search-result snippet JSON to normalized fields (`snippet_mapper.py`)

**Files:**
- Create: `scraper/src/autosmart24/scraping/snippet_mapper.py`
- Create: `scraper/tests/test_snippet_mapper.py`

**Interfaces:**
- Consumes: `autosmart24.scraping.next_data.extract_next_data`.
- Produces: `autosmart24.scraping.snippet_mapper.map_snippet_listing(raw: dict) -> dict` returning keys: `id, cross_reference_id, url, brand, model, model_group, variant, motor_type_name, version_input, transmission, fuel, first_registration (date|None), mileage_km (int|None), seller_type, seller_company_name, city, zip_code, price (int|None), raw_snippet` — consumed by `crawler.py` (Task 10) and `run_manager.py` (Task 13).

- [ ] **Step 1: Write the failing test**

`scraper/tests/test_snippet_mapper.py`:

```python
from pathlib import Path

from autosmart24.scraping.next_data import extract_next_data
from autosmart24.scraping.snippet_mapper import map_snippet_listing

FIXTURES = Path(__file__).parent / "fixtures"


def _first_raw_listing() -> dict:
    html = (FIXTURES / "search_fiat_page1.html").read_text(encoding="utf-8")
    data = extract_next_data(html)
    return data["props"]["pageProps"]["listings"][0]


def test_map_snippet_listing_extracts_core_fields():
    raw = _first_raw_listing()
    mapped = map_snippet_listing(raw)

    assert mapped["id"] == "b73b0c64-3c16-4215-b927-02a5fe324ee7"
    assert mapped["brand"] == "Fiat"
    assert mapped["model"] == "Grande Panda"
    assert mapped["price"] == 13990
    assert mapped["mileage_km"] == 10
    assert mapped["first_registration"].isoformat() == "2026-04-01"
    assert mapped["seller_type"] == "Dealer"
    assert mapped["url"] == (
        "https://www.autoscout24.it/annunci/"
        "fiat-grande-panda-benzina-icon-cambio-manuale-promo-flex-benzina-cat_ma28mo76901-"
        "b73b0c64-3c16-4215-b927-02a5fe324ee7"
    )
    assert mapped["raw_snippet"] == raw


def test_map_snippet_listing_handles_missing_tracking_gracefully():
    raw = {
        "id": "zzz",
        "crossReferenceId": None,
        "url": "/annunci/zzz",
        "price": {},
        "vehicle": {},
        "location": {},
        "seller": {},
        "tracking": {},
    }
    mapped = map_snippet_listing(raw)

    assert mapped["price"] is None
    assert mapped["mileage_km"] is None
    assert mapped["first_registration"] is None
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd scraper && pytest tests/test_snippet_mapper.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autosmart24.scraping.snippet_mapper'`

- [ ] **Step 3: Implement snippet_mapper.py**

`scraper/src/autosmart24/scraping/snippet_mapper.py`:

```python
from __future__ import annotations

import datetime as dt

from autosmart24.config import BASE_URL


def _parse_first_registration(value: str | None) -> dt.date | None:
    if not value:
        return None
    month_str, year_str = value.split("-")
    return dt.date(int(year_str), int(month_str), 1)


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
        "raw_snippet": raw,
    }
```

- [ ] **Step 4: Run to confirm pass**

Run: `cd scraper && pytest tests/test_snippet_mapper.py -v`
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add scraper/src/autosmart24/scraping/snippet_mapper.py scraper/tests/test_snippet_mapper.py
git commit -m "Map search-result snippet JSON to normalized listing fields"
```

---

## Task 6: Map detail-page JSON to normalized fields (`detail_mapper.py`)

**Files:**
- Create: `scraper/src/autosmart24/scraping/detail_mapper.py`
- Create: `scraper/tests/test_detail_mapper.py`

**Interfaces:**
- Consumes: `autosmart24.scraping.next_data.extract_next_data`.
- Produces: `autosmart24.scraping.detail_mapper.map_detail_listing(ld: dict) -> dict` returning keys: `id, cross_reference_id, brand, model, model_group, variant, motor_type_name, version_input, transmission, fuel, first_registration (date|None), mileage_km, power_kw, power_cv, displacement_ccm, body_type, body_color, num_seats, num_doors, num_previous_owners, seller_type, seller_company_name, city, province, zip_code, latitude, longitude, price, vat_exposed, price_evaluation_category, price_evaluation_median, url, source_status, created_at_source (datetime|None), raw_detail` — consumed by `detail_queue.py` (Task 12) and `run_manager.py` (Task 13).

- [ ] **Step 1: Write the failing test**

`scraper/tests/test_detail_mapper.py`:

```python
from pathlib import Path

from autosmart24.scraping.next_data import extract_next_data
from autosmart24.scraping.detail_mapper import map_detail_listing

FIXTURES = Path(__file__).parent / "fixtures"


def _listing_details() -> dict:
    html = (FIXTURES / "detail_fiat_grande_panda.html").read_text(encoding="utf-8")
    data = extract_next_data(html)
    return data["props"]["pageProps"]["listingDetails"]


def test_map_detail_listing_extracts_full_fields():
    ld = _listing_details()
    mapped = map_detail_listing(ld)

    assert mapped["id"] == ld["id"]
    assert mapped["brand"] == "Fiat"
    assert mapped["model"] == "Grande Panda"
    assert mapped["price"] == 13990
    assert mapped["power_kw"] == 74
    assert mapped["power_cv"] == 101
    assert mapped["displacement_ccm"] == 1199
    assert mapped["body_type"] == "Berlina"
    assert mapped["num_seats"] == 5
    assert mapped["province"] == "TO"
    assert mapped["seller_type"] == "Dealer"
    assert mapped["source_status"] == "Active"
    assert mapped["first_registration"].isoformat() == "2026-04-01"
    assert mapped["created_at_source"].year == 2026
    assert mapped["url"].startswith("https://www.autoscout24.it/annunci/")
    assert mapped["raw_detail"] == ld


def test_map_detail_listing_handles_missing_city_gracefully():
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

    assert mapped["city"] is None
    assert mapped["province"] is None
    assert mapped["created_at_source"] is None
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd scraper && pytest tests/test_detail_mapper.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autosmart24.scraping.detail_mapper'`

- [ ] **Step 3: Implement detail_mapper.py**

`scraper/src/autosmart24/scraping/detail_mapper.py`:

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


def map_detail_listing(ld: dict) -> dict:
    vehicle = ld.get("vehicle") or {}
    location = ld.get("location") or {}
    seller = ld.get("seller") or {}
    identifier = ld.get("identifier") or {}
    prices_public = (ld.get("prices") or {}).get("public") or {}

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
        "url": ld.get("webPage"),
        "source_status": ld.get("status"),
        "created_at_source": _parse_created_at(ld.get("createdTimestampWithOffset")),
        "raw_detail": ld,
    }
```

- [ ] **Step 4: Run to confirm pass**

Run: `cd scraper && pytest tests/test_detail_mapper.py -v`
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add scraper/src/autosmart24/scraping/detail_mapper.py scraper/tests/test_detail_mapper.py
git commit -m "Map detail-page JSON to normalized listing fields"
```

---

## Task 7: Build search URLs with model/year filters (`search_query.py`)

Verified against the live site: `/lst/{brand_slug}` accepts query param `mmmv={makeId}|{modelId}||` to filter by model (confirmed: Fiat all models = 44,772 results; `mmmv=28|1746||` = Panda only = 10,623 results), and `fregfrom`/`fregto` (year only, e.g. `2020`) to filter by first-registration year range (confirmed: Panda 2020-2022 = 2,270 results). Base query params confirmed from the live search page: `cy=I&atype=C&ustate=N,U&sort=standard&desc=0&powertype=kw`.

**Files:**
- Create: `scraper/src/autosmart24/scraping/search_query.py`
- Create: `scraper/tests/test_search_query.py`

**Interfaces:**
- Produces: `autosmart24.scraping.search_query.build_search_url(brand_slug: str, page: int, make_id: int, model_id: int | None = None, year_from: int | None = None, year_to: int | None = None) -> str` — consumed by `crawler.py` (Task 10).

- [ ] **Step 1: Write the failing tests**

`scraper/tests/test_search_query.py`:

```python
from urllib.parse import parse_qs, urlparse

from autosmart24.scraping.search_query import build_search_url


def test_build_search_url_brand_only():
    url = build_search_url("fiat", page=1, make_id=28)
    parsed = urlparse(url)
    assert parsed.path == "/lst/fiat"
    query = parse_qs(parsed.query)
    assert query["page"] == ["1"]
    assert query["cy"] == ["I"]
    assert "mmmv" not in query
    assert "fregfrom" not in query


def test_build_search_url_with_model_filter():
    url = build_search_url("fiat", page=3, make_id=28, model_id=1746)
    query = parse_qs(urlparse(url).query)
    assert query["mmmv"] == ["28|1746||"]
    assert query["page"] == ["3"]


def test_build_search_url_with_year_range():
    url = build_search_url("fiat", page=1, make_id=28, model_id=1746, year_from=2020, year_to=2022)
    query = parse_qs(urlparse(url).query)
    assert query["fregfrom"] == ["2020"]
    assert query["fregto"] == ["2022"]
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd scraper && pytest tests/test_search_query.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autosmart24.scraping.search_query'`

- [ ] **Step 3: Implement search_query.py**

```python
from __future__ import annotations

from urllib.parse import urlencode

from autosmart24.config import BASE_URL

BASE_QUERY_PARAMS = {
    "cy": "I",
    "atype": "C",
    "ustate": "N,U",
    "sort": "standard",
    "desc": "0",
    "powertype": "kw",
}


def build_search_url(
    brand_slug: str,
    page: int,
    make_id: int,
    model_id: int | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
) -> str:
    params = dict(BASE_QUERY_PARAMS)
    params["page"] = str(page)
    if model_id is not None:
        params["mmmv"] = f"{make_id}|{model_id}||"
    if year_from is not None:
        params["fregfrom"] = str(year_from)
    if year_to is not None:
        params["fregto"] = str(year_to)

    return f"{BASE_URL}/lst/{brand_slug}?{urlencode(params)}"
```

- [ ] **Step 4: Run to confirm pass**

Run: `cd scraper && pytest tests/test_search_query.py -v`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add scraper/src/autosmart24/scraping/search_query.py scraper/tests/test_search_query.py
git commit -m "Build autoscout24 search URLs with model and year-range filters"
```

---

## Task 8: Recursive year-range splitter (`year_split.py`)

When a single model exceeds `MAX_RESULTS_PER_QUERY` (4000) even after filtering by model, we must further split by first-registration year range so every sub-query's result count stays under the pagination cap. This is a pure bisection algorithm, independent of HTTP — the caller supplies a `count_fn` that returns the live result count for a candidate range.

**Files:**
- Create: `scraper/src/autosmart24/scraping/year_split.py`
- Create: `scraper/tests/test_year_split.py`

**Interfaces:**
- Produces: `autosmart24.scraping.year_split.split_year_ranges(count_fn: Callable[[int, int], int], year_from: int, year_to: int, max_results: int) -> list[tuple[int, int]]` — consumed by `crawler.py` (Task 10).

- [ ] **Step 1: Write the failing tests**

`scraper/tests/test_year_split.py`:

```python
from autosmart24.scraping.year_split import split_year_ranges


def test_no_split_needed_when_under_threshold():
    ranges = split_year_ranges(lambda f, t: 100, 1950, 2026, max_results=4000)
    assert ranges == [(1950, 2026)]


def test_splits_recursively_until_under_threshold():
    counts = {
        (1950, 2026): 10000,
        (1950, 1988): 3000,
        (1989, 2026): 8000,
        (1989, 2007): 3500,
        (2008, 2026): 4500,
        (2008, 2017): 2000,
        (2018, 2026): 2500,
    }

    def count_fn(year_from: int, year_to: int) -> int:
        return counts[(year_from, year_to)]

    ranges = split_year_ranges(count_fn, 1950, 2026, max_results=4000)
    assert ranges == [(1950, 1988), (1989, 2007), (2008, 2017), (2018, 2026)]


def test_stops_splitting_at_single_year_even_if_over_threshold():
    ranges = split_year_ranges(lambda f, t: 999999, 2020, 2020, max_results=4000)
    assert ranges == [(2020, 2020)]
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd scraper && pytest tests/test_year_split.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autosmart24.scraping.year_split'`

- [ ] **Step 3: Implement year_split.py**

```python
from __future__ import annotations

from typing import Callable


def split_year_ranges(
    count_fn: Callable[[int, int], int],
    year_from: int,
    year_to: int,
    max_results: int,
) -> list[tuple[int, int]]:
    count = count_fn(year_from, year_to)
    if count <= max_results or year_from >= year_to:
        return [(year_from, year_to)]

    midpoint = (year_from + year_to) // 2
    left = split_year_ranges(count_fn, year_from, midpoint, max_results)
    right = split_year_ranges(count_fn, midpoint + 1, year_to, max_results)
    return left + right
```

- [ ] **Step 4: Run to confirm pass**

Run: `cd scraper && pytest tests/test_year_split.py -v`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add scraper/src/autosmart24/scraping/year_split.py scraper/tests/test_year_split.py
git commit -m "Add recursive year-range bisection for oversized model queries"
```

---

## Task 9: Rate-limited HTTP client with block detection (`http_client.py`)

Confirmed live: plain HTTP GET (no browser) with a realistic `User-Agent` returns full data with HTTP 200. The client below adds a randomized delay before every request (calibratable via constructor args / env vars later) and turns HTTP 403/429 into a typed `BlockedError` so callers can distinguish "site is blocking us" from "listing genuinely gone" (404/410).

**Files:**
- Create: `scraper/src/autosmart24/scraping/http_client.py`
- Create: `scraper/tests/test_http_client.py`

**Interfaces:**
- Produces: `autosmart24.scraping.http_client.RateLimitedClient` (dataclass; fields `min_delay_seconds: float = 3.0`, `max_delay_seconds: float = 8.0`, `sleep_fn: Callable[[float], None] = time.sleep`; method `get(url: str) -> httpx.Response`, raises `BlockedError` on 403/429, raises `httpx.HTTPStatusError` on other 4xx/5xx via `raise_for_status()`), `.BlockedError(status_code: int, url: str)` — consumed by `crawler.py` (Task 10), `detail_queue.py` (Task 12), `run_manager.py` (Task 13).

- [ ] **Step 1: Write the failing tests**

`scraper/tests/test_http_client.py`:

```python
import httpx
import pytest
import respx

from autosmart24.scraping.http_client import BlockedError, RateLimitedClient


def _instant_client() -> RateLimitedClient:
    return RateLimitedClient(min_delay_seconds=0, max_delay_seconds=0, sleep_fn=lambda _: None)


@respx.mock
def test_get_returns_response_body():
    respx.get("https://example.test/page").mock(return_value=httpx.Response(200, text="ok"))

    response = _instant_client().get("https://example.test/page")

    assert response.status_code == 200
    assert response.text == "ok"


@respx.mock
def test_get_raises_blocked_error_on_403():
    respx.get("https://example.test/blocked").mock(return_value=httpx.Response(403, text="forbidden"))

    with pytest.raises(BlockedError) as exc_info:
        _instant_client().get("https://example.test/blocked")

    assert exc_info.value.status_code == 403


@respx.mock
def test_get_raises_blocked_error_on_429():
    respx.get("https://example.test/limited").mock(return_value=httpx.Response(429, text="too many"))

    with pytest.raises(BlockedError):
        _instant_client().get("https://example.test/limited")


@respx.mock
def test_get_raises_http_status_error_on_404():
    respx.get("https://example.test/gone").mock(return_value=httpx.Response(404, text="not found"))

    with pytest.raises(httpx.HTTPStatusError):
        _instant_client().get("https://example.test/gone")
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd scraper && pytest tests/test_http_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autosmart24.scraping.http_client'`

- [ ] **Step 3: Implement http_client.py**

```python
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Callable

import httpx

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

BLOCK_STATUS_CODES = {403, 429}


class BlockedError(Exception):
    def __init__(self, status_code: int, url: str):
        super().__init__(f"Blocked with status {status_code} fetching {url}")
        self.status_code = status_code
        self.url = url


@dataclass
class RateLimitedClient:
    min_delay_seconds: float = 3.0
    max_delay_seconds: float = 8.0
    client: httpx.Client = field(
        default_factory=lambda: httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept-Language": "it-IT,it;q=0.9"},
            timeout=15.0,
            follow_redirects=True,
        )
    )
    sleep_fn: Callable[[float], None] = field(default=time.sleep)

    def get(self, url: str) -> httpx.Response:
        delay = random.uniform(self.min_delay_seconds, self.max_delay_seconds)
        self.sleep_fn(delay)
        response = self.client.get(url)
        if response.status_code in BLOCK_STATUS_CODES:
            raise BlockedError(response.status_code, url)
        response.raise_for_status()
        return response

    def close(self) -> None:
        self.client.close()
```

- [ ] **Step 4: Run to confirm pass**

Run: `cd scraper && pytest tests/test_http_client.py -v`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add scraper/src/autosmart24/scraping/http_client.py scraper/tests/test_http_client.py
git commit -m "Add rate-limited HTTP client with 403/429 block detection"
```

---

## Task 10: Brand sweep orchestration (`crawler.py`)

Discovers a brand's full model list from the same JSON that every search page already returns (`pageProps.taxonomy.models[str(make_id)]` — verified live: for Fiat this returns every model with its numeric `value`/`label`, no hardcoded list needed), then for each model paginates (splitting by year range only if the model alone exceeds the pagination cap).

**Files:**
- Create: `scraper/src/autosmart24/scraping/crawler.py`
- Create: `scraper/tests/test_crawler.py`

**Interfaces:**
- Consumes: `RateLimitedClient` (Task 9), `build_search_url` (Task 7), `extract_next_data` (Task 4), `map_snippet_listing` (Task 5), `split_year_ranges` (Task 8), `MAX_RESULTS_PER_QUERY` (Task 1).
- Produces: `autosmart24.scraping.crawler.crawl_brand(client: RateLimitedClient, brand_slug: str, make_id: int) -> Iterator[dict]` yielding mapped snippet dicts (as from `map_snippet_listing`) for every listing found across the whole brand — consumed by `run_manager.py` (Task 13).

- [ ] **Step 1: Write the failing test**

`scraper/tests/test_crawler.py`:

```python
import json

import httpx
import respx

from autosmart24.scraping.crawler import crawl_brand
from autosmart24.scraping.http_client import RateLimitedClient
from autosmart24.scraping.search_query import build_search_url


def _next_data_html(page_props: dict) -> str:
    payload = {"props": {"pageProps": page_props}}
    return f'<html><body><script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script></body></html>'


def _fake_listing(listing_id: str, price: int) -> dict:
    return {
        "id": listing_id,
        "crossReferenceId": listing_id,
        "url": f"/annunci/{listing_id}",
        "price": {"priceRaw": price},
        "vehicle": {
            "make": "Fiat",
            "model": "Panda",
            "modelGroup": "Panda",
            "variant": None,
            "motorTypeName": "1.0",
            "modelVersionInput": None,
            "transmission": "Manuale",
            "fuel": "Benzina",
        },
        "location": {"city": "Roma - Roma - RM", "zip": "00100"},
        "seller": {"type": "Dealer", "companyName": "Test Dealer"},
        "tracking": {"firstRegistration": "01-2020", "mileage": "50000"},
    }


@respx.mock
def test_crawl_brand_yields_all_listings_across_pages():
    discovery_page_props = {
        "numberOfResults": 1,
        "numberOfPages": 1,
        "listings": [_fake_listing("discovery-1", 1000)],
        "taxonomy": {"models": {"28": [{"value": 1746, "label": "Panda"}]}},
    }
    model_page1_props = {
        "numberOfResults": 25,
        "numberOfPages": 2,
        "listings": [_fake_listing(f"p1-{i}", 10000 + i) for i in range(20)],
    }
    model_page2_props = {
        "numberOfResults": 25,
        "numberOfPages": 2,
        "listings": [_fake_listing(f"p2-{i}", 20000 + i) for i in range(5)],
    }

    discovery_url = build_search_url("fiat", page=1, make_id=28)
    page1_url = build_search_url("fiat", page=1, make_id=28, model_id=1746)
    page2_url = build_search_url("fiat", page=2, make_id=28, model_id=1746)

    respx.get(discovery_url).mock(return_value=httpx.Response(200, text=_next_data_html(discovery_page_props)))
    respx.get(page1_url).mock(return_value=httpx.Response(200, text=_next_data_html(model_page1_props)))
    respx.get(page2_url).mock(return_value=httpx.Response(200, text=_next_data_html(model_page2_props)))

    client = RateLimitedClient(min_delay_seconds=0, max_delay_seconds=0, sleep_fn=lambda _: None)
    results = list(crawl_brand(client, "fiat", 28))

    assert len(results) == 25
    assert {r["id"] for r in results} == {f"p1-{i}" for i in range(20)} | {f"p2-{i}" for i in range(5)}


@respx.mock
def test_crawl_brand_splits_by_year_when_model_exceeds_threshold():
    discovery_page_props = {
        "numberOfResults": 1,
        "numberOfPages": 1,
        "listings": [_fake_listing("discovery-1", 1000)],
        "taxonomy": {"models": {"28": [{"value": 1746, "label": "Panda"}]}},
    }
    probe_over_threshold_props = {"numberOfResults": 5000, "numberOfPages": 200, "listings": []}
    year_range_props = {
        "numberOfResults": 2,
        "numberOfPages": 1,
        "listings": [_fake_listing("y1", 5000), _fake_listing("y2", 6000)],
    }

    discovery_url = build_search_url("fiat", page=1, make_id=28)
    probe_url = build_search_url("fiat", page=1, make_id=28, model_id=1746)

    respx.get(discovery_url).mock(return_value=httpx.Response(200, text=_next_data_html(discovery_page_props)))
    respx.get(probe_url).mock(return_value=httpx.Response(200, text=_next_data_html(probe_over_threshold_props)))
    # Any year-range query (bisection probes + final leaf fetches) returns the same small result set.
    respx.get(url__regex=r".*fregfrom=.*").mock(return_value=httpx.Response(200, text=_next_data_html(year_range_props)))

    client = RateLimitedClient(min_delay_seconds=0, max_delay_seconds=0, sleep_fn=lambda _: None)
    results = list(crawl_brand(client, "fiat", 28))

    assert {r["id"] for r in results} == {"y1", "y2"}
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd scraper && pytest tests/test_crawler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autosmart24.scraping.crawler'`

- [ ] **Step 3: Implement crawler.py**

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from autosmart24.config import MAX_RESULTS_PER_QUERY
from autosmart24.scraping.http_client import RateLimitedClient
from autosmart24.scraping.next_data import extract_next_data
from autosmart24.scraping.search_query import build_search_url
from autosmart24.scraping.snippet_mapper import map_snippet_listing
from autosmart24.scraping.year_split import split_year_ranges

MIN_YEAR = 1950
MAX_YEAR = 2027


@dataclass
class ModelInfo:
    model_id: int
    label: str


def fetch_page_data(client: RateLimitedClient, url: str) -> dict:
    response = client.get(url)
    data = extract_next_data(response.text)
    return data["props"]["pageProps"]


def discover_models(client: RateLimitedClient, brand_slug: str, make_id: int) -> list[ModelInfo]:
    url = build_search_url(brand_slug, page=1, make_id=make_id)
    page_props = fetch_page_data(client, url)
    raw_models = page_props["taxonomy"]["models"].get(str(make_id), [])
    return [ModelInfo(model_id=m["value"], label=m["label"]) for m in raw_models]


def _count_for_year_range(
    client: RateLimitedClient, brand_slug: str, make_id: int, model_id: int, year_from: int, year_to: int
) -> int:
    url = build_search_url(
        brand_slug, page=1, make_id=make_id, model_id=model_id, year_from=year_from, year_to=year_to
    )
    return fetch_page_data(client, url)["numberOfResults"]


def _iter_listings_from_page(page_props: dict) -> Iterator[dict]:
    for raw_listing in page_props["listings"]:
        yield map_snippet_listing(raw_listing)


def _crawl_remaining_pages(
    client: RateLimitedClient,
    brand_slug: str,
    make_id: int,
    model_id: int,
    year_from: int | None,
    year_to: int | None,
    number_of_pages: int,
) -> Iterator[dict]:
    for page in range(2, number_of_pages + 1):
        url = build_search_url(
            brand_slug, page=page, make_id=make_id, model_id=model_id, year_from=year_from, year_to=year_to
        )
        yield from _iter_listings_from_page(fetch_page_data(client, url))


def _crawl_all_pages(
    client: RateLimitedClient,
    brand_slug: str,
    make_id: int,
    model_id: int,
    year_from: int | None,
    year_to: int | None,
) -> Iterator[dict]:
    url = build_search_url(
        brand_slug, page=1, make_id=make_id, model_id=model_id, year_from=year_from, year_to=year_to
    )
    page_props = fetch_page_data(client, url)
    yield from _iter_listings_from_page(page_props)
    yield from _crawl_remaining_pages(
        client, brand_slug, make_id, model_id, year_from, year_to, page_props["numberOfPages"]
    )


def crawl_brand(client: RateLimitedClient, brand_slug: str, make_id: int) -> Iterator[dict]:
    models = discover_models(client, brand_slug, make_id)

    for model in models:
        probe_url = build_search_url(brand_slug, page=1, make_id=make_id, model_id=model.model_id)
        probe_page_props = fetch_page_data(client, probe_url)
        total_results = probe_page_props["numberOfResults"]

        if total_results <= MAX_RESULTS_PER_QUERY:
            yield from _iter_listings_from_page(probe_page_props)
            yield from _crawl_remaining_pages(
                client, brand_slug, make_id, model.model_id, None, None, probe_page_props["numberOfPages"]
            )
        else:
            year_ranges = split_year_ranges(
                lambda yf, yt: _count_for_year_range(client, brand_slug, make_id, model.model_id, yf, yt),
                MIN_YEAR,
                MAX_YEAR,
                MAX_RESULTS_PER_QUERY,
            )
            for year_from, year_to in year_ranges:
                yield from _crawl_all_pages(client, brand_slug, make_id, model.model_id, year_from, year_to)
```

- [ ] **Step 4: Run to confirm pass**

Run: `cd scraper && pytest tests/test_crawler.py -v`
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add scraper/src/autosmart24/scraping/crawler.py scraper/tests/test_crawler.py
git commit -m "Add brand sweep orchestration: model discovery, pagination, year-split fallback"
```

---

## Task 11: Sweep diff logic — new / price-changed / missing (`change_detection.py`)

Pure function, no DB or HTTP involved: given this run's `{listing_id: price}` and the DB's currently-active `{listing_id: price}`, classify every id. Per spec §8, `missing_ids` are NOT sold yet — they're just candidates that `run_manager.py` (Task 13) must verify via the detail page before marking sold.

**Files:**
- Create: `scraper/src/autosmart24/scraping/change_detection.py`
- Create: `scraper/tests/test_change_detection.py`

**Interfaces:**
- Produces: `autosmart24.scraping.change_detection.SweepDiff` (dataclass: `new_ids: set[str]`, `price_changed: dict[str, int]`, `unchanged_ids: set[str]`, `missing_ids: set[str]`) and `.diff_sweep(current_prices: dict[str, int], active_db_prices: dict[str, int]) -> SweepDiff` — consumed by `run_manager.py` (Task 13).

- [ ] **Step 1: Write the failing test**

`scraper/tests/test_change_detection.py`:

```python
from autosmart24.scraping.change_detection import diff_sweep


def test_diff_sweep_classifies_new_changed_unchanged_missing():
    current = {"a": 1000, "b": 2500, "c": 3000}
    active_in_db = {"b": 2000, "c": 3000, "d": 4000}

    diff = diff_sweep(current, active_in_db)

    assert diff.new_ids == {"a"}
    assert diff.price_changed == {"b": 2500}
    assert diff.unchanged_ids == {"c"}
    assert diff.missing_ids == {"d"}


def test_diff_sweep_handles_empty_db():
    diff = diff_sweep({"a": 1000}, {})
    assert diff.new_ids == {"a"}
    assert diff.missing_ids == set()


def test_diff_sweep_handles_empty_sweep():
    diff = diff_sweep({}, {"a": 1000})
    assert diff.missing_ids == {"a"}
    assert diff.new_ids == set()
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd scraper && pytest tests/test_change_detection.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autosmart24.scraping.change_detection'`

- [ ] **Step 3: Implement change_detection.py**

```python
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SweepDiff:
    new_ids: set[str] = field(default_factory=set)
    price_changed: dict[str, int] = field(default_factory=dict)
    unchanged_ids: set[str] = field(default_factory=set)
    missing_ids: set[str] = field(default_factory=set)


def diff_sweep(current_prices: dict[str, int], active_db_prices: dict[str, int]) -> SweepDiff:
    diff = SweepDiff()

    for listing_id, price in current_prices.items():
        if listing_id not in active_db_prices:
            diff.new_ids.add(listing_id)
        elif active_db_prices[listing_id] != price:
            diff.price_changed[listing_id] = price
        else:
            diff.unchanged_ids.add(listing_id)

    diff.missing_ids = set(active_db_prices) - set(current_prices)
    return diff
```

- [ ] **Step 4: Run to confirm pass**

Run: `cd scraper && pytest tests/test_change_detection.py -v`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add scraper/src/autosmart24/scraping/change_detection.py scraper/tests/test_change_detection.py
git commit -m "Add pure sweep-diff classification: new, price-changed, unchanged, missing"
```

---

## Task 12: Detail-page fetch with sold-confirmation semantics (`detail_queue.py`)

Per spec §8: an id missing from a sweep is confirmed sold only if its detail page returns HTTP 404/410, OR returns 200 with JSON `status` ≠ `"Active"`. If the page is still `200` + `"Active"`, it's NOT sold (log an anomaly instead — spec explicitly forbids inferring "sold" from absence alone).

**Files:**
- Create: `scraper/src/autosmart24/scraping/detail_queue.py`
- Create: `scraper/tests/test_detail_queue.py`

**Interfaces:**
- Consumes: `RateLimitedClient` (Task 9), `extract_next_data` (Task 4), `map_detail_listing` (Task 6).
- Produces: `autosmart24.scraping.detail_queue.DetailResult` (dataclass: `sold: bool`, `data: dict | None`), `.fetch_detail(client: RateLimitedClient, url: str) -> DetailResult` (raises `BlockedError` on 403/429, same as `RateLimitedClient.get`) — consumed by `run_manager.py` (Task 13).

- [ ] **Step 1: Write the failing tests**

`scraper/tests/test_detail_queue.py`:

```python
from pathlib import Path

import httpx
import respx

from autosmart24.scraping.detail_queue import fetch_detail
from autosmart24.scraping.http_client import RateLimitedClient

FIXTURES = Path(__file__).parent / "fixtures"


def _client() -> RateLimitedClient:
    return RateLimitedClient(min_delay_seconds=0, max_delay_seconds=0, sleep_fn=lambda _: None)


@respx.mock
def test_fetch_detail_returns_data_when_active():
    html = (FIXTURES / "detail_fiat_grande_panda.html").read_text(encoding="utf-8")
    url = "https://www.autoscout24.it/annunci/fiat-grande-panda-test"
    respx.get(url).mock(return_value=httpx.Response(200, text=html))

    result = fetch_detail(_client(), url)

    assert result.sold is False
    assert result.data["brand"] == "Fiat"


@respx.mock
def test_fetch_detail_marks_sold_on_404():
    url = "https://www.autoscout24.it/annunci/gone"
    respx.get(url).mock(return_value=httpx.Response(404, text="not found"))

    result = fetch_detail(_client(), url)

    assert result.sold is True
    assert result.data is None


@respx.mock
def test_fetch_detail_marks_sold_when_status_not_active():
    html = (FIXTURES / "detail_fiat_grande_panda.html").read_text(encoding="utf-8")
    modified = html.replace('"status":"Active"', '"status":"Removed"')
    url = "https://www.autoscout24.it/annunci/removed"
    respx.get(url).mock(return_value=httpx.Response(200, text=modified))

    result = fetch_detail(_client(), url)

    assert result.sold is True
    assert result.data["source_status"] == "Removed"
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd scraper && pytest tests/test_detail_queue.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autosmart24.scraping.detail_queue'`

- [ ] **Step 3: Implement detail_queue.py**

```python
from __future__ import annotations

from dataclasses import dataclass

import httpx

from autosmart24.scraping.detail_mapper import map_detail_listing
from autosmart24.scraping.http_client import RateLimitedClient
from autosmart24.scraping.next_data import extract_next_data


@dataclass
class DetailResult:
    sold: bool
    data: dict | None = None


def fetch_detail(client: RateLimitedClient, url: str) -> DetailResult:
    try:
        response = client.get(url)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (404, 410):
            return DetailResult(sold=True)
        raise

    data = extract_next_data(response.text)
    listing_details = data["props"]["pageProps"]["listingDetails"]
    mapped = map_detail_listing(listing_details)

    if mapped["source_status"] != "Active":
        return DetailResult(sold=True, data=mapped)

    return DetailResult(sold=False, data=mapped)
```

- [ ] **Step 4: Run to confirm pass**

Run: `cd scraper && pytest tests/test_detail_queue.py -v`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add scraper/src/autosmart24/scraping/detail_queue.py scraper/tests/test_detail_queue.py
git commit -m "Add detail-page fetch with sold-confirmation semantics"
```

---

## Task 13: Run manager — full sweep + detail-backlog enrichment (`run_manager.py`)

Ties everything together for one brand's scheduled run:

1. Crawl the brand (Task 10), diff against DB (Task 11).
2. Persist: new listings + first price-history row; price changes + new price-history row; unchanged just bump timestamps.
3. For `missing_ids`, confirm via detail page (Task 12) before marking sold — if still active, log a `warning` event instead (never silently mark sold).
4. Process a bounded batch (default 50) of previously-new listings that still have `detail_scraped=False`, enriching them with full detail fields (power, displacement, body, coordinates, price-evaluation, etc.) — this is the "Fase dettaglio in background" from spec §5. Bounded batch size keeps each run's duration predictable; the backlog drains across successive runs.
5. Record a `ScrapeRun` row with counters, and `ScrapeEvent` rows for anomalies/blocks.

If `crawl_brand` raises `BlockedError`, the run is marked `blocked` and nothing else is written (no partial listing writes) — the next scheduled run retries from scratch.

**Files:**
- Create: `scraper/src/autosmart24/run_manager.py`
- Create: `scraper/tests/test_run_manager.py`

**Interfaces:**
- Consumes: `Listing, PriceHistory, ScrapeRun, ScrapeEvent` (Task 2), `BrandConfig` (Task 1), `diff_sweep` (Task 11), `crawl_brand` (Task 10), `fetch_detail, DetailResult` (Task 12), `RateLimitedClient, BlockedError` (Task 9).
- Produces: `autosmart24.run_manager.run_brand_sweep(session: Session, client: RateLimitedClient, brand: BrandConfig, crawl_fn=crawl_brand, fetch_detail_fn=fetch_detail) -> ScrapeRun` and `.process_detail_backlog(session, client, brand, run, batch_size=50, fetch_detail_fn=fetch_detail) -> None` (called internally by `run_brand_sweep`, also exported for direct testing) — consumed by `scheduler.py`/`api/app.py` (Tasks 14/16).

- [ ] **Step 1: Write the failing tests**

`scraper/tests/test_run_manager.py`:

```python
import datetime as dt

from autosmart24.config import BrandConfig
from autosmart24.db.models import Listing, PriceHistory, ScrapeEvent
from autosmart24.run_manager import run_brand_sweep
from autosmart24.scraping.detail_queue import DetailResult
from autosmart24.scraping.http_client import BlockedError, RateLimitedClient

BRAND = BrandConfig(slug="fiat", make_id=28, display_name="Fiat")


def _client() -> RateLimitedClient:
    return RateLimitedClient(min_delay_seconds=0, max_delay_seconds=0, sleep_fn=lambda _: None)


def _fake_snippet(listing_id: str, price: int) -> dict:
    return {
        "id": listing_id,
        "cross_reference_id": listing_id,
        "brand": "Fiat",
        "model": "Panda",
        "model_group": "Panda",
        "variant": None,
        "motor_type_name": "1.0",
        "version_input": None,
        "transmission": "Manuale",
        "fuel": "Benzina",
        "first_registration": dt.date(2020, 1, 1),
        "mileage_km": 50000,
        "seller_type": "Dealer",
        "seller_company_name": "Test Dealer",
        "city": "Roma - Roma - RM",
        "zip_code": "00100",
        "price": price,
        "url": f"https://www.autoscout24.it/annunci/{listing_id}",
        "raw_snippet": {"id": listing_id},
    }


def _existing_listing(listing_id: str, price: int, detail_scraped: bool = True) -> Listing:
    now = dt.datetime.utcnow()
    return Listing(
        id=listing_id, brand="Fiat", price=price, url=f"https://www.autoscout24.it/annunci/{listing_id}",
        first_seen_at=now, last_seen_at=now, last_checked_at=now, status="active", detail_scraped=detail_scraped,
    )


def test_run_brand_sweep_records_new_listing(db_session):
    def fake_crawl(client, brand_slug, make_id):
        yield _fake_snippet("new-1", 15000)

    run = run_brand_sweep(db_session, _client(), BRAND, crawl_fn=fake_crawl)

    assert run.status == "success"
    assert run.new_listings == 1
    listing = db_session.get(Listing, "new-1")
    assert listing is not None
    assert listing.status == "active"
    assert listing.price == 15000
    history = db_session.query(PriceHistory).filter_by(listing_id="new-1").all()
    assert len(history) == 1


def test_run_brand_sweep_detects_price_change(db_session):
    db_session.add(_existing_listing("existing-1", 10000))
    db_session.commit()

    def fake_crawl(client, brand_slug, make_id):
        yield _fake_snippet("existing-1", 12000)

    run = run_brand_sweep(db_session, _client(), BRAND, crawl_fn=fake_crawl)

    assert run.price_changes == 1
    listing = db_session.get(Listing, "existing-1")
    assert listing.price == 12000
    prices = [h.price for h in db_session.query(PriceHistory).filter_by(listing_id="existing-1").all()]
    assert 12000 in prices


def test_run_brand_sweep_confirms_sold_when_detail_confirms(db_session):
    db_session.add(_existing_listing("missing-1", 10000))
    db_session.commit()

    def fake_crawl(client, brand_slug, make_id):
        return iter(())

    def fake_fetch_detail(client, url):
        return DetailResult(sold=True)

    run = run_brand_sweep(db_session, _client(), BRAND, crawl_fn=fake_crawl, fetch_detail_fn=fake_fetch_detail)

    assert run.sold_detected == 1
    listing = db_session.get(Listing, "missing-1")
    assert listing.status == "sold"
    assert listing.sold_at is not None


def test_run_brand_sweep_keeps_active_when_detail_still_active(db_session):
    db_session.add(_existing_listing("anomaly-1", 10000))
    db_session.commit()

    def fake_crawl(client, brand_slug, make_id):
        return iter(())

    def fake_fetch_detail(client, url):
        return DetailResult(sold=False, data={"source_status": "Active"})

    run = run_brand_sweep(db_session, _client(), BRAND, crawl_fn=fake_crawl, fetch_detail_fn=fake_fetch_detail)

    assert run.sold_detected == 0
    listing = db_session.get(Listing, "anomaly-1")
    assert listing.status == "active"
    events = db_session.query(ScrapeEvent).filter_by(brand="Fiat").all()
    assert any(e.level == "warning" for e in events)


def test_run_brand_sweep_marks_blocked_on_blocked_error(db_session):
    def fake_crawl(client, brand_slug, make_id):
        yield _fake_snippet("x-1", 1000)
        raise BlockedError(403, "https://www.autoscout24.it/lst/fiat")

    run = run_brand_sweep(db_session, _client(), BRAND, crawl_fn=fake_crawl)

    assert run.status == "blocked"
    assert db_session.get(Listing, "x-1") is None


def test_run_brand_sweep_enriches_pending_detail_backlog(db_session):
    db_session.add(_existing_listing("pending-1", 10000, detail_scraped=False))
    db_session.commit()

    def fake_crawl(client, brand_slug, make_id):
        yield _fake_snippet("pending-1", 10000)

    def fake_fetch_detail(client, url):
        return DetailResult(
            sold=False,
            data={
                "price": 10500, "power_kw": 74, "power_cv": 101, "displacement_ccm": 1199,
                "body_type": "Berlina", "body_color": None, "num_seats": 5, "num_doors": 5,
                "num_previous_owners": None, "province": "TO", "latitude": 44.8, "longitude": 7.3,
                "vat_exposed": False, "price_evaluation_category": 1, "price_evaluation_median": 16100,
                "created_at_source": dt.datetime.utcnow(), "raw_detail": {"id": "pending-1"},
            },
        )

    run_brand_sweep(db_session, _client(), BRAND, crawl_fn=fake_crawl, fetch_detail_fn=fake_fetch_detail)

    listing = db_session.get(Listing, "pending-1")
    assert listing.detail_scraped is True
    assert listing.power_kw == 74
    assert listing.price == 10500
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd scraper && pytest tests/test_run_manager.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autosmart24.run_manager'`

- [ ] **Step 3: Implement run_manager.py**

```python
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from autosmart24.config import BrandConfig
from autosmart24.db.models import Listing, PriceHistory, ScrapeEvent, ScrapeRun
from autosmart24.scraping.change_detection import diff_sweep
from autosmart24.scraping.crawler import crawl_brand
from autosmart24.scraping.detail_queue import fetch_detail
from autosmart24.scraping.http_client import BlockedError, RateLimitedClient

DETAIL_BATCH_SIZE = 50


def _now() -> dt.datetime:
    return dt.datetime.utcnow()


def _log_event(session: Session, run: ScrapeRun, level: str, message: str, url: str | None = None) -> None:
    session.add(
        ScrapeEvent(run_id=run.id, brand=run.brand, level=level, message=message, url=url, created_at=_now())
    )


def process_detail_backlog(
    session: Session,
    client: RateLimitedClient,
    brand: BrandConfig,
    run: ScrapeRun,
    batch_size: int = DETAIL_BATCH_SIZE,
    fetch_detail_fn=fetch_detail,
) -> None:
    pending = session.execute(
        select(Listing)
        .where(Listing.brand == brand.display_name, Listing.status == "active", Listing.detail_scraped.is_(False))
        .order_by(Listing.first_seen_at.asc())
        .limit(batch_size)
    ).scalars().all()

    if not pending:
        return

    enriched = 0
    sold = 0
    now = _now()

    for row in pending:
        try:
            result = fetch_detail_fn(client, row.url)
        except BlockedError as exc:
            _log_event(session, run, "blocked", str(exc), url=exc.url)
            break

        row.last_checked_at = now
        if result.sold:
            row.status = "sold"
            row.sold_at = now
            sold += 1
            continue

        detail = result.data
        if detail["price"] is not None and detail["price"] != row.price:
            row.price = detail["price"]
            session.add(PriceHistory(listing_id=row.id, price=detail["price"], recorded_at=now))

        row.power_kw = detail["power_kw"]
        row.power_cv = detail["power_cv"]
        row.displacement_ccm = detail["displacement_ccm"]
        row.body_type = detail["body_type"]
        row.body_color = detail["body_color"]
        row.num_seats = detail["num_seats"]
        row.num_doors = detail["num_doors"]
        row.num_previous_owners = detail["num_previous_owners"]
        row.province = detail["province"]
        row.latitude = detail["latitude"]
        row.longitude = detail["longitude"]
        row.vat_exposed = detail["vat_exposed"]
        row.price_evaluation_category = detail["price_evaluation_category"]
        row.price_evaluation_median = detail["price_evaluation_median"]
        row.created_at_source = detail["created_at_source"]
        row.raw_detail = detail["raw_detail"]
        row.detail_scraped = True
        enriched += 1

    _log_event(
        session, run, "info",
        f"Detail backlog batch: enriched {enriched}, confirmed sold {sold} (batch size {len(pending)})",
    )


def run_brand_sweep(
    session: Session,
    client: RateLimitedClient,
    brand: BrandConfig,
    crawl_fn=crawl_brand,
    fetch_detail_fn=fetch_detail,
) -> ScrapeRun:
    run = ScrapeRun(brand=brand.display_name, started_at=_now(), status="running")
    session.add(run)
    session.flush()

    try:
        current_snippets: dict[str, dict] = {}
        for snippet in crawl_fn(client, brand.slug, brand.make_id):
            current_snippets[snippet["id"]] = snippet
    except BlockedError as exc:
        run.status = "blocked"
        run.finished_at = _now()
        _log_event(session, run, "blocked", str(exc), url=exc.url)
        session.commit()
        return run

    current_prices = {listing_id: s["price"] for listing_id, s in current_snippets.items()}

    active_rows = session.execute(
        select(Listing).where(Listing.brand == brand.display_name, Listing.status == "active")
    ).scalars().all()
    active_db_prices = {row.id: row.price for row in active_rows}
    active_rows_by_id = {row.id: row for row in active_rows}

    diff = diff_sweep(current_prices, active_db_prices)
    now = _now()

    for listing_id in diff.new_ids:
        snippet = current_snippets[listing_id]
        session.add(
            Listing(
                id=listing_id,
                cross_reference_id=snippet["cross_reference_id"],
                brand=snippet["brand"] or brand.display_name,
                model=snippet["model"],
                model_group=snippet["model_group"],
                variant=snippet["variant"],
                motor_type_name=snippet["motor_type_name"],
                version_input=snippet["version_input"],
                transmission=snippet["transmission"],
                fuel=snippet["fuel"],
                first_registration=snippet["first_registration"],
                mileage_km=snippet["mileage_km"],
                seller_type=snippet["seller_type"],
                seller_company_name=snippet["seller_company_name"],
                city=snippet["city"],
                zip_code=snippet["zip_code"],
                price=snippet["price"],
                url=snippet["url"],
                first_seen_at=now,
                last_seen_at=now,
                last_checked_at=now,
                status="active",
                detail_scraped=False,
                raw_snippet=snippet["raw_snippet"],
            )
        )
        session.add(PriceHistory(listing_id=listing_id, price=snippet["price"], recorded_at=now))

    for listing_id, new_price in diff.price_changed.items():
        row = active_rows_by_id[listing_id]
        row.price = new_price
        row.last_seen_at = now
        row.last_checked_at = now
        session.add(PriceHistory(listing_id=listing_id, price=new_price, recorded_at=now))

    for listing_id in diff.unchanged_ids:
        row = active_rows_by_id[listing_id]
        row.last_seen_at = now
        row.last_checked_at = now

    sold_count = 0
    for listing_id in diff.missing_ids:
        row = active_rows_by_id[listing_id]
        try:
            result = fetch_detail_fn(client, row.url)
        except BlockedError as exc:
            _log_event(session, run, "blocked", str(exc), url=exc.url)
            continue

        row.last_checked_at = now
        if result.sold:
            row.status = "sold"
            row.sold_at = now
            sold_count += 1
        else:
            _log_event(
                session, run, "warning",
                f"Listing {listing_id} not found in sweep but still active on detail page",
                url=row.url,
            )

    process_detail_backlog(session, client, brand, run, fetch_detail_fn=fetch_detail_fn)

    run.listings_seen = len(current_snippets)
    run.new_listings = len(diff.new_ids)
    run.price_changes = len(diff.price_changed)
    run.sold_detected = sold_count
    run.status = "success"
    run.finished_at = _now()

    session.commit()
    return run
```

- [ ] **Step 4: Run to confirm pass**

Run: `cd scraper && pytest tests/test_run_manager.py -v`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add scraper/src/autosmart24/run_manager.py scraper/tests/test_run_manager.py
git commit -m "Add run manager: full sweep persistence, sold confirmation, detail-backlog enrichment"
```

---

## Task 14: Per-brand job scheduler (`scheduler.py`)

Thin wrapper around APScheduler's `BackgroundScheduler` keyed by `brand.slug`, so pause/resume/force-run can address a specific brand's job. Tested via APScheduler's own introspection (`job.next_run_time is None` when paused) — no real waiting on intervals.

**Files:**
- Create: `scraper/src/autosmart24/scheduler.py`
- Create: `scraper/tests/test_scheduler.py`

**Interfaces:**
- Produces: `autosmart24.scheduler.BrandScheduler` (wraps `apscheduler.schedulers.background.BackgroundScheduler`; methods `schedule_brand(brand: BrandConfig, interval_days: float, run_fn) -> None`, `pause_brand(brand_slug: str) -> None`, `resume_brand(brand_slug: str) -> None`, `is_paused(brand_slug: str) -> bool`, `start() -> None`, `shutdown() -> None`) — consumed by `api/app.py` (Task 16).

- [ ] **Step 1: Write the failing tests**

`scraper/tests/test_scheduler.py`:

```python
from apscheduler.schedulers.background import BackgroundScheduler

from autosmart24.config import BrandConfig
from autosmart24.scheduler import BrandScheduler

BRAND = BrandConfig(slug="fiat", make_id=28, display_name="Fiat")


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

- [ ] **Step 2: Run to confirm failure**

Run: `cd scraper && pytest tests/test_scheduler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autosmart24.scheduler'`

- [ ] **Step 3: Implement scheduler.py**

```python
from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from autosmart24.config import BrandConfig


class BrandScheduler:
    def __init__(self, scheduler: BackgroundScheduler | None = None):
        self.scheduler = scheduler or BackgroundScheduler()

    def schedule_brand(self, brand: BrandConfig, interval_days: float, run_fn) -> None:
        self.scheduler.add_job(
            run_fn,
            trigger=IntervalTrigger(days=interval_days),
            id=brand.slug,
            replace_existing=True,
            args=[brand],
        )

    def pause_brand(self, brand_slug: str) -> None:
        self.scheduler.pause_job(brand_slug)

    def resume_brand(self, brand_slug: str) -> None:
        self.scheduler.resume_job(brand_slug)

    def is_paused(self, brand_slug: str) -> bool:
        job = self.scheduler.get_job(brand_slug)
        return job is not None and job.next_run_time is None

    def start(self) -> None:
        self.scheduler.start()

    def shutdown(self) -> None:
        self.scheduler.shutdown(wait=False)
```

- [ ] **Step 4: Run to confirm pass**

Run: `cd scraper && pytest tests/test_scheduler.py -v`
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add scraper/src/autosmart24/scheduler.py scraper/tests/test_scheduler.py
git commit -m "Add per-brand APScheduler wrapper with pause/resume"
```

---

## Task 15: FastAPI dashboard API (`api/main.py`, `api/schemas.py`)

Exposes brand status (incl. `slug`, needed by the frontend to address pause/resume/run-now), run history, event log, and controls. `create_app` takes `session_factory`, `scheduler`, and `run_now_fn` as parameters so tests can inject fakes without touching real scheduling/DB wiring (that's Task 16).

**Files:**
- Create: `scraper/src/autosmart24/api/__init__.py`
- Create: `scraper/src/autosmart24/api/schemas.py`
- Create: `scraper/src/autosmart24/api/main.py`
- Create: `scraper/tests/test_api.py`

**Interfaces:**
- Consumes: `MVP_BRANDS` (Task 1), `Listing/ScrapeRun/ScrapeEvent` (Task 2), `BrandScheduler` (Task 14).
- Produces: `autosmart24.api.main.create_app(session_factory, scheduler: BrandScheduler, run_now_fn: Callable[[BrandConfig], None]) -> FastAPI` with routes `GET /brands`, `GET /brands/{slug}/runs`, `GET /brands/{slug}/events`, `POST /brands/{slug}/pause`, `POST /brands/{slug}/resume`, `POST /brands/{slug}/run-now` — consumed by `api/app.py` (Task 16) and the dashboard (Tasks 17-18).

- [ ] **Step 1: Write the failing tests**

`scraper/tests/test_api.py`:

```python
import datetime as dt

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi.testclient import TestClient

from autosmart24.api.main import create_app
from autosmart24.config import MVP_BRANDS
from autosmart24.db.models import ScrapeRun
from autosmart24.scheduler import BrandScheduler


def _app_with_session(db_session, run_now_fn=None):
    scheduler = BrandScheduler(BackgroundScheduler())
    for brand in MVP_BRANDS:
        scheduler.schedule_brand(brand, interval_days=4, run_fn=lambda brand: None)

    app = create_app(
        session_factory=lambda: db_session,
        scheduler=scheduler,
        run_now_fn=run_now_fn or (lambda brand: None),
    )
    return app, scheduler


def test_list_brands_returns_all_mvp_brands_with_slug(db_session):
    app, _ = _app_with_session(db_session)
    client = TestClient(app)

    response = client.get("/brands")

    assert response.status_code == 200
    body = response.json()
    slugs = {row["slug"] for row in body}
    assert slugs == {b.slug for b in MVP_BRANDS}


def test_list_brands_reports_last_run(db_session):
    now = dt.datetime.utcnow()
    db_session.add(ScrapeRun(brand="Fiat", started_at=now, finished_at=now, status="success"))
    db_session.commit()

    app, _ = _app_with_session(db_session)
    client = TestClient(app)

    response = client.get("/brands")
    fiat_row = next(row for row in response.json() if row["slug"] == "fiat")
    assert fiat_row["last_run"]["status"] == "success"


def test_pause_and_resume_brand_via_api(db_session):
    app, scheduler = _app_with_session(db_session)
    client = TestClient(app)

    response = client.post("/brands/fiat/pause")
    assert response.status_code == 200
    assert scheduler.is_paused("fiat") is True

    response = client.post("/brands/fiat/resume")
    assert response.status_code == 200
    assert scheduler.is_paused("fiat") is False


def test_run_now_triggers_callback(db_session):
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
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd scraper && pytest tests/test_api.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autosmart24.api'`

- [ ] **Step 3: Implement schemas.py and main.py**

`scraper/src/autosmart24/api/__init__.py` — empty file.

`scraper/src/autosmart24/api/schemas.py`:

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
    brand: str
    slug: str
    paused: bool
    last_run: RunOut | None
```

`scraper/src/autosmart24/api/main.py`:

```python
from __future__ import annotations

from typing import Callable

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from autosmart24.api.schemas import BrandStatusOut, EventOut, RunOut
from autosmart24.config import BrandConfig, MVP_BRANDS
from autosmart24.db.models import ScrapeEvent, ScrapeRun
from autosmart24.scheduler import BrandScheduler


def _find_brand(brand_slug: str) -> BrandConfig:
    for brand in MVP_BRANDS:
        if brand.slug == brand_slug:
            return brand
    raise HTTPException(status_code=404, detail=f"Unknown brand: {brand_slug}")


def create_app(
    session_factory,
    scheduler: BrandScheduler,
    run_now_fn: Callable[[BrandConfig], None],
) -> FastAPI:
    app = FastAPI(title="AutoSmart24 Scraper API")

    def get_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    @app.get("/brands", response_model=list[BrandStatusOut])
    def list_brands(session: Session = Depends(get_session)):
        results = []
        for brand in MVP_BRANDS:
            last_run = session.execute(
                select(ScrapeRun)
                .where(ScrapeRun.brand == brand.display_name)
                .order_by(ScrapeRun.started_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            results.append(
                BrandStatusOut(
                    brand=brand.display_name,
                    slug=brand.slug,
                    paused=scheduler.is_paused(brand.slug),
                    last_run=RunOut.model_validate(last_run) if last_run else None,
                )
            )
        return results

    @app.get("/brands/{brand_slug}/runs", response_model=list[RunOut])
    def brand_runs(brand_slug: str, session: Session = Depends(get_session)):
        brand = _find_brand(brand_slug)
        rows = session.execute(
            select(ScrapeRun).where(ScrapeRun.brand == brand.display_name).order_by(ScrapeRun.started_at.desc())
        ).scalars().all()
        return [RunOut.model_validate(row) for row in rows]

    @app.get("/brands/{brand_slug}/events", response_model=list[EventOut])
    def brand_events(brand_slug: str, session: Session = Depends(get_session)):
        brand = _find_brand(brand_slug)
        rows = session.execute(
            select(ScrapeEvent).where(ScrapeEvent.brand == brand.display_name).order_by(ScrapeEvent.created_at.desc())
        ).scalars().all()
        return [EventOut.model_validate(row) for row in rows]

    @app.post("/brands/{brand_slug}/pause")
    def pause_brand(brand_slug: str):
        _find_brand(brand_slug)
        scheduler.pause_brand(brand_slug)
        return {"paused": True}

    @app.post("/brands/{brand_slug}/resume")
    def resume_brand(brand_slug: str):
        _find_brand(brand_slug)
        scheduler.resume_brand(brand_slug)
        return {"paused": False}

    @app.post("/brands/{brand_slug}/run-now")
    def run_now(brand_slug: str):
        brand = _find_brand(brand_slug)
        run_now_fn(brand)
        return {"triggered": True}

    return app
```

- [ ] **Step 4: Run to confirm pass**

Run: `cd scraper && pytest tests/test_api.py -v`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add scraper/src/autosmart24/api/__init__.py scraper/src/autosmart24/api/schemas.py scraper/src/autosmart24/api/main.py scraper/tests/test_api.py
git commit -m "Add FastAPI dashboard API: brand status, runs, events, pause/resume/run-now"
```

---

## Task 16: Production wiring, Dockerfile, docker-compose (`api/app.py`)

Single process runs both the FastAPI server AND the APScheduler background thread, so pause/resume/run-now act on the same in-memory scheduler that actually executes jobs (a separate worker container would have its own in-memory scheduler state, disconnected from the API's — this single-process design avoids that split-brain problem). Alembic (not `init_db`) owns schema creation in production; `init_db` remains test-only (SQLite).

**Files:**
- Create: `scraper/src/autosmart24/api/app.py`
- Create: `scraper/Dockerfile`
- Modify: `docker-compose.yml` (add the `app` service)

**Interfaces:**
- Consumes: `create_app` (Task 15), `BrandScheduler` (Task 14), `run_brand_sweep` (Task 13), `RateLimitedClient` (Task 9), `make_engine/make_session_factory` (Task 2).
- Produces: module-level `autosmart24.api.app.app` (a real `FastAPI` instance for uvicorn to import) with scheduler start/stop wired to FastAPI startup/shutdown events.

- [ ] **Step 1: Implement api/app.py**

```python
from __future__ import annotations

import os
import time

from autosmart24.api.main import create_app
from autosmart24.config import MVP_BRANDS
from autosmart24.db.session import make_engine, make_session_factory
from autosmart24.run_manager import run_brand_sweep
from autosmart24.scheduler import BrandScheduler
from autosmart24.scraping.http_client import RateLimitedClient

INTERVAL_DAYS = float(os.environ.get("SCRAPE_INTERVAL_DAYS", "4"))
MIN_DELAY_SECONDS = float(os.environ.get("SCRAPE_MIN_DELAY_SECONDS", "3"))
MAX_DELAY_SECONDS = float(os.environ.get("SCRAPE_MAX_DELAY_SECONDS", "8"))

engine = make_engine()
session_factory = make_session_factory(engine)
client = RateLimitedClient(min_delay_seconds=MIN_DELAY_SECONDS, max_delay_seconds=MAX_DELAY_SECONDS)
scheduler = BrandScheduler()


def _run_fn(brand):
    session = session_factory()
    try:
        run_brand_sweep(session, client, brand)
    finally:
        session.close()


def _run_now_fn(brand):
    scheduler.scheduler.add_job(_run_fn, args=[brand], trigger="date", id=f"manual-{brand.slug}-{int(time.time())}")


app = create_app(session_factory=session_factory, scheduler=scheduler, run_now_fn=_run_now_fn)


@app.on_event("startup")
def _start_scheduler():
    for brand in MVP_BRANDS:
        scheduler.schedule_brand(brand, interval_days=INTERVAL_DAYS, run_fn=_run_fn)
    scheduler.start()


@app.on_event("shutdown")
def _stop_scheduler():
    scheduler.shutdown()
```

- [ ] **Step 2: Write the Dockerfile**

`scraper/Dockerfile`:

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY alembic.ini .
COPY migrations ./migrations

ENV PYTHONPATH=/app/src

CMD ["sh", "-c", "python -m alembic upgrade head && uvicorn autosmart24.api.app:app --host 0.0.0.0 --port 8000"]
```

- [ ] **Step 3: Add the app service to docker-compose.yml**

Modify `docker-compose.yml`, adding under `services:` (after `postgres:`):

```yaml
  app:
    build: ./scraper
    depends_on:
      - postgres
    environment:
      DATABASE_URL: postgresql+psycopg://autosmart24:autosmart24@postgres:5432/autosmart24
      SCRAPE_INTERVAL_DAYS: "4"
      SCRAPE_MIN_DELAY_SECONDS: "3"
      SCRAPE_MAX_DELAY_SECONDS: "8"
    ports:
      - "8000:8000"
```

- [ ] **Step 4: Verify the full backend stack boots and serves real data**

Run: `docker compose up -d --build postgres app` then wait ~10s, then:
`curl http://localhost:8000/brands`
Expected: JSON array with 5 brand objects (`fiat`, `volkswagen`, `bmw`, `audi`, `mercedes-benz`), each `"last_run": null` (no runs yet).

- [ ] **Step 5: Commit**

```bash
git add scraper/src/autosmart24/api/app.py scraper/Dockerfile docker-compose.yml
git commit -m "Wire production FastAPI+scheduler entrypoint, Dockerfile, and docker-compose app service"
```

---

## Task 17: Dashboard scaffold + brand overview (Vite + React + TS)

**Files:**
- Create: `dashboard/package.json`
- Create: `dashboard/vite.config.ts`
- Create: `dashboard/tsconfig.json`
- Create: `dashboard/index.html`
- Create: `dashboard/src/setupTests.ts`
- Create: `dashboard/src/types.ts`
- Create: `dashboard/src/api.ts`
- Create: `dashboard/src/index.css`
- Create: `dashboard/src/components/BrandCard.tsx`
- Create: `dashboard/src/components/BrandCard.test.tsx`
- Create: `dashboard/src/App.tsx`
- Create: `dashboard/src/main.tsx`

**Interfaces:**
- Produces: `BrandStatusOut`/`RunOut`/`EventOut` TS types mirroring the API schemas (Task 15); `fetchBrands`, `pauseBrand`, `resumeBrand`, `runBrandNow` in `api.ts`; `<BrandCard>` component — consumed by `App.tsx` here and `BrandDetail.tsx` (Task 18).

- [ ] **Step 1: Scaffold package.json, config, and static entry files**

`dashboard/package.json`:

```json
{
  "name": "autosmart24-dashboard",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "test": "vitest run"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "recharts": "^2.12.7"
  },
  "devDependencies": {
    "@testing-library/jest-dom": "^6.4.6",
    "@testing-library/react": "^16.0.0",
    "@types/react": "^18.3.3",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.1",
    "jsdom": "^24.1.0",
    "typescript": "^5.5.3",
    "vite": "^5.3.4",
    "vitest": "^2.0.4"
  }
}
```

`dashboard/vite.config.ts`:

```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/setupTests.ts",
  },
});
```

`dashboard/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true
  },
  "include": ["src"]
}
```

`dashboard/index.html`:

```html
<!doctype html>
<html lang="it">
  <head>
    <meta charset="UTF-8" />
    <title>AutoSmart24 Scraper Dashboard</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

`dashboard/src/setupTests.ts`:

```ts
import "@testing-library/jest-dom";
```

`dashboard/src/index.css`:

```css
body {
  font-family: system-ui, sans-serif;
  background: #0f1115;
  color: #e5e7eb;
  margin: 0;
  padding: 24px;
}

.brand-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 16px;
}

.brand-card {
  background: #1a1d24;
  border-radius: 8px;
  padding: 16px;
}

.brand-card h3 {
  cursor: pointer;
  margin-top: 0;
}

.status-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 0.85em;
}

.status-attivo { background: #14532d; }
.status-in-pausa { background: #713f12; }
.status-bloccato { background: #7f1d1d; }
.status-in-esecuzione { background: #1e3a8a; }
```

- [ ] **Step 2: Write types.ts and api.ts**

`dashboard/src/types.ts`:

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
  brand: string;
  slug: string;
  paused: boolean;
  last_run: RunOut | null;
}
```

`dashboard/src/api.ts`:

```ts
import type { BrandStatusOut, EventOut, RunOut } from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`);
  if (!response.ok) {
    throw new Error(`Request to ${path} failed with status ${response.status}`);
  }
  return response.json() as Promise<T>;
}

async function postJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, { method: "POST" });
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
```

- [ ] **Step 3: Write the failing BrandCard test**

`dashboard/src/components/BrandCard.test.tsx`:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { BrandCard } from "./BrandCard";
import type { BrandStatusOut } from "../types";

const brand: BrandStatusOut = {
  brand: "Fiat",
  slug: "fiat",
  paused: false,
  last_run: {
    id: 1, brand: "Fiat", started_at: "2026-07-24T10:00:00Z", finished_at: "2026-07-24T10:05:00Z",
    status: "success", listings_seen: 100, new_listings: 5, price_changes: 3, sold_detected: 2, errors_count: 0,
  },
};

describe("BrandCard", () => {
  it("shows brand name and last run stats", () => {
    render(<BrandCard brand={brand} onPause={vi.fn()} onResume={vi.fn()} onRunNow={vi.fn()} onSelect={vi.fn()} />);
    expect(screen.getByText("Fiat")).toBeInTheDocument();
    expect(screen.getByText(/Nuovi annunci: 5/)).toBeInTheDocument();
  });

  it("calls onPause when pause button clicked", () => {
    const onPause = vi.fn();
    render(<BrandCard brand={brand} onPause={onPause} onResume={vi.fn()} onRunNow={vi.fn()} onSelect={vi.fn()} />);
    fireEvent.click(screen.getByText("Metti in pausa"));
    expect(onPause).toHaveBeenCalledWith("fiat");
  });

  it("shows resume button and paused status when brand is paused", () => {
    const pausedBrand = { ...brand, paused: true };
    render(<BrandCard brand={pausedBrand} onPause={vi.fn()} onResume={vi.fn()} onRunNow={vi.fn()} onSelect={vi.fn()} />);
    expect(screen.getByText("In pausa")).toBeInTheDocument();
    expect(screen.getByText("Riprendi")).toBeInTheDocument();
  });
});
```

- [ ] **Step 4: Run to confirm failure**

Run: `cd dashboard && npm install && npm test`
Expected: FAIL — `Cannot find module './BrandCard'`

- [ ] **Step 5: Implement BrandCard.tsx**

`dashboard/src/components/BrandCard.tsx`:

```tsx
import type { BrandStatusOut } from "../types";

interface BrandCardProps {
  brand: BrandStatusOut;
  onPause: (slug: string) => void;
  onResume: (slug: string) => void;
  onRunNow: (slug: string) => void;
  onSelect: (slug: string) => void;
}

function statusLabel(brand: BrandStatusOut): string {
  if (brand.paused) return "In pausa";
  if (brand.last_run?.status === "blocked") return "Bloccato";
  if (brand.last_run?.status === "running") return "In esecuzione";
  return "Attivo";
}

export function BrandCard({ brand, onPause, onResume, onRunNow, onSelect }: BrandCardProps) {
  const status = statusLabel(brand);

  return (
    <div className="brand-card" data-testid={`brand-card-${brand.slug}`}>
      <h3 onClick={() => onSelect(brand.slug)}>{brand.brand}</h3>
      <span className={`status-badge status-${status.toLowerCase().replace(" ", "-")}`}>{status}</span>
      {brand.last_run && (
        <ul>
          <li>Ultimo run: {new Date(brand.last_run.started_at).toLocaleString("it-IT")}</li>
          <li>Nuovi annunci: {brand.last_run.new_listings}</li>
          <li>Prezzi aggiornati: {brand.last_run.price_changes}</li>
          <li>Venduti rilevati: {brand.last_run.sold_detected}</li>
          <li>Errori: {brand.last_run.errors_count}</li>
        </ul>
      )}
      <div className="brand-card-actions">
        {brand.paused ? (
          <button onClick={() => onResume(brand.slug)}>Riprendi</button>
        ) : (
          <button onClick={() => onPause(brand.slug)}>Metti in pausa</button>
        )}
        <button onClick={() => onRunNow(brand.slug)}>Forza run ora</button>
      </div>
    </div>
  );
}
```

- [ ] **Step 6: Run to confirm pass**

Run: `cd dashboard && npm test`
Expected: `3 passed` (BrandCard.test.tsx)

- [ ] **Step 7: Write App.tsx and main.tsx (Overview page with polling)**

`dashboard/src/App.tsx`:

```tsx
import { useEffect, useState } from "react";
import { BrandCard } from "./components/BrandCard";
import { fetchBrands, pauseBrand, resumeBrand, runBrandNow } from "./api";
import type { BrandStatusOut } from "./types";

const POLL_INTERVAL_MS = 15000;

export function App() {
  const [brands, setBrands] = useState<BrandStatusOut[]>([]);
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);

  async function reload() {
    setBrands(await fetchBrands());
  }

  useEffect(() => {
    reload();
    const timer = setInterval(reload, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, []);

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
      {selectedSlug && (
        <p style={{ opacity: 0.7 }}>
          Dettaglio per "{selectedSlug}" — vedi BrandDetail (Task 18).
        </p>
      )}
    </div>
  );
}
```

`dashboard/src/main.tsx`:

```tsx
import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
```

- [ ] **Step 8: Verify the dev server boots**

Run: `cd dashboard && npm run dev -- --port 5173 &` then `curl -s http://localhost:5173 | grep -o "<title>.*</title>"` (stop the dev server afterward)
Expected: `<title>AutoSmart24 Scraper Dashboard</title>`

- [ ] **Step 9: Commit**

```bash
git add dashboard/package.json dashboard/vite.config.ts dashboard/tsconfig.json dashboard/index.html dashboard/src
git commit -m "Scaffold React dashboard: brand overview cards with pause/resume/run-now"
```

---

## Task 18: Brand detail view — run history chart + event log (`BrandDetail.tsx`)

**Files:**
- Create: `dashboard/src/components/BrandDetail.tsx`
- Create: `dashboard/src/components/BrandDetail.test.tsx`
- Modify: `dashboard/src/App.tsx` (render `<BrandDetail>` instead of the placeholder paragraph)

**Interfaces:**
- Consumes: `fetchBrandRuns`, `fetchBrandEvents` (Task 17's `api.ts`), `RunOut`, `EventOut` (Task 17's `types.ts`).
- Produces: `<BrandDetail brandSlug: string, onClose: () => void>` — consumed by `App.tsx`.

- [ ] **Step 1: Write the failing test**

`dashboard/src/components/BrandDetail.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { BrandDetail } from "./BrandDetail";
import * as api from "../api";

vi.mock("../api");

describe("BrandDetail", () => {
  it("renders events after loading", async () => {
    vi.mocked(api.fetchBrandRuns).mockResolvedValue([]);
    vi.mocked(api.fetchBrandEvents).mockResolvedValue([
      {
        id: 1, run_id: 1, brand: "Fiat", level: "warning", message: "Test event",
        url: null, created_at: "2026-07-24T10:00:00Z",
      },
    ]);

    render(<BrandDetail brandSlug="fiat" onClose={vi.fn()} />);

    await waitFor(() => expect(screen.getByText("Test event")).toBeInTheDocument());
  });
});
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd dashboard && npm test`
Expected: FAIL — `Cannot find module './BrandDetail'`

- [ ] **Step 3: Implement BrandDetail.tsx**

```tsx
import { useEffect, useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { fetchBrandEvents, fetchBrandRuns } from "../api";
import type { EventOut, RunOut } from "../types";

interface BrandDetailProps {
  brandSlug: string;
  onClose: () => void;
}

export function BrandDetail({ brandSlug, onClose }: BrandDetailProps) {
  const [runs, setRuns] = useState<RunOut[]>([]);
  const [events, setEvents] = useState<EventOut[]>([]);

  useEffect(() => {
    fetchBrandRuns(brandSlug).then(setRuns);
    fetchBrandEvents(brandSlug).then(setEvents);
  }, [brandSlug]);

  const chartData = [...runs].reverse().map((run) => ({
    date: new Date(run.started_at).toLocaleDateString("it-IT"),
    annunci: run.listings_seen,
    nuovi: run.new_listings,
    errori: run.errors_count,
  }));

  return (
    <div className="brand-detail" data-testid="brand-detail">
      <button onClick={onClose}>Chiudi</button>
      <h2>Dettaglio {brandSlug}</h2>

      <ResponsiveContainer width="100%" height={240}>
        <LineChart data={chartData}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="date" />
          <YAxis />
          <Tooltip />
          <Line type="monotone" dataKey="annunci" stroke="#60a5fa" />
          <Line type="monotone" dataKey="nuovi" stroke="#34d399" />
          <Line type="monotone" dataKey="errori" stroke="#f87171" />
        </LineChart>
      </ResponsiveContainer>

      <table>
        <thead>
          <tr>
            <th>Livello</th>
            <th>Messaggio</th>
            <th>Quando</th>
          </tr>
        </thead>
        <tbody>
          {events.map((event) => (
            <tr key={event.id} className={`event-${event.level}`}>
              <td>{event.level}</td>
              <td>{event.message}</td>
              <td>{new Date(event.created_at).toLocaleString("it-IT")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 4: Run to confirm pass**

Run: `cd dashboard && npm test`
Expected: `4 passed` (3 BrandCard + 1 BrandDetail)

- [ ] **Step 5: Wire BrandDetail into App.tsx**

In `dashboard/src/App.tsx`, replace:

```tsx
      {selectedSlug && (
        <p style={{ opacity: 0.7 }}>
          Dettaglio per "{selectedSlug}" — vedi BrandDetail (Task 18).
        </p>
      )}
```

with:

```tsx
      {selectedSlug && <BrandDetail brandSlug={selectedSlug} onClose={() => setSelectedSlug(null)} />}
```

and add the import at the top of the file:

```tsx
import { BrandDetail } from "./components/BrandDetail";
```

- [ ] **Step 6: Verify manually in the dev server**

Run: `cd dashboard && npm run dev -- --port 5173` (with `scraper` API also running per Task 16 Step 4), open `http://localhost:5173`, click a brand's name.
Expected: run-history chart and event table render below the brand grid without console errors.

- [ ] **Step 7: Commit**

```bash
git add dashboard/src/components/BrandDetail.tsx dashboard/src/components/BrandDetail.test.tsx dashboard/src/App.tsx
git commit -m "Add brand detail view: run-history chart and event log table"
```

---

## Task 19: Dashboard Docker wiring + end-to-end smoke test

**Files:**
- Create: `dashboard/Dockerfile`
- Create: `dashboard/nginx.conf`
- Modify: `docker-compose.yml` (add the `dashboard` service)

**Interfaces:**
- None (deployment wiring only; consumes the `app` service's port 8000 via `VITE_API_BASE_URL` baked in at build time).

- [ ] **Step 1: Write the Dockerfile**

`dashboard/Dockerfile`:

```dockerfile
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
```

- [ ] **Step 2: Write nginx.conf**

`dashboard/nginx.conf`:

```
server {
    listen 80;
    location / {
        root /usr/share/nginx/html;
        try_files $uri /index.html;
    }
}
```

- [ ] **Step 3: Add the dashboard service to docker-compose.yml**

Modify `docker-compose.yml`, adding under `services:` (after `app:`):

```yaml
  dashboard:
    build: ./dashboard
    depends_on:
      - app
    ports:
      - "5173:80"
```

- [ ] **Step 4: End-to-end smoke test of the whole stack**

Run: `docker compose up -d --build`
Wait ~20s, then:
- `curl http://localhost:8000/brands` → expect JSON array of 5 brands.
- `curl -s http://localhost:5173 | grep -o "<title>.*</title>"` → expect `<title>AutoSmart24 Scraper Dashboard</title>`.
- `curl -X POST http://localhost:8000/brands/fiat/run-now` → expect `{"triggered":true}`.
- Open `http://localhost:5173` in a browser, confirm the Fiat card shows a run appearing within a minute or two (the crawl of all Fiat models takes a while — a few models finishing is enough to confirm the pipeline is alive; watch for `blocked` status appearing, which would mean the rate limit needs to be raised per spec §6's calibration process).

- [ ] **Step 5: Commit**

```bash
git add dashboard/Dockerfile dashboard/nginx.conf docker-compose.yml
git commit -m "Add dashboard Docker service; complete end-to-end docker-compose stack"
```

---

## Self-review notes

- **Spec coverage:** §1-2 (goal/scope) → Task 1 (`MVP_BRANDS`); §3 (technical discovery) → Tasks 4-8 (JSON extraction, mapping, URL/split logic, all grounded in real fetched data); §4 (architecture) → Tasks 9, 14-16 (httpx client, scheduler, single-process API+worker); §5 (two-phase crawl) → Tasks 10 (snippet sweep) and 13 (`process_detail_backlog`); §6 (calibration) → Task 16 env vars (`SCRAPE_MIN/MAX_DELAY_SECONDS`, `SCRAPE_INTERVAL_DAYS`) adjustable without code changes; §7 (data model) → Task 2; §8 (change detection/sold logic) → Tasks 11-13; §9 (dashboard) → Tasks 15, 17-18; §10 (block handling) → Tasks 9, 13 (`BlockedError` propagation, `blocked` run status, dashboard visibility); §11 (testing) → every task's TDD cycle uses local fixtures/mocks, no live-network tests.
- **Placeholder scan:** no TBD/TODO; every step has runnable code and exact commands.
- **Type consistency verified:** `map_snippet_listing`/`map_detail_listing` output keys match exactly what `run_manager.py` reads; `Listing` model fields match both mappers' keys and the Alembic migration's columns; `BrandStatusOut.slug` added specifically so the frontend never needs to derive a slug from a display name.

