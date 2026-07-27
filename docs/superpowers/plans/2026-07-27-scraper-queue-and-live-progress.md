# Coda seriale e progresso live — Piano di implementazione

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rendere osservabile lo scraping — progresso live per fase, coda seriale con una marca alla volta, metriche di calibrazione — e adeguare la dashboard a 25 marche.

**Architecture:** Lo scraper persiste il proprio avanzamento su `scrape_runs` sfruttando i commit di batch già esistenti; le run vengono serializzate da un executor APScheduler a singolo worker con un controller che ferma la coda su `blocked`; l'API espone coda e metriche calcolando lato backend percentuali ed ETA; la dashboard consuma quei dati con polling adattivo.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0.35, Alembic 1.13.3, APScheduler 3.10.4, FastAPI 0.115.0, pytest 8.3.3, respx 0.21.1 · React 18.3, TypeScript 5.5, Vite 5.3, Vitest 2.0, Recharts 2.12

**Spec di riferimento:** `docs/superpowers/specs/2026-07-27-scraper-queue-and-live-progress-design.md`

## Global Constraints

- **Branch di lavoro:** `feature/scraper-queue-live-progress` (già creato, spec committata in `d8ff107`).
- **Identità git già configurata nel repo:** `vyperrrr99 <vperrone@gmail.com>`.
- **Nessun tool di sviluppo sul host:** non esistono `pip`, `node`, `npm`, né un venv Python. Tutti i test girano in container. `sudo` richiede password: i comandi con `sudo` vanno eseguiti dall'utente nel suo terminale.
- **Comando test backend** (monta il sorgente perché il Dockerfile non copia `tests/`):
  ```bash
  cd /home/vperrone/AutoSmart24 && sudo docker compose run --rm --no-deps \
    -v "$PWD/scraper:/app" app pytest tests/<file> -v
  ```
  I test usano SQLite in memoria (`tests/conftest.py`), quindi **non toccano il Postgres di produzione** e possono girare durante lo scraping.
- **Comando test frontend** (l'immagine `dashboard` è nginx e non contiene Node):
  ```bash
  cd /home/vperrone/AutoSmart24 && sudo docker run --rm \
    -v "$PWD/dashboard:/app" -w /app node:20-alpine \
    sh -c "npm install --silent && npx vitest run <file>"
  ```
- **Vincolo di deploy:** il rebuild del container `app` **interrompe le run in corso**. I Task 1-8 (backend) si scrivono e si testano liberamente, ma il deploy avviene solo nel Task 14, a batch fermo. Il container `dashboard` si ricostruisce senza impatto.
- **Lingua:** codice, commenti e commit in inglese; testi dell'interfaccia in italiano (come il codice esistente).
- **Convenzioni di test esistenti da seguire:** backend `pytest` con fixture `db_session`; frontend `vitest` + `@testing-library/react` con `vi.mock("../api")`.

## File Structure

**Backend — modificati**
- `scraper/src/autosmart24/db/models.py` — 5 colonne su `ScrapeRun`
- `scraper/migrations/versions/0007_run_progress.py` — **nuovo**, migrazione
- `scraper/src/autosmart24/run_manager.py` — scrive il progresso a ogni batch
- `scraper/src/autosmart24/queue_control.py` — **nuovo**, `QueueController`
- `scraper/src/autosmart24/scheduler.py` — executor a singolo worker
- `scraper/src/autosmart24/api/schemas.py` — `RunOut` esteso, schemi coda e metriche
- `scraper/src/autosmart24/api/main.py` — endpoint `/queue`, `/queue/resume`, `/brands/{slug}/metrics`
- `scraper/src/autosmart24/api/progress.py` — **nuovo**, calcolo percentuali/ETA (isolato per essere testabile senza DB)
- `scraper/src/autosmart24/api/app.py` — wiring del `QueueController`

**Frontend — modificati**
- `dashboard/src/types.ts` — tipi nuovi
- `dashboard/src/api.ts` — `fetchQueue`, `resumeQueue`, `fetchBrandMetrics`
- `dashboard/src/components/RunProgress.tsx` — **nuovo**
- `dashboard/src/components/QueuePanel.tsx` — **nuovo**
- `dashboard/src/components/BrandFilters.tsx` — **nuovo**
- `dashboard/src/components/BrandMetrics.tsx` — **nuovo**
- `dashboard/src/components/BrandCard.tsx` — usa `RunProgress`
- `dashboard/src/components/BrandDetail.tsx` — polling + `BrandMetrics`
- `dashboard/src/App.tsx` — integra `QueuePanel` e `BrandFilters`
- `dashboard/src/index.css` — stili dei nuovi componenti

Il calcolo di percentuali ed ETA vive in `api/progress.py`, separato dalle route: è pura aritmetica su valori già letti, quindi testabile senza database né client HTTP.

---

### Task 1: Colonne di progresso su `scrape_runs`

**Files:**
- Modify: `scraper/src/autosmart24/db/models.py:110-123`
- Create: `scraper/migrations/versions/0007_run_progress.py`
- Test: `scraper/tests/test_run_progress_models.py`

**Interfaces:**
- Produces: `ScrapeRun.phase`, `.search_finished_at`, `.search_total`, `.detail_total`, `.detail_enriched`

- [ ] **Step 1: Write the failing test**

```python
# scraper/tests/test_run_progress_models.py
import datetime as dt

from autosmart24.db.models import ScrapeRun


def test_scrape_run_accepts_progress_fields(db_session):
    run = ScrapeRun(
        brand="Fiat",
        started_at=dt.datetime(2026, 7, 27, 3, 0, 0),
        status="running",
        phase="search",
        search_finished_at=None,
        search_total=15776,
        detail_total=None,
        detail_enriched=0,
    )
    db_session.add(run)
    db_session.commit()

    stored = db_session.query(ScrapeRun).one()
    assert stored.phase == "search"
    assert stored.search_total == 15776
    assert stored.detail_enriched == 0


def test_scrape_run_progress_fields_default_to_empty(db_session):
    run = ScrapeRun(brand="Fiat", started_at=dt.datetime(2026, 7, 27, 3, 0, 0), status="running")
    db_session.add(run)
    db_session.commit()

    stored = db_session.query(ScrapeRun).one()
    assert stored.phase is None
    assert stored.search_finished_at is None
    assert stored.search_total is None
    assert stored.detail_total is None
    assert stored.detail_enriched == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `sudo docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/test_run_progress_models.py -v`
Expected: FAIL — `TypeError: 'phase' is an invalid keyword argument for ScrapeRun`

- [ ] **Step 3: Add the columns to the model**

In `models.py`, dentro `class ScrapeRun`, dopo `errors_count`:

```python
    phase: Mapped[str | None] = mapped_column(String(16), nullable=True)
    search_finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    search_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail_enriched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `sudo docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/test_run_progress_models.py -v`
Expected: PASS (2 test)

- [ ] **Step 5: Write the migration**

```python
# scraper/migrations/versions/0007_run_progress.py
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_run_progress"
down_revision = "0006_drop_raw_json"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("scrape_runs", sa.Column("phase", sa.String(16), nullable=True))
    op.add_column("scrape_runs", sa.Column("search_finished_at", sa.DateTime(), nullable=True))
    op.add_column("scrape_runs", sa.Column("search_total", sa.Integer(), nullable=True))
    op.add_column("scrape_runs", sa.Column("detail_total", sa.Integer(), nullable=True))
    # server_default backfills the rows already in production; it is dropped
    # afterwards so the application-side default is the only source of truth.
    op.add_column(
        "scrape_runs",
        sa.Column("detail_enriched", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column("scrape_runs", "detail_enriched", server_default=None)


def downgrade() -> None:
    op.drop_column("scrape_runs", "detail_enriched")
    op.drop_column("scrape_runs", "detail_total")
    op.drop_column("scrape_runs", "search_total")
    op.drop_column("scrape_runs", "search_finished_at")
    op.drop_column("scrape_runs", "phase")
```

- [ ] **Step 6: Commit**

```bash
git add scraper/src/autosmart24/db/models.py scraper/migrations/versions/0007_run_progress.py scraper/tests/test_run_progress_models.py
git commit -m "feat: add per-phase progress columns to scrape_runs"
```

---

### Task 2: Progresso persistito durante la fase di ricerca

**Files:**
- Modify: `scraper/src/autosmart24/run_manager.py:180-186` (creazione run), `:313-327` (loop batch), `:387-410` (except)
- Test: `scraper/tests/test_run_manager_progress.py`

**Interfaces:**
- Consumes: colonne del Task 1
- Produces: durante lo sweep `run.listings_seen` / `run.new_listings` / `run.price_changes` riflettono i batch già committati; `run.phase == "search"`

- [ ] **Step 1: Write the failing test**

```python
# scraper/tests/test_run_manager_progress.py
import datetime as dt

from sqlalchemy.orm import sessionmaker

from autosmart24.config import BrandConfig
from autosmart24.db.models import ScrapeRun
from autosmart24.run_manager import run_brand_sweep
from autosmart24.scraping.detail_queue import DetailResult
from autosmart24.scraping.http_client import RateLimitedClient

BRAND = BrandConfig(slug="fiat", make_id=28, display_name="Fiat")


def _client() -> RateLimitedClient:
    return RateLimitedClient(min_delay_seconds=0, max_delay_seconds=0, sleep_fn=lambda _: None)


def _noop_fetch_detail(client, url):
    return DetailResult(sold=False, data=None)


def _snippet(listing_id: str, price: int) -> dict:
    return {
        "id": listing_id, "cross_reference_id": listing_id, "brand": "Fiat",
        "model": "Panda", "model_group": "Panda", "variant": None,
        "motor_type_name": "1.0", "version_input": None, "transmission": "Manuale",
        "fuel": "Benzina", "first_registration": dt.date(2020, 1, 1), "mileage_km": 50000,
        "seller_type": "Dealer", "seller_company_name": "Test Dealer",
        "city": "Roma - Roma - RM", "zip_code": "00100", "price": price,
        "url": f"https://www.autoscout24.it/annunci/{listing_id}",
    }


def test_run_records_search_phase_progress_before_the_crawl_ends(db_session):
    """The run row must expose partial progress mid-sweep: this is what the
    dashboard polls. Before this change listings_seen stayed 0 until the end."""
    observed = {}

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        yield _snippet("a-1", 1000)
        yield _snippet("a-2", 2000)

        other = sessionmaker(bind=db_session.bind)()
        try:
            row = other.query(ScrapeRun).one()
            observed["phase"] = row.phase
            observed["listings_seen"] = row.listings_seen
            observed["new_listings"] = row.new_listings
        finally:
            other.close()

        yield _snippet("a-3", 3000)

    run_brand_sweep(
        db_session, _client, BRAND, crawl_fn=fake_crawl, batch_size=2,
        fetch_detail_fn=_noop_fetch_detail,
    )

    assert observed["phase"] == "search"
    assert observed["listings_seen"] == 2
    assert observed["new_listings"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `sudo docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/test_run_manager_progress.py -v`
Expected: FAIL — `assert None == 'search'` (la fase non è mai impostata)

- [ ] **Step 3: Set the phase at run creation**

In `run_brand_sweep`, sostituire la creazione della run:

```python
    run = ScrapeRun(brand=brand.display_name, started_at=_now(), status="running", phase="search")
```

- [ ] **Step 4: Persist counters inside the existing batch commit**

Nel loop dei batch, sostituire il blocco `session.commit()` seguito dall'aggiornamento dei contatori con:

```python
                seen_ids.update(batch_snippets.keys())
                # relisted_ids are ids from diff.new_ids that were treated as
                # UPDATEs to a pre-existing row above, not fresh inserts --
                # they must not inflate new_ids/run.new_listings.
                new_ids.update(diff.new_ids - relisted_ids)
                listings_seen += len(batch_snippets)
                price_changes += len(diff.price_changed)

                # Persist progress in the SAME commit as this batch's listing
                # changes: the dashboard polls these fields mid-run, and
                # committing them together means a failed commit rolls back
                # both, so the row can never claim progress that was not
                # durably written.
                run.listings_seen = listings_seen
                run.new_listings = len(new_ids)
                run.price_changes = price_changes
                session.commit()
```

- [ ] **Step 5: Stop reconciling counters in the failure paths**

Ora che ogni batch persiste i contatori, riassegnarli dalle variabili locali dopo un rollback reintrodurrebbe proprio il conteggio non committato che il commento originale voleva evitare (dopo `session.rollback()` l'oggetto `run` viene ricaricato con i valori dell'ultimo commit riuscito).

Nel blocco `except Exception`, rimuovere queste tre righe:

```python
        run.listings_seen = listings_seen
        run.new_listings = len(new_ids)
        run.price_changes = price_changes
```

e sostituire il commento esistente con:

```python
        # Counters are not reassigned here: every batch already persisted them
        # in its own commit, so after the rollback the row holds exactly what
        # was durably written.
```

Nel blocco `except BlockedError` della fase di ricerca, rimuovere allo stesso modo le tre assegnazioni `run.listings_seen/new_listings/price_changes`, lasciando `run.status`, `run.finished_at` e l'evento.

- [ ] **Step 6: Run test to verify it passes**

Run: `sudo docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/test_run_manager_progress.py -v`
Expected: PASS

- [ ] **Step 7: Run the full run_manager suite for regressions**

Run: `sudo docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/test_run_manager.py -v`
Expected: PASS (tutti i test esistenti, inclusi `test_run_brand_sweep_marks_error_and_preserves_partial_state_on_unexpected_exception` e `test_run_brand_sweep_preserves_committed_batches_on_block`)

- [ ] **Step 8: Commit**

```bash
git add scraper/src/autosmart24/run_manager.py scraper/tests/test_run_manager_progress.py
git commit -m "feat: persist search-phase progress on every batch commit"
```

---

### Task 3: Transizione di fase e progresso della fase di dettaglio

**Files:**
- Modify: `scraper/src/autosmart24/run_manager.py` (`process_detail_backlog` e `run_brand_sweep`)
- Test: `scraper/tests/test_run_manager_progress.py` (aggiunte)

**Interfaces:**
- Consumes: Task 1, Task 2
- Produces: `run.phase == "detail"`, `run.search_finished_at`, `run.detail_total`, `run.detail_enriched` aggiornati per pagina; `phase = None` a run conclusa

- [ ] **Step 1: Write the failing test**

Aggiungere a `scraper/tests/test_run_manager_progress.py`:

```python
from autosmart24.db.models import Listing


def _pending_listing(listing_id: str) -> Listing:
    now = dt.datetime(2026, 7, 27, 3, 0, 0)
    return Listing(
        id=listing_id, brand="Fiat", url=f"https://www.autoscout24.it/annunci/{listing_id}",
        first_seen_at=now, last_seen_at=now, last_checked_at=now,
        status="active", detail_scraped=False, price=1000,
        first_registration=dt.date(2020, 1, 1),
    )


def test_run_switches_to_detail_phase_and_counts_enriched(db_session):
    db_session.add(_pending_listing("d-1"))
    db_session.add(_pending_listing("d-2"))
    db_session.commit()

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        yield _snippet("d-1", 1000)
        yield _snippet("d-2", 1000)

    def fake_detail(client, url):
        return DetailResult(sold=False, data=_detail_payload())

    run = run_brand_sweep(
        db_session, _client, BRAND, crawl_fn=fake_crawl, fetch_detail_fn=fake_detail,
    )

    assert run.status == "success"
    assert run.search_finished_at is not None
    assert run.search_finished_at >= run.started_at
    assert run.detail_total == 2
    assert run.detail_enriched == 2
    # phase is cleared once the run is over, so the UI stops showing a bar
    assert run.phase is None


def test_run_marks_detail_phase_while_backlog_is_being_processed(db_session):
    db_session.add(_pending_listing("d-1"))
    db_session.commit()
    observed = {}

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        yield _snippet("d-1", 1000)

    def fake_detail(client, url):
        other = sessionmaker(bind=db_session.bind)()
        try:
            row = other.query(ScrapeRun).one()
            observed["phase"] = row.phase
            observed["detail_total"] = row.detail_total
        finally:
            other.close()
        return DetailResult(sold=False, data=_detail_payload())

    run_brand_sweep(db_session, _client, BRAND, crawl_fn=fake_crawl, fetch_detail_fn=fake_detail)

    assert observed["phase"] == "detail"
    assert observed["detail_total"] == 1
```

Aggiungere l'helper del payload di dettaglio in cima al file (i campi sono quelli letti da `process_detail_backlog`):

```python
def _detail_payload() -> dict:
    return {
        "price": 1000, "power_kw": 51, "power_cv": 69, "displacement_ccm": 999,
        "body_type": "Berlina", "body_color": "Bianco", "num_seats": 5, "num_doors": 5,
        "num_previous_owners": 1, "province": "RM", "latitude": 41.9, "longitude": 12.5,
        "vat_exposed": True, "price_evaluation_category": 1, "price_evaluation_median": 1000,
        "created_at_source": None, "had_accident": False, "has_full_service_history": True,
        "gears": 5, "drive_train": "Anteriore", "cylinders": 3, "weight_kg": 900,
        "co2_emissions_g_km": 120.0, "fuel_consumption_combined": 5.0,
        "fuel_consumption_urban": 6.0, "fuel_consumption_extra_urban": 4.5,
        "emission_class": "Euro 6", "upholstery": "Tessuto", "upholstery_color": "Nero",
        "is_conditional_price": False, "interaction_count": 10, "favorites_count": 2,
        "new_driver_suitable": True, "dealer": None,
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `sudo docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/test_run_manager_progress.py -v`
Expected: FAIL — `assert None is not None` su `search_finished_at`

- [ ] **Step 3: Mark the phase transition in `run_brand_sweep`**

Subito prima della chiamata a `process_detail_backlog` (dentro `if run.status != "blocked":`):

```python
        backlog_sold_count = 0
        if run.status != "blocked":
            run.phase = "detail"
            run.search_finished_at = _now()
            session.commit()
            backlog_sold_count = process_detail_backlog(
                session, client_factory, brand, run,
                concurrency=concurrency, session_refresh_requests=session_refresh_requests,
                fetch_detail_fn=fetch_detail_fn, year_from=year_from,
            )
```

- [ ] **Step 4: Count the backlog total and enriched rows**

In `process_detail_backlog`, subito dopo `failed_ids: set[str] = set()`, contare il totale una sola volta:

```python
    # Denominator for the dashboard's progress bar, counted once before the
    # first page: the same filters the paging query below uses.
    total_stmt = select(func.count()).select_from(Listing).where(
        Listing.brand == brand.display_name,
        Listing.status == "active",
        Listing.detail_scraped.is_(False),
        Listing.id.notin_(set(exclude_ids)),
    )
    if year_from is not None:
        total_stmt = total_stmt.where(
            or_(
                Listing.first_registration.is_(None),
                Listing.first_registration >= dt.date(year_from, 1, 1),
            )
        )
    run.detail_total = session.execute(total_stmt).scalar_one()
    session.commit()
```

Aggiungere `func` all'import esistente di SQLAlchemy in cima al file:

```python
from sqlalchemy import func, or_, select
```

Poi, dentro il loop, prima del `session.commit()` che chiude ogni pagina, accreditare gli arricchiti:

```python
        run.detail_enriched = (run.detail_enriched or 0) + enriched
        _log_event(
            session, run, "info",
            f"Detail backlog page: enriched {enriched}, confirmed sold {sold} (page size {len(pending)})",
        )
        session.commit()
```

Fare la stessa aggiunta nel ramo `except BlockedError` di `process_detail_backlog`, prima del suo `session.commit()`, così il progresso non si perde su blocco.

- [ ] **Step 5: Clear the phase when the run ends**

In `run_brand_sweep`, nel blocco finale che imposta lo stato:

```python
        run.listings_seen = listings_seen
        run.new_listings = len(new_ids)
        run.price_changes = price_changes
        run.sold_detected = sold_count + backlog_sold_count
        if run.status != "blocked":
            run.status = "success"
        run.phase = None
        run.finished_at = _now()
```

Impostare `run.phase = None` anche nel blocco `except Exception` (dopo `run.status = "error"`) e nei due rami `BlockedError`, così nessuna run conclusa resta con una fase attiva.

- [ ] **Step 6: Run test to verify it passes**

Run: `sudo docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/test_run_manager_progress.py -v`
Expected: PASS (4 test)

- [ ] **Step 7: Run the full backend suite**

Run: `sudo docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/ -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add scraper/src/autosmart24/run_manager.py scraper/tests/test_run_manager_progress.py
git commit -m "feat: track detail-phase progress and phase transitions on runs"
```

---

### Task 4: `QueueController` — ferma su blocco, prosegue su errore

**Files:**
- Create: `scraper/src/autosmart24/queue_control.py`
- Test: `scraper/tests/test_queue_control.py`

**Interfaces:**
- Produces: `QueueController` con `is_halted() -> bool`, `halt(reason: str) -> None`, `resume() -> None`, `state() -> QueueState`; `QueueState` è una dataclass con `halted: bool`, `reason: str | None`, `halted_at: datetime | None`

- [ ] **Step 1: Write the failing test**

```python
# scraper/tests/test_queue_control.py
import datetime as dt

from autosmart24.queue_control import QueueController


def test_new_controller_is_not_halted():
    controller = QueueController()

    assert controller.is_halted() is False
    assert controller.state().reason is None


def test_halt_records_reason_and_timestamp():
    controller = QueueController(now_fn=lambda: dt.datetime(2026, 7, 27, 4, 12, 0))

    controller.halt("blocco rilevato su Toyota")

    state = controller.state()
    assert state.halted is True
    assert state.reason == "blocco rilevato su Toyota"
    assert state.halted_at == dt.datetime(2026, 7, 27, 4, 12, 0)


def test_resume_clears_the_halt():
    controller = QueueController()
    controller.halt("blocco")

    controller.resume()

    state = controller.state()
    assert state.halted is False
    assert state.reason is None
    assert state.halted_at is None


def test_halt_keeps_the_first_reason():
    """The first block is the diagnostic one: later runs exiting early must
    not overwrite it with their own message."""
    controller = QueueController(now_fn=lambda: dt.datetime(2026, 7, 27, 4, 12, 0))
    controller.halt("blocco rilevato su Toyota")

    controller.halt("blocco rilevato su Kia")

    assert controller.state().reason == "blocco rilevato su Toyota"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `sudo docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/test_queue_control.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'autosmart24.queue_control'`

- [ ] **Step 3: Implement the controller**

```python
# scraper/src/autosmart24/queue_control.py
from __future__ import annotations

import datetime as dt
import threading
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class QueueState:
    halted: bool
    reason: str | None
    halted_at: dt.datetime | None


class QueueController:
    """Global stop switch for the scrape queue.

    A run that ends `blocked` halts the queue: with a rate-limited or banned
    IP, letting the remaining brands run would turn one block into a cascade
    of failures and deepen the block. A run that ends `error` leaves the
    queue alone -- that is an isolated fault, not a signal about the whole
    site. Resuming is deliberate and manual, from the dashboard.

    State is in-process, matching the scheduler it guards; a container
    restart clears it, which is acceptable because restarting is itself a
    manual act.
    """

    def __init__(self, now_fn: Callable[[], dt.datetime] = dt.datetime.utcnow) -> None:
        self._lock = threading.Lock()
        self._now_fn = now_fn
        self._halted = False
        self._reason: str | None = None
        self._halted_at: dt.datetime | None = None

    def is_halted(self) -> bool:
        with self._lock:
            return self._halted

    def halt(self, reason: str) -> None:
        with self._lock:
            # Keep the first reason: it names the run that actually hit the
            # block, which is the one worth showing to the operator.
            if self._halted:
                return
            self._halted = True
            self._reason = reason
            self._halted_at = self._now_fn()

    def resume(self) -> None:
        with self._lock:
            self._halted = False
            self._reason = None
            self._halted_at = None

    def state(self) -> QueueState:
        with self._lock:
            return QueueState(halted=self._halted, reason=self._reason, halted_at=self._halted_at)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `sudo docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/test_queue_control.py -v`
Expected: PASS (4 test)

- [ ] **Step 5: Commit**

```bash
git add scraper/src/autosmart24/queue_control.py scraper/tests/test_queue_control.py
git commit -m "feat: add QueueController to halt the queue on blocks"
```

---

### Task 5: Scheduler serializzato

**Files:**
- Modify: `scraper/src/autosmart24/scheduler.py:37-39`
- Test: `scraper/tests/test_scheduler.py` (aggiunte)

**Interfaces:**
- Produces: `BrandScheduler()` senza argomenti costruisce uno scheduler che esegue **una run alla volta**

- [ ] **Step 1: Write the failing test**

Aggiungere a `scraper/tests/test_scheduler.py`:

```python
import threading
import time


def test_default_scheduler_runs_one_job_at_a_time():
    """With 25 brands sharing the 03:00 trigger, APScheduler's default pool
    would run 10 concurrently -- around 60 parallel HTTP requests once each
    run opens its own worker pool. The queue must be serial."""
    scheduler = BrandScheduler()
    concurrent = []
    peak = []
    lock = threading.Lock()
    done = threading.Event()

    def slow_job(brand):
        with lock:
            concurrent.append(1)
            peak.append(len(concurrent))
        time.sleep(0.2)
        with lock:
            concurrent.pop()
            if len(peak) == 3:
                done.set()

    scheduler.scheduler.start()
    try:
        for i in range(3):
            scheduler.scheduler.add_job(slow_job, id=f"brand-{i}", args=[None])
        done.wait(timeout=10)
    finally:
        scheduler.shutdown()

    assert max(peak) == 1


def test_default_scheduler_tolerates_late_job_submission():
    """A job waiting behind a long run must not be discarded: the default
    misfire_grace_time of 1s would drop it."""
    scheduler = BrandScheduler()

    grace = scheduler.scheduler._job_defaults["misfire_grace_time"]

    assert grace >= 3600
```

- [ ] **Step 2: Run test to verify it fails**

Run: `sudo docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/test_scheduler.py -v -k "one_job_at_a_time or late_job"`
Expected: FAIL — `assert 3 == 1` (il pool di default ne esegue 10 in parallelo)

- [ ] **Step 3: Configure the executor**

In `scheduler.py`, sostituire il costruttore di `BrandScheduler`:

```python
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger


class BrandScheduler:
    def __init__(self, scheduler: BackgroundScheduler | None = None):
        # APScheduler's default pool runs 10 jobs at once. With every brand
        # sharing the 03:00 trigger that means up to 10 concurrent sweeps,
        # each opening SCRAPE_CONCURRENCY workers of its own -- roughly 60
        # parallel requests to autoscout24. One worker makes the queue
        # serial, keeping outbound concurrency at exactly one sweep's worth.
        #
        # misfire_grace_time must be generous for the same reason: a brand
        # queued behind a multi-hour sweep is submitted long after its
        # trigger time, and the 1s default would silently drop it.
        self.scheduler = scheduler or BackgroundScheduler(
            executors={"default": ThreadPoolExecutor(max_workers=1)},
            job_defaults={"misfire_grace_time": 3600, "max_instances": 1},
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `sudo docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/test_scheduler.py -v`
Expected: PASS (tutti, inclusi quelli preesistenti che passano uno scheduler esplicito)

- [ ] **Step 5: Commit**

```bash
git add scraper/src/autosmart24/scheduler.py scraper/tests/test_scheduler.py
git commit -m "feat: serialize scrape runs with a single-worker executor"
```

---

### Task 6: Wiring del controller nel run handler

**Files:**
- Modify: `scraper/src/autosmart24/api/app.py:67-95`
- Test: `scraper/tests/test_app_wiring.py` (aggiunte)

**Interfaces:**
- Consumes: `QueueController` (Task 4)
- Produces: `queue_controller` esportato da `app.py`; `_run_fn` esce senza richieste HTTP a coda ferma e chiama `halt()` quando una run termina `blocked`

- [ ] **Step 1: Write the failing test**

Aggiungere a `scraper/tests/test_app_wiring.py`:

```python
def test_run_fn_skips_work_when_the_queue_is_halted(monkeypatch, db_session):
    """A halted queue must cost zero HTTP requests: that is the whole point
    of halting after an IP block."""
    import autosmart24.api.app as app_module

    called = []
    monkeypatch.setattr(app_module, "run_brand_sweep", lambda *a, **k: called.append(1))
    monkeypatch.setattr(app_module, "session_factory", lambda: db_session)
    app_module.queue_controller.halt("blocco di prova")
    try:
        app_module._run_fn(BrandConfig(slug="fiat", make_id=28, display_name="Fiat"))
    finally:
        app_module.queue_controller.resume()

    assert called == []


def test_run_fn_halts_the_queue_when_a_sweep_reports_blocked(monkeypatch, db_session):
    import autosmart24.api.app as app_module
    from autosmart24.db.models import ScrapeRun

    def fake_sweep(session, client_factory, brand, **kwargs):
        return ScrapeRun(brand=brand.display_name, started_at=dt.datetime.utcnow(), status="blocked")

    monkeypatch.setattr(app_module, "run_brand_sweep", fake_sweep)
    monkeypatch.setattr(app_module, "session_factory", lambda: db_session)
    app_module.queue_controller.resume()

    app_module._run_fn(BrandConfig(slug="fiat", make_id=28, display_name="Fiat"))
    try:
        assert app_module.queue_controller.is_halted() is True
        assert "Fiat" in app_module.queue_controller.state().reason
    finally:
        app_module.queue_controller.resume()
```

Assicurarsi che il file importi `datetime as dt` e `BrandConfig`.

- [ ] **Step 2: Run test to verify it fails**

Run: `sudo docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/test_app_wiring.py -v -k halted`
Expected: FAIL — `AttributeError: module 'autosmart24.api.app' has no attribute 'queue_controller'`

- [ ] **Step 3: Wire the controller into `_run_fn`**

In `app.py`, dopo `run_guard = BrandRunGuard()`:

```python
from autosmart24.queue_control import QueueController

queue_controller = QueueController()
```

e sostituire `_run_fn`:

```python
def _run_fn(brand: BrandConfig) -> None:
    if queue_controller.is_halted():
        # Exit before opening a client: with the queue halted after a block,
        # every request we skip is one that would deepen the block.
        state = queue_controller.state()
        logger.warning("Skipping sweep for brand %s: queue halted (%s)", brand.slug, state.reason)
        session = session_factory()
        try:
            session.add(
                ScrapeEvent(
                    run_id=None, brand=brand.display_name, level="warning",
                    message=f"Run saltata: coda ferma ({state.reason})",
                    url=None, created_at=dt.datetime.utcnow(),
                )
            )
            session.commit()
        finally:
            session.close()
        return

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
            run = run_brand_sweep(
                session, _client_factory, brand,
                concurrency=CONCURRENCY, year_from=year_from, session_refresh_requests=SESSION_REFRESH_REQUESTS,
            )
            if run is not None and run.status == "blocked":
                queue_controller.halt(f"blocco rilevato su {brand.display_name}")
        finally:
            session.close()
    finally:
        run_guard.release(brand.slug)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `sudo docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/test_app_wiring.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scraper/src/autosmart24/api/app.py scraper/tests/test_app_wiring.py
git commit -m "feat: halt the queue on blocked sweeps and skip runs while halted"
```

---

### Task 7: Calcolo di percentuali ed ETA

**Files:**
- Create: `scraper/src/autosmart24/api/progress.py`
- Test: `scraper/tests/test_progress.py`

**Interfaces:**
- Produces:
  - `FALLBACK_SEARCH_RATE_PER_MIN = 911.0`, `FALLBACK_DETAIL_RATE_PER_MIN = 59.0`
  - `phase_progress(run) -> tuple[int, int | None]` → `(done, total)` della fase corrente
  - `percent(done: int, total: int | None) -> float | None`
  - `rates_from_history(runs: list) -> tuple[float, float, bool]` → `(search_rate, detail_rate, is_fallback)`
  - `eta_seconds(run, search_rate: float, detail_rate: float) -> int | None`
  - `run_metrics(run) -> dict | None` → riga di calibrazione per una run conclusa

- [ ] **Step 1: Write the failing test**

```python
# scraper/tests/test_progress.py
import datetime as dt

from autosmart24.api.progress import (
    FALLBACK_DETAIL_RATE_PER_MIN,
    eta_seconds,
    percent,
    phase_progress,
    rates_from_history,
    run_metrics,
)
from autosmart24.db.models import ScrapeRun


def _run(**kw) -> ScrapeRun:
    base = dict(
        brand="Fiat", started_at=dt.datetime(2026, 7, 27, 3, 0, 0), status="running",
        listings_seen=0, new_listings=0, price_changes=0, sold_detected=0, errors_count=0,
        phase=None, search_finished_at=None, search_total=None, detail_total=None, detail_enriched=0,
    )
    base.update(kw)
    return ScrapeRun(**base)


def test_phase_progress_uses_listings_seen_during_search():
    run = _run(phase="search", listings_seen=1200, search_total=7000)

    assert phase_progress(run) == (1200, 7000)


def test_phase_progress_uses_enriched_during_detail():
    run = _run(phase="detail", detail_enriched=340, detail_total=6800)

    assert phase_progress(run) == (340, 6800)


def test_percent_is_none_when_the_total_is_unknown():
    assert percent(120, None) is None


def test_percent_rounds_to_one_decimal():
    assert percent(1449, 6800) == 21.3


def test_percent_never_exceeds_one_hundred():
    """failed_ids can leave the denominator unreached, but a rerun of the
    same page must never push the bar past full."""
    assert percent(7000, 6800) == 100.0


def test_rates_fall_back_when_there_is_no_history():
    search, detail, is_fallback = rates_from_history([])

    assert is_fallback is True
    assert detail == FALLBACK_DETAIL_RATE_PER_MIN


def test_rates_are_derived_from_finished_runs():
    finished = _run(
        status="success",
        started_at=dt.datetime(2026, 7, 27, 3, 0, 0),
        search_finished_at=dt.datetime(2026, 7, 27, 3, 8, 0),   # 480s
        finished_at=dt.datetime(2026, 7, 27, 5, 0, 0),          # 6720s di dettaglio
        listings_seen=7200, detail_enriched=6720,
    )

    search, detail, is_fallback = rates_from_history([finished])

    assert is_fallback is False
    assert round(search) == 900   # 7200 annunci / 8 min
    assert round(detail) == 60    # 6720 annunci / 112 min


def test_eta_uses_the_remaining_items_of_the_current_phase():
    run = _run(phase="detail", detail_enriched=1000, detail_total=4000)

    # 3000 rimanenti a 60/min = 3000 secondi
    assert eta_seconds(run, search_rate=900.0, detail_rate=60.0) == 3000


def test_eta_is_none_without_a_total():
    run = _run(phase="detail", detail_enriched=1000, detail_total=None)

    assert eta_seconds(run, search_rate=900.0, detail_rate=60.0) is None


def test_run_metrics_reports_both_phases():
    finished = _run(
        status="success",
        started_at=dt.datetime(2026, 7, 27, 3, 0, 0),
        search_finished_at=dt.datetime(2026, 7, 27, 3, 8, 0),
        finished_at=dt.datetime(2026, 7, 27, 5, 0, 0),
        listings_seen=7200, detail_enriched=6720,
    )
    finished.id = 42

    metrics = run_metrics(finished)

    assert metrics["run_id"] == 42
    assert metrics["search_seconds"] == 480
    assert metrics["search_items"] == 7200
    assert round(metrics["search_rate_per_min"]) == 900
    assert metrics["detail_seconds"] == 6720
    assert round(metrics["detail_rate_per_min"]) == 60


def test_run_metrics_is_none_for_a_run_still_going():
    assert run_metrics(_run(phase="search")) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `sudo docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/test_progress.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'autosmart24.api.progress'`

- [ ] **Step 3: Implement the calculations**

```python
# scraper/src/autosmart24/api/progress.py
from __future__ import annotations

from autosmart24.db.models import ScrapeRun

# Measured on 2026-07-27 (Citroën, concurrency 6): 7256 listings in 478s of
# search, 6995 in 7152s of detail. Used only until a brand has finished runs
# of its own; responses flag it so the UI can call the estimate approximate.
FALLBACK_SEARCH_RATE_PER_MIN = 911.0
FALLBACK_DETAIL_RATE_PER_MIN = 59.0

MIN_RATE_PER_MIN = 1.0


def phase_progress(run: ScrapeRun) -> tuple[int, int | None]:
    """(done, total) for whichever phase the run is currently in."""
    if run.phase == "detail":
        return (run.detail_enriched or 0), run.detail_total
    return (run.listings_seen or 0), run.search_total


def percent(done: int, total: int | None) -> float | None:
    if not total or total <= 0:
        return None
    return min(100.0, round(done * 100.0 / total, 1))


def _seconds(start, end) -> int | None:
    if start is None or end is None:
        return None
    return int((end - start).total_seconds())


def _rate(items: int | None, seconds: int | None) -> float | None:
    if not items or not seconds or seconds <= 0:
        return None
    return items * 60.0 / seconds


def rates_from_history(runs: list[ScrapeRun]) -> tuple[float, float, bool]:
    """Average search/detail throughput over finished runs.

    Returns the fallback constants (and is_fallback=True) when no finished
    run carries usable timings, so a brand's first run still gets an ETA.
    """
    search_rates: list[float] = []
    detail_rates: list[float] = []
    for run in runs:
        search = _rate(run.listings_seen, _seconds(run.started_at, run.search_finished_at))
        detail = _rate(run.detail_enriched, _seconds(run.search_finished_at, run.finished_at))
        if search:
            search_rates.append(search)
        if detail:
            detail_rates.append(detail)

    if not search_rates and not detail_rates:
        return FALLBACK_SEARCH_RATE_PER_MIN, FALLBACK_DETAIL_RATE_PER_MIN, True

    search_avg = sum(search_rates) / len(search_rates) if search_rates else FALLBACK_SEARCH_RATE_PER_MIN
    detail_avg = sum(detail_rates) / len(detail_rates) if detail_rates else FALLBACK_DETAIL_RATE_PER_MIN
    return max(search_avg, MIN_RATE_PER_MIN), max(detail_avg, MIN_RATE_PER_MIN), False


def eta_seconds(run: ScrapeRun, search_rate: float, detail_rate: float) -> int | None:
    done, total = phase_progress(run)
    if not total or total <= 0:
        return None
    remaining = max(0, total - done)
    rate = detail_rate if run.phase == "detail" else search_rate
    return int(remaining * 60.0 / max(rate, MIN_RATE_PER_MIN))


def run_metrics(run: ScrapeRun) -> dict | None:
    """One calibration row for a finished run; None while it is still going."""
    if run.finished_at is None:
        return None
    search_seconds = _seconds(run.started_at, run.search_finished_at)
    detail_seconds = _seconds(run.search_finished_at, run.finished_at)
    return {
        "run_id": run.id,
        "started_at": run.started_at,
        "status": run.status,
        "search_seconds": search_seconds,
        "search_items": run.listings_seen,
        "search_rate_per_min": _rate(run.listings_seen, search_seconds),
        "detail_seconds": detail_seconds,
        "detail_items": run.detail_enriched,
        "detail_rate_per_min": _rate(run.detail_enriched, detail_seconds),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `sudo docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/test_progress.py -v`
Expected: PASS (12 test)

- [ ] **Step 5: Commit**

```bash
git add scraper/src/autosmart24/api/progress.py scraper/tests/test_progress.py
git commit -m "feat: add progress, ETA and calibration calculations"
```

---

### Task 8: Endpoint `/queue`, `/queue/resume`, `/brands/{slug}/metrics`

**Files:**
- Modify: `scraper/src/autosmart24/api/schemas.py`, `scraper/src/autosmart24/api/main.py`, `scraper/src/autosmart24/api/app.py`
- Test: `scraper/tests/test_api_queue.py`

**Interfaces:**
- Consumes: `QueueController` (Task 4), `progress.py` (Task 7)
- Produces: `create_app(..., queue_controller=...)`; `RunOut` con i 5 campi nuovi; risposte `QueueOut` e `RunMetricsOut`

- [ ] **Step 1: Write the failing test**

```python
# scraper/tests/test_api_queue.py
import datetime as dt

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi.testclient import TestClient

from autosmart24.api.main import create_app
from autosmart24.db.models import BrandCatalog, ScrapeRun, TrackedBrand
from autosmart24.queue_control import QueueController
from autosmart24.scheduler import BrandScheduler


def _seed(db_session, slug="fiat", make_id=28, display="Fiat"):
    now = dt.datetime.utcnow()
    db_session.add(BrandCatalog(make_id=make_id, display_name=display, slug=slug, synced_at=now))
    db_session.add(TrackedBrand(
        make_id=make_id, slug=slug, display_name=display, paused=False,
        year_from_years=10, schedule_day_of_week=None, schedule_hour=3,
        schedule_minute=0, created_at=now,
    ))
    db_session.commit()


def _app(db_session, controller=None):
    scheduler = BrandScheduler(BackgroundScheduler())
    app = create_app(
        session_factory=lambda: db_session,
        scheduler=scheduler,
        run_now_fn=lambda brand: None,
        run_fn=lambda brand: None,
        refresh_catalog_fn=lambda: [],
        queue_controller=controller or QueueController(),
    )
    return TestClient(app)


def test_queue_reports_idle_when_nothing_is_running(db_session):
    _seed(db_session)

    response = _app(db_session).get("/queue")

    assert response.status_code == 200
    body = response.json()
    assert body["halted"] is False
    assert body["current"] is None


def test_queue_reports_the_running_brand_with_percent_and_eta(db_session):
    _seed(db_session)
    db_session.add(ScrapeRun(
        brand="Fiat", started_at=dt.datetime.utcnow(), status="running",
        phase="detail", detail_enriched=1000, detail_total=4000,
    ))
    db_session.commit()

    body = _app(db_session).get("/queue").json()

    assert body["current"]["slug"] == "fiat"
    assert body["current"]["phase"] == "detail"
    assert body["current"]["done"] == 1000
    assert body["current"]["total"] == 4000
    assert body["current"]["percent"] == 25.0
    assert body["current"]["eta_is_fallback"] is True
    assert body["current"]["eta_seconds"] > 0


def test_queue_reports_halted_state(db_session):
    _seed(db_session)
    controller = QueueController()
    controller.halt("blocco rilevato su Fiat")

    body = _app(db_session, controller).get("/queue").json()

    assert body["halted"] is True
    assert body["halted_reason"] == "blocco rilevato su Fiat"


def test_resume_clears_the_halt(db_session):
    _seed(db_session)
    controller = QueueController()
    controller.halt("blocco")
    client = _app(db_session, controller)

    response = client.post("/queue/resume")

    assert response.status_code == 200
    assert controller.is_halted() is False


def test_brand_metrics_returns_one_row_per_finished_run(db_session):
    _seed(db_session)
    db_session.add(ScrapeRun(
        brand="Fiat",
        started_at=dt.datetime(2026, 7, 27, 3, 0, 0),
        search_finished_at=dt.datetime(2026, 7, 27, 3, 8, 0),
        finished_at=dt.datetime(2026, 7, 27, 5, 0, 0),
        status="success", listings_seen=7200, detail_enriched=6720,
    ))
    db_session.commit()

    body = _app(db_session).get("/brands/fiat/metrics").json()

    assert len(body) == 1
    assert body[0]["search_seconds"] == 480
    assert round(body[0]["detail_rate_per_min"]) == 60


def test_brand_metrics_skips_unfinished_runs(db_session):
    _seed(db_session)
    db_session.add(ScrapeRun(brand="Fiat", started_at=dt.datetime.utcnow(), status="running", phase="search"))
    db_session.commit()

    assert _app(db_session).get("/brands/fiat/metrics").json() == []


def test_run_out_exposes_progress_fields(db_session):
    _seed(db_session)
    db_session.add(ScrapeRun(
        brand="Fiat", started_at=dt.datetime.utcnow(), status="running",
        phase="detail", detail_enriched=5, detail_total=10, search_total=100,
    ))
    db_session.commit()

    body = _app(db_session).get("/brands").json()

    assert body[0]["last_run"]["phase"] == "detail"
    assert body[0]["last_run"]["detail_enriched"] == 5
    assert body[0]["last_run"]["detail_total"] == 10
    assert body[0]["last_run"]["search_total"] == 100
```

- [ ] **Step 2: Run test to verify it fails**

Run: `sudo docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/test_api_queue.py -v`
Expected: FAIL — `TypeError: create_app() got an unexpected keyword argument 'queue_controller'`

- [ ] **Step 3: Extend the schemas**

In `schemas.py`, aggiungere a `RunOut` (dopo `errors_count`):

```python
    phase: str | None = None
    search_finished_at: dt.datetime | None = None
    search_total: int | None = None
    detail_total: int | None = None
    detail_enriched: int = 0
```

e in fondo al file:

```python
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
```

- [ ] **Step 4: Add the routes**

In `main.py`, estendere la firma di `create_app` con `queue_controller` e aggiungere le route prima del `return app`:

```python
    @app.get("/queue", response_model=QueueOut)
    def get_queue(session: Session = Depends(get_session)):
        state = queue_controller.state()
        running = session.execute(
            select(ScrapeRun).where(ScrapeRun.status == "running").order_by(ScrapeRun.started_at.desc()).limit(1)
        ).scalar_one_or_none()

        current = None
        current_eta = None
        if running is not None:
            tracked = session.execute(
                select(TrackedBrand).where(TrackedBrand.display_name == running.brand)
            ).scalar_one_or_none()
            history = session.execute(
                select(ScrapeRun)
                .where(ScrapeRun.brand == running.brand, ScrapeRun.finished_at.is_not(None))
                .order_by(ScrapeRun.started_at.desc()).limit(5)
            ).scalars().all()
            search_rate, detail_rate, is_fallback = rates_from_history(list(history))
            done, total = phase_progress(running)
            current_eta = eta_seconds(running, search_rate, detail_rate)
            current = QueueCurrentOut(
                slug=tracked.slug if tracked else running.brand,
                brand=running.brand,
                phase=running.phase,
                done=done,
                total=total,
                percent=percent(done, total),
                eta_seconds=current_eta,
                eta_is_fallback=is_fallback,
                started_at=running.started_at,
            )

        # Pending = brands with a live, unpaused job, excluding the one running.
        pending: list[QueuePendingOut] = []
        rows = session.execute(select(TrackedBrand).order_by(TrackedBrand.slug)).scalars().all()
        position = 0
        total_eta = current_eta or 0
        for row in rows:
            if row.paused or (running is not None and row.display_name == running.brand):
                continue
            position += 1
            history = session.execute(
                select(ScrapeRun)
                .where(ScrapeRun.brand == row.display_name, ScrapeRun.finished_at.is_not(None))
                .order_by(ScrapeRun.started_at.desc()).limit(5)
            ).scalars().all()
            last = history[0] if history else None
            brand_eta = None
            if last is not None:
                search_rate, detail_rate, _ = rates_from_history(list(history))
                brand_eta = int(
                    (last.listings_seen or 0) * 60.0 / max(search_rate, 1.0)
                    + (last.detail_enriched or 0) * 60.0 / max(detail_rate, 1.0)
                )
                total_eta += brand_eta
            pending.append(
                QueuePendingOut(slug=row.slug, brand=row.display_name, position=position, eta_seconds=brand_eta)
            )

        return QueueOut(
            halted=state.halted,
            halted_reason=state.reason,
            halted_at=state.halted_at,
            current=current,
            pending=pending,
            total_eta_seconds=total_eta or None,
        )

    @app.post("/queue/resume")
    def resume_queue():
        queue_controller.resume()
        return {"halted": False}

    @app.get("/brands/{brand_slug}/metrics", response_model=list[RunMetricsOut])
    def brand_metrics(brand_slug: str, session: Session = Depends(get_session)):
        brand = _find_tracked_brand(session, brand_slug)
        rows = session.execute(
            select(ScrapeRun).where(ScrapeRun.brand == brand.display_name).order_by(ScrapeRun.started_at.desc())
        ).scalars().all()
        return [RunMetricsOut(**m) for row in rows if (m := run_metrics(row)) is not None]
```

Aggiungere gli import in cima a `main.py`:

```python
from autosmart24.api.progress import eta_seconds, percent, phase_progress, rates_from_history, run_metrics
from autosmart24.api.schemas import (
    ...,
    QueueCurrentOut,
    QueueOut,
    QueuePendingOut,
    RunMetricsOut,
)
from autosmart24.queue_control import QueueController
```

e la firma:

```python
def create_app(
    session_factory,
    scheduler: BrandScheduler,
    run_now_fn: Callable[[BrandConfig], None],
    run_fn: Callable[[BrandConfig], None],
    refresh_catalog_fn: Callable[[], list[CatalogEntry]],
    queue_controller: QueueController,
) -> FastAPI:
```

> **Nota:** `/brands/{brand_slug}/metrics` deve stare **dopo** le route statiche già presenti, ma essendo un suffisso distinto non collide con `/brands/apply-defaults`.

- [ ] **Step 5: Pass the controller from `app.py`**

In `app.py`, nella chiamata a `create_app`, aggiungere:

```python
    queue_controller=queue_controller,
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `sudo docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/test_api_queue.py tests/test_api.py -v`
Expected: PASS (nuovi + esistenti; `test_api.py` va aggiornato passando `queue_controller=QueueController()` in `_app_with_session`)

- [ ] **Step 7: Commit**

```bash
git add scraper/src/autosmart24/api/ scraper/tests/test_api_queue.py scraper/tests/test_api.py
git commit -m "feat: expose queue state and calibration metrics over the API"
```

---

### Task 9: Tipi e client API del frontend

**Files:**
- Modify: `dashboard/src/types.ts`, `dashboard/src/api.ts`
- Test: `dashboard/src/api.test.ts`

**Interfaces:**
- Produces: tipi `QueueOut`, `QueueCurrent`, `QueuePending`, `RunMetrics`; `RunOut` esteso; funzioni `fetchQueue()`, `resumeQueue()`, `fetchBrandMetrics(slug)`

- [ ] **Step 1: Write the failing test**

```tsx
// dashboard/src/api.test.ts
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { fetchQueue, resumeQueue, fetchBrandMetrics } from "./api";

describe("api queue endpoints", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ halted: false }) })));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("fetches the queue", async () => {
    const result = await fetchQueue();
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/queue"));
    expect(result).toEqual({ halted: false });
  });

  it("posts to resume the queue", async () => {
    await resumeQueue();
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/queue/resume"),
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("fetches brand metrics", async () => {
    await fetchBrandMetrics("fiat");
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/brands/fiat/metrics"));
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `sudo docker run --rm -v "$PWD/dashboard:/app" -w /app node:20-alpine sh -c "npm install --silent && npx vitest run src/api.test.ts"`
Expected: FAIL — `fetchQueue is not exported`

- [ ] **Step 3: Add the types**

In `types.ts`, estendere `RunOut` e aggiungere:

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
  phase: string | null;
  search_finished_at: string | null;
  search_total: number | null;
  detail_total: number | null;
  detail_enriched: number;
}

export interface QueueCurrent {
  slug: string;
  brand: string;
  phase: string | null;
  done: number;
  total: number | null;
  percent: number | null;
  eta_seconds: number | null;
  eta_is_fallback: boolean;
  started_at: string;
}

export interface QueuePending {
  slug: string;
  brand: string;
  position: number;
  eta_seconds: number | null;
}

export interface QueueOut {
  halted: boolean;
  halted_reason: string | null;
  halted_at: string | null;
  current: QueueCurrent | null;
  pending: QueuePending[];
  total_eta_seconds: number | null;
}

export interface RunMetrics {
  run_id: number;
  started_at: string;
  status: string;
  search_seconds: number | null;
  search_items: number | null;
  search_rate_per_min: number | null;
  detail_seconds: number | null;
  detail_items: number | null;
  detail_rate_per_min: number | null;
}
```

- [ ] **Step 4: Add the client functions**

In `api.ts`, aggiungere l'import dei tipi nuovi e in fondo:

```ts
export function fetchQueue(): Promise<QueueOut> {
  return getJson<QueueOut>("/queue");
}

export function resumeQueue(): Promise<{ halted: boolean }> {
  return postJson("/queue/resume");
}

export function fetchBrandMetrics(brandSlug: string): Promise<RunMetrics[]> {
  return getJson<RunMetrics[]>(`/brands/${brandSlug}/metrics`);
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `sudo docker run --rm -v "$PWD/dashboard:/app" -w /app node:20-alpine sh -c "npx vitest run src/api.test.ts"`
Expected: PASS (3 test)

- [ ] **Step 6: Commit**

```bash
git add dashboard/src/types.ts dashboard/src/api.ts dashboard/src/api.test.ts
git commit -m "feat: add queue and metrics types to the dashboard API client"
```

---

### Task 10: Componente `RunProgress`

**Files:**
- Create: `dashboard/src/components/RunProgress.tsx`, `dashboard/src/components/RunProgress.test.tsx`

**Interfaces:**
- Produces: `<RunProgress phase={string|null} done={number} total={number|null} etaSeconds={number|null} etaIsFallback={boolean} />`; esporta anche `formatDuration(seconds: number): string`

- [ ] **Step 1: Write the failing test**

```tsx
// dashboard/src/components/RunProgress.test.tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RunProgress, formatDuration } from "./RunProgress";

describe("formatDuration", () => {
  it("formats hours and minutes", () => {
    expect(formatDuration(5300)).toBe("1h 28m");
  });

  it("formats minutes only under an hour", () => {
    expect(formatDuration(480)).toBe("8m");
  });
});

describe("RunProgress", () => {
  it("shows phase, percent and eta", () => {
    render(<RunProgress phase="detail" done={1449} total={6800} etaSeconds={5300} etaIsFallback={false} />);

    expect(screen.getByText(/dettaglio/i)).toBeInTheDocument();
    expect(screen.getByText(/21,3%/)).toBeInTheDocument();
    expect(screen.getByText(/1h 28m/)).toBeInTheDocument();
  });

  it("shows an indeterminate bar when the total is unknown", () => {
    render(<RunProgress phase="search" done={120} total={null} etaSeconds={null} etaIsFallback={false} />);

    expect(screen.getByTestId("run-progress-bar")).toHaveAttribute("data-indeterminate", "true");
    expect(screen.getByText(/120 annunci/)).toBeInTheDocument();
  });

  it("flags a fallback estimate", () => {
    render(<RunProgress phase="detail" done={10} total={100} etaSeconds={600} etaIsFallback={true} />);

    expect(screen.getByText(/stima approssimativa/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `sudo docker run --rm -v "$PWD/dashboard:/app" -w /app node:20-alpine sh -c "npx vitest run src/components/RunProgress.test.tsx"`
Expected: FAIL — modulo non trovato

- [ ] **Step 3: Implement the component**

```tsx
// dashboard/src/components/RunProgress.tsx
interface RunProgressProps {
  phase: string | null;
  done: number;
  total: number | null;
  etaSeconds: number | null;
  etaIsFallback: boolean;
}

const PHASE_LABELS: Record<string, string> = {
  search: "ricerca",
  detail: "dettaglio",
};

export function formatDuration(seconds: number): string {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  return hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`;
}

export function RunProgress({ phase, done, total, etaSeconds, etaIsFallback }: RunProgressProps) {
  const label = phase ? PHASE_LABELS[phase] ?? phase : "in corso";
  // A null total is normal early in the search phase, before the crawler has
  // probed every model: show movement, not a made-up percentage.
  const percent = total && total > 0 ? Math.min(100, (done * 100) / total) : null;

  return (
    <div className="run-progress">
      <div className="run-progress-labels">
        <span className="run-progress-phase">{label}</span>
        {percent !== null ? (
          <span>{percent.toFixed(1).replace(".", ",")}%</span>
        ) : (
          <span>{done.toLocaleString("it-IT")} annunci</span>
        )}
        {etaSeconds !== null && <span>resta ~{formatDuration(etaSeconds)}</span>}
      </div>
      <div
        className="run-progress-track"
        data-testid="run-progress-bar"
        data-indeterminate={percent === null ? "true" : "false"}
      >
        <div className="run-progress-fill" style={{ width: percent === null ? "100%" : `${percent}%` }} />
      </div>
      {etaIsFallback && etaSeconds !== null && (
        <small className="run-progress-note">stima approssimativa (nessuno storico per questa marca)</small>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `sudo docker run --rm -v "$PWD/dashboard:/app" -w /app node:20-alpine sh -c "npx vitest run src/components/RunProgress.test.tsx"`
Expected: PASS (5 test)

- [ ] **Step 5: Add the styles**

In `dashboard/src/index.css`:

```css
.run-progress { margin: 0.5rem 0; }
.run-progress-labels { display: flex; gap: 0.75rem; font-size: 0.85rem; margin-bottom: 0.25rem; }
.run-progress-phase { text-transform: uppercase; letter-spacing: 0.04em; opacity: 0.75; }
.run-progress-track { background: #1f2937; border-radius: 999px; height: 8px; overflow: hidden; }
.run-progress-fill { background: #60a5fa; height: 100%; transition: width 0.4s ease; }
.run-progress-track[data-indeterminate="true"] .run-progress-fill {
  background: linear-gradient(90deg, #1f2937 0%, #60a5fa 50%, #1f2937 100%);
  animation: run-progress-slide 1.4s linear infinite;
}
@keyframes run-progress-slide { from { transform: translateX(-100%); } to { transform: translateX(100%); } }
.run-progress-note { opacity: 0.7; }
```

- [ ] **Step 6: Commit**

```bash
git add dashboard/src/components/RunProgress.tsx dashboard/src/components/RunProgress.test.tsx dashboard/src/index.css
git commit -m "feat: add RunProgress component with indeterminate state"
```

---

### Task 11: Componente `QueuePanel`

**Files:**
- Create: `dashboard/src/components/QueuePanel.tsx`, `dashboard/src/components/QueuePanel.test.tsx`

**Interfaces:**
- Consumes: `RunProgress` (Task 10), `fetchQueue`/`resumeQueue` (Task 9)
- Produces: `<QueuePanel queue={QueueOut | null} onResume={() => void} />`

- [ ] **Step 1: Write the failing test**

```tsx
// dashboard/src/components/QueuePanel.test.tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { QueuePanel } from "./QueuePanel";
import type { QueueOut } from "../types";

const RUNNING: QueueOut = {
  halted: false, halted_reason: null, halted_at: null,
  current: {
    slug: "opel", brand: "Opel", phase: "detail", done: 1449, total: 6800,
    percent: 21.3, eta_seconds: 5300, eta_is_fallback: false,
    started_at: "2026-07-27T14:15:33",
  },
  pending: [
    { slug: "toyota", brand: "Toyota", position: 1, eta_seconds: 7200 },
    { slug: "kia", brand: "Kia", position: 2, eta_seconds: 3600 },
  ],
  total_eta_seconds: 54000,
};

describe("QueuePanel", () => {
  it("shows the running brand and how many are waiting", () => {
    render(<QueuePanel queue={RUNNING} onResume={vi.fn()} />);

    expect(screen.getByText(/Opel/)).toBeInTheDocument();
    expect(screen.getByText(/2 marche in attesa/i)).toBeInTheDocument();
    expect(screen.getByText(/15h 0m/)).toBeInTheDocument();
  });

  it("shows an idle message when nothing is running", () => {
    render(
      <QueuePanel
        queue={{ halted: false, halted_reason: null, halted_at: null, current: null, pending: [], total_eta_seconds: null }}
        onResume={vi.fn()}
      />,
    );

    expect(screen.getByText(/nessuna scansione in corso/i)).toBeInTheDocument();
  });

  it("shows the halt banner with its reason and calls onResume", async () => {
    const onResume = vi.fn();
    render(
      <QueuePanel
        queue={{
          halted: true, halted_reason: "blocco rilevato su Toyota",
          halted_at: "2026-07-27T04:12:00", current: null, pending: [], total_eta_seconds: null,
        }}
        onResume={onResume}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent(/coda ferma/i);
    expect(screen.getByRole("alert")).toHaveTextContent(/blocco rilevato su Toyota/);

    await userEvent.click(screen.getByRole("button", { name: /riprendi coda/i }));

    expect(onResume).toHaveBeenCalled();
  });

  it("renders nothing while the queue is still loading", () => {
    const { container } = render(<QueuePanel queue={null} onResume={vi.fn()} />);

    expect(container).toBeEmptyDOMElement();
  });
});
```

- [ ] **Step 2: Install the missing test dependency**

`userEvent` non è ancora tra le dipendenze:

```bash
cd /home/vperrone/AutoSmart24 && sudo docker run --rm -v "$PWD/dashboard:/app" -w /app node:20-alpine \
  sh -c "npm install --save-dev --silent @testing-library/user-event@^14.5.2"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `sudo docker run --rm -v "$PWD/dashboard:/app" -w /app node:20-alpine sh -c "npx vitest run src/components/QueuePanel.test.tsx"`
Expected: FAIL — modulo `./QueuePanel` non trovato

- [ ] **Step 4: Implement the component**

```tsx
// dashboard/src/components/QueuePanel.tsx
import { RunProgress, formatDuration } from "./RunProgress";
import type { QueueOut } from "../types";

interface QueuePanelProps {
  queue: QueueOut | null;
  onResume: () => void;
}

export function QueuePanel({ queue, onResume }: QueuePanelProps) {
  if (queue === null) return null;

  if (queue.halted) {
    const at = queue.halted_at ? new Date(queue.halted_at).toLocaleTimeString("it-IT") : null;
    return (
      <section className="queue-panel queue-panel-halted">
        <div role="alert">
          <strong>Coda ferma</strong>
          {queue.halted_reason && <span>: {queue.halted_reason}</span>}
          {at && <span> (alle {at})</span>}
        </div>
        <button onClick={onResume}>Riprendi coda</button>
      </section>
    );
  }

  if (queue.current === null) {
    return (
      <section className="queue-panel">
        <span>Nessuna scansione in corso.</span>
        {queue.pending.length > 0 && <span> {queue.pending.length} marche in coda.</span>}
      </section>
    );
  }

  return (
    <section className="queue-panel">
      <div className="queue-panel-current">
        <strong>In esecuzione: {queue.current.brand}</strong>
        <RunProgress
          phase={queue.current.phase}
          done={queue.current.done}
          total={queue.current.total}
          etaSeconds={queue.current.eta_seconds}
          etaIsFallback={queue.current.eta_is_fallback}
        />
      </div>
      {queue.pending.length > 0 && (
        <div className="queue-panel-pending">
          {queue.pending.length} marche in attesa
          {queue.total_eta_seconds !== null && <> — totale ~{formatDuration(queue.total_eta_seconds)}</>}
        </div>
      )}
    </section>
  );
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `sudo docker run --rm -v "$PWD/dashboard:/app" -w /app node:20-alpine sh -c "npx vitest run src/components/QueuePanel.test.tsx"`
Expected: PASS (4 test)

- [ ] **Step 6: Add the styles**

```css
.queue-panel { background: #111827; border-radius: 8px; padding: 0.75rem 1rem; margin-bottom: 1rem; }
.queue-panel-halted { border: 1px solid #f87171; }
.queue-panel-halted button { margin-top: 0.5rem; }
.queue-panel-pending { opacity: 0.8; font-size: 0.9rem; margin-top: 0.5rem; }
```

- [ ] **Step 7: Commit**

```bash
git add dashboard/src/components/QueuePanel.tsx dashboard/src/components/QueuePanel.test.tsx dashboard/src/index.css dashboard/package.json dashboard/package-lock.json
git commit -m "feat: add QueuePanel showing the running brand and halt state"
```

---

### Task 12: Componente `BrandFilters`

**Files:**
- Create: `dashboard/src/components/BrandFilters.tsx`, `dashboard/src/components/BrandFilters.test.tsx`

**Interfaces:**
- Produces: `<BrandFilters query={string} status={BrandStatusFilter} onQueryChange={(v: string) => void} onStatusChange={(v: BrandStatusFilter) => void} />`; esporta `type BrandStatusFilter = "all" | "running" | "paused" | "error"` e `filterBrands(brands, query, status): BrandStatusOut[]`

- [ ] **Step 1: Write the failing test**

```tsx
// dashboard/src/components/BrandFilters.test.tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { BrandFilters, filterBrands } from "./BrandFilters";
import type { BrandStatusOut, RunOut } from "../types";

function brand(slug: string, over: Partial<BrandStatusOut> = {}): BrandStatusOut {
  return {
    make_id: 1, brand: slug.toUpperCase(), slug, paused: false, year_from_years: 10,
    schedule_day_of_week: null, schedule_hour: 3, schedule_minute: 0, last_run: null,
    ...over,
  };
}

function run(status: string): RunOut {
  return {
    id: 1, brand: "X", started_at: "2026-07-27T03:00:00", finished_at: null, status,
    listings_seen: 0, new_listings: 0, price_changes: 0, sold_detected: 0, errors_count: 0,
    phase: null, search_finished_at: null, search_total: null, detail_total: null, detail_enriched: 0,
  };
}

describe("filterBrands", () => {
  const brands = [
    brand("opel"),
    brand("toyota", { paused: true }),
    brand("kia", { last_run: run("running") }),
    brand("skoda", { last_run: run("error") }),
  ];

  it("matches on slug case-insensitively", () => {
    expect(filterBrands(brands, "OPE", "all").map((b) => b.slug)).toEqual(["opel"]);
  });

  it("filters paused brands", () => {
    expect(filterBrands(brands, "", "paused").map((b) => b.slug)).toEqual(["toyota"]);
  });

  it("filters running brands", () => {
    expect(filterBrands(brands, "", "running").map((b) => b.slug)).toEqual(["kia"]);
  });

  it("filters brands whose last run errored", () => {
    expect(filterBrands(brands, "", "error").map((b) => b.slug)).toEqual(["skoda"]);
  });

  it("returns everything with no filters", () => {
    expect(filterBrands(brands, "", "all")).toHaveLength(4);
  });
});

describe("BrandFilters", () => {
  it("reports typing", async () => {
    const onQueryChange = vi.fn();
    render(<BrandFilters query="" status="all" onQueryChange={onQueryChange} onStatusChange={vi.fn()} />);

    await userEvent.type(screen.getByRole("textbox", { name: /cerca marca/i }), "op");

    expect(onQueryChange).toHaveBeenCalled();
  });

  it("reports status changes", async () => {
    const onStatusChange = vi.fn();
    render(<BrandFilters query="" status="all" onQueryChange={vi.fn()} onStatusChange={onStatusChange} />);

    await userEvent.selectOptions(screen.getByRole("combobox", { name: /stato/i }), "paused");

    expect(onStatusChange).toHaveBeenCalledWith("paused");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `sudo docker run --rm -v "$PWD/dashboard:/app" -w /app node:20-alpine sh -c "npx vitest run src/components/BrandFilters.test.tsx"`
Expected: FAIL — modulo non trovato

- [ ] **Step 3: Implement the component**

```tsx
// dashboard/src/components/BrandFilters.tsx
import type { BrandStatusOut } from "../types";

export type BrandStatusFilter = "all" | "running" | "paused" | "error";

interface BrandFiltersProps {
  query: string;
  status: BrandStatusFilter;
  onQueryChange: (value: string) => void;
  onStatusChange: (value: BrandStatusFilter) => void;
}

const STATUS_OPTIONS: { value: BrandStatusFilter; label: string }[] = [
  { value: "all", label: "Tutte" },
  { value: "running", label: "In esecuzione" },
  { value: "paused", label: "In pausa" },
  { value: "error", label: "Con errori" },
];

export function filterBrands(
  brands: BrandStatusOut[],
  query: string,
  status: BrandStatusFilter,
): BrandStatusOut[] {
  const needle = query.trim().toLowerCase();
  return brands.filter((b) => {
    if (needle && !b.slug.toLowerCase().includes(needle) && !b.brand.toLowerCase().includes(needle)) {
      return false;
    }
    if (status === "paused") return b.paused;
    if (status === "running") return b.last_run?.status === "running";
    if (status === "error") return b.last_run?.status === "error" || b.last_run?.status === "blocked";
    return true;
  });
}

export function BrandFilters({ query, status, onQueryChange, onStatusChange }: BrandFiltersProps) {
  return (
    <div className="brand-filters">
      <label>
        Cerca marca
        <input type="text" value={query} onChange={(e) => onQueryChange(e.target.value)} />
      </label>
      <label>
        Stato
        <select value={status} onChange={(e) => onStatusChange(e.target.value as BrandStatusFilter)}>
          {STATUS_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      </label>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `sudo docker run --rm -v "$PWD/dashboard:/app" -w /app node:20-alpine sh -c "npx vitest run src/components/BrandFilters.test.tsx"`
Expected: PASS (7 test)

- [ ] **Step 5: Add the styles**

```css
.brand-filters { display: flex; gap: 1rem; margin-bottom: 1rem; flex-wrap: wrap; }
.brand-filters label { display: flex; flex-direction: column; font-size: 0.85rem; gap: 0.25rem; }
```

- [ ] **Step 6: Commit**

```bash
git add dashboard/src/components/BrandFilters.tsx dashboard/src/components/BrandFilters.test.tsx dashboard/src/index.css
git commit -m "feat: add brand search and status filters"
```

---

### Task 13: `BrandDetail` con polling e pannello di calibrazione

**Files:**
- Create: `dashboard/src/components/BrandMetrics.tsx`, `dashboard/src/components/BrandMetrics.test.tsx`
- Modify: `dashboard/src/components/BrandDetail.tsx`, `dashboard/src/components/BrandDetail.test.tsx`

**Interfaces:**
- Consumes: `fetchBrandMetrics` (Task 9), `RunProgress` (Task 10)
- Produces: `<BrandMetrics metrics={RunMetrics[]} />`; `BrandDetail` accetta la prop opzionale `pollIntervalMs`

- [ ] **Step 1: Write the failing tests**

```tsx
// dashboard/src/components/BrandMetrics.test.tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BrandMetrics } from "./BrandMetrics";

describe("BrandMetrics", () => {
  it("shows per-phase durations and rates", () => {
    render(
      <BrandMetrics
        metrics={[{
          run_id: 42, started_at: "2026-07-27T03:00:00", status: "success",
          search_seconds: 480, search_items: 7200, search_rate_per_min: 900,
          detail_seconds: 6720, detail_items: 6720, detail_rate_per_min: 60,
        }]}
      />,
    );

    expect(screen.getByText(/8m/)).toBeInTheDocument();
    expect(screen.getByText(/900/)).toBeInTheDocument();
    expect(screen.getByText(/60/)).toBeInTheDocument();
  });

  it("explains the empty state", () => {
    render(<BrandMetrics metrics={[]} />);

    expect(screen.getByText(/nessuna run conclusa/i)).toBeInTheDocument();
  });
});
```

In `BrandDetail.test.tsx`, il test esistente `renders events after loading` va **prima** aggiornato: aggiungendo `fetchBrandMetrics` alla `Promise.all` del componente, un mock che restituisce `undefined` farebbe crollare `BrandMetrics` su `metrics.length`. Aggiungere quindi la riga mancante al test esistente:

```tsx
    vi.mocked(api.fetchBrandMetrics).mockResolvedValue([]);
```

Poi aggiungere il nuovo test:

```tsx
it("refetches while the panel stays open", async () => {
  vi.mocked(api.fetchBrandRuns).mockResolvedValue([]);
  vi.mocked(api.fetchBrandEvents).mockResolvedValue([]);
  vi.mocked(api.fetchBrandMetrics).mockResolvedValue([]);

  render(<BrandDetail brandSlug="fiat" onClose={vi.fn()} pollIntervalMs={20} />);

  await waitFor(() => expect(vi.mocked(api.fetchBrandEvents).mock.calls.length).toBeGreaterThan(1));
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `sudo docker run --rm -v "$PWD/dashboard:/app" -w /app node:20-alpine sh -c "npx vitest run src/components/BrandMetrics.test.tsx src/components/BrandDetail.test.tsx"`
Expected: FAIL — `BrandMetrics` non esiste; `fetchBrandEvents` chiamata una sola volta

- [ ] **Step 3: Implement `BrandMetrics`**

```tsx
// dashboard/src/components/BrandMetrics.tsx
import { formatDuration } from "./RunProgress";
import type { RunMetrics } from "../types";

interface BrandMetricsProps {
  metrics: RunMetrics[];
}

function rate(value: number | null): string {
  return value === null ? "—" : `${Math.round(value)}/min`;
}

function duration(value: number | null): string {
  return value === null ? "—" : formatDuration(value);
}

export function BrandMetrics({ metrics }: BrandMetricsProps) {
  if (metrics.length === 0) {
    return <p className="brand-metrics-empty">Nessuna run conclusa: le metriche compaiono al primo giro completato.</p>;
  }

  return (
    <table className="brand-metrics">
      <thead>
        <tr>
          <th>Run</th><th>Stato</th>
          <th>Ricerca</th><th>Vel. ricerca</th>
          <th>Dettaglio</th><th>Vel. dettaglio</th>
        </tr>
      </thead>
      <tbody>
        {metrics.map((m) => (
          <tr key={m.run_id}>
            <td>{new Date(m.started_at).toLocaleString("it-IT")}</td>
            <td>{m.status}</td>
            <td>{duration(m.search_seconds)}</td>
            <td>{rate(m.search_rate_per_min)}</td>
            <td>{duration(m.detail_seconds)}</td>
            <td>{rate(m.detail_rate_per_min)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
```

- [ ] **Step 4: Add polling and the metrics panel to `BrandDetail`**

```tsx
import { useEffect, useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { fetchBrandEvents, fetchBrandMetrics, fetchBrandRuns } from "../api";
import { BrandMetrics } from "./BrandMetrics";
import { RunProgress } from "./RunProgress";
import type { EventOut, RunMetrics, RunOut } from "../types";

interface BrandDetailProps {
  brandSlug: string;
  onClose: () => void;
  pollIntervalMs?: number;
}

const DEFAULT_POLL_MS = 3000;

export function BrandDetail({ brandSlug, onClose, pollIntervalMs = DEFAULT_POLL_MS }: BrandDetailProps) {
  const [runs, setRuns] = useState<RunOut[]>([]);
  const [events, setEvents] = useState<EventOut[]>([]);
  const [metrics, setMetrics] = useState<RunMetrics[]>([]);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      const [nextRuns, nextEvents, nextMetrics] = await Promise.all([
        fetchBrandRuns(brandSlug),
        fetchBrandEvents(brandSlug),
        fetchBrandMetrics(brandSlug),
      ]);
      // The panel stays mounted across polls; drop late responses from a
      // previous brand so switching brands cannot show the wrong data.
      if (cancelled) return;
      setRuns(nextRuns);
      setEvents(nextEvents);
      setMetrics(nextMetrics);
    }

    load();
    const timer = setInterval(load, pollIntervalMs);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [brandSlug, pollIntervalMs]);

  const current = runs.find((run) => run.status === "running") ?? null;

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

      {current && (
        <RunProgress
          phase={current.phase}
          done={current.phase === "detail" ? current.detail_enriched : current.listings_seen}
          total={current.phase === "detail" ? current.detail_total : current.search_total}
          etaSeconds={null}
          etaIsFallback={false}
        />
      )}

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

      <h3>Calibrazione</h3>
      <BrandMetrics metrics={metrics} />

      <h3>Eventi</h3>
      <table>
        <thead>
          <tr><th>Livello</th><th>Messaggio</th><th>Quando</th></tr>
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

- [ ] **Step 5: Run tests to verify they pass**

Run: `sudo docker run --rm -v "$PWD/dashboard:/app" -w /app node:20-alpine sh -c "npx vitest run src/components/"`
Expected: PASS (inclusi i test preesistenti di `BrandCard` e `ManageBrands`)

- [ ] **Step 6: Commit**

```bash
git add dashboard/src/components/BrandMetrics.tsx dashboard/src/components/BrandMetrics.test.tsx dashboard/src/components/BrandDetail.tsx dashboard/src/components/BrandDetail.test.tsx
git commit -m "feat: poll brand detail and show calibration metrics"
```

---

### Task 14: Integrazione in `App` e deploy

**Files:**
- Modify: `dashboard/src/App.tsx`, `dashboard/src/components/BrandCard.tsx`
- Test: `dashboard/src/App.test.tsx`

**Interfaces:**
- Consumes: tutti i componenti dei Task 10-13

- [ ] **Step 1: Write the failing test**

```tsx
// dashboard/src/App.test.tsx
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { App } from "./App";
import * as api from "./api";

vi.mock("./api");

describe("App", () => {
  it("shows the queue panel and filters the brand grid", async () => {
    vi.mocked(api.fetchBrands).mockResolvedValue([
      {
        make_id: 54, brand: "Opel", slug: "opel", paused: false, year_from_years: 10,
        schedule_day_of_week: null, schedule_hour: 3, schedule_minute: 0, last_run: null,
      },
    ]);
    vi.mocked(api.fetchQueue).mockResolvedValue({
      halted: false, halted_reason: null, halted_at: null,
      current: {
        slug: "opel", brand: "Opel", phase: "detail", done: 10, total: 100,
        percent: 10, eta_seconds: 600, eta_is_fallback: false, started_at: "2026-07-27T14:00:00",
      },
      pending: [], total_eta_seconds: 600,
    });

    render(<App />);

    await waitFor(() => expect(screen.getByText(/In esecuzione: Opel/)).toBeInTheDocument());
    expect(screen.getByRole("textbox", { name: /cerca marca/i })).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `sudo docker run --rm -v "$PWD/dashboard:/app" -w /app node:20-alpine sh -c "npx vitest run src/App.test.tsx"`
Expected: FAIL — `In esecuzione: Opel` non presente

- [ ] **Step 3: Integrate into `App.tsx`**

Aggiungere gli import e lo stato, estendere `reload` e la vista Panoramica:

```tsx
import { BrandFilters, filterBrands, type BrandStatusFilter } from "./components/BrandFilters";
import { QueuePanel } from "./components/QueuePanel";
import { fetchBrands, fetchQueue, pauseBrand, resumeBrand, resumeQueue, runBrandNow } from "./api";
import type { BrandStatusOut, QueueOut } from "./types";
```

```tsx
  const [queue, setQueue] = useState<QueueOut | null>(null);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<BrandStatusFilter>("all");

  async function reload() {
    const [nextBrands, nextQueue] = await Promise.all([fetchBrands(), fetchQueue()]);
    setBrands(nextBrands);
    setQueue(nextQueue);
  }

  async function handleResumeQueue() {
    await resumeQueue();
    await reload();
  }
```

Il polling adattivo esistente deve tenere conto anche della coda, non solo di `last_run`:

```tsx
  useEffect(() => {
    const hasActiveRun = queue?.current != null || brands.some((b) => b.last_run?.status === "running");
    const interval = hasActiveRun ? POLL_INTERVAL_ACTIVE_MS : POLL_INTERVAL_IDLE_MS;
    const timer = setInterval(reload, interval);
    return () => clearInterval(timer);
  }, [brands, queue]);
```

e nella vista Panoramica:

```tsx
      {view === "overview" && (
        <>
          <QueuePanel queue={queue} onResume={handleResumeQueue} />
          <BrandFilters
            query={query}
            status={statusFilter}
            onQueryChange={setQuery}
            onStatusChange={setStatusFilter}
          />
          <div className="brand-grid">
            {filterBrands(brands, query, statusFilter).map((brand) => (
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
```

- [ ] **Step 4: Show progress on the brand card**

In `BrandCard.tsx`, importare `RunProgress` e inserirlo subito dopo il badge di stato:

```tsx
      {brand.last_run?.status === "running" && (
        <RunProgress
          phase={brand.last_run.phase}
          done={brand.last_run.phase === "detail" ? brand.last_run.detail_enriched : brand.last_run.listings_seen}
          total={brand.last_run.phase === "detail" ? brand.last_run.detail_total : brand.last_run.search_total}
          etaSeconds={null}
          etaIsFallback={false}
        />
      )}
```

- [ ] **Step 5: Run the whole frontend suite**

Run: `sudo docker run --rm -v "$PWD/dashboard:/app" -w /app node:20-alpine sh -c "npx vitest run"`
Expected: PASS (tutti)

- [ ] **Step 6: Type-check the build**

Run: `sudo docker run --rm -v "$PWD/dashboard:/app" -w /app node:20-alpine sh -c "npx tsc -b"`
Expected: nessun errore

- [ ] **Step 7: Commit**

```bash
git add dashboard/src/App.tsx dashboard/src/App.test.tsx dashboard/src/components/BrandCard.tsx
git commit -m "feat: wire queue panel, filters and card progress into the dashboard"
```

- [ ] **Step 8: Deploy — solo a scraping fermo**

⚠️ Verificare prima che nessuna run sia in corso:

```bash
curl -s http://localhost:8001/brands | python3 -c "import sys,json; print([b['slug'] for b in json.load(sys.stdin) if (b.get('last_run') or {}).get('status')=='running'] or 'nessuna run attiva')"
```

Se il risultato è `nessuna run attiva`, procedere:

```bash
cd /home/vperrone/AutoSmart24
sudo docker compose build app dashboard
sudo docker compose up -d app dashboard
sudo docker compose logs --tail=30 app
```

La migrazione `0007_run_progress` viene applicata automaticamente dal `CMD` del container (`alembic upgrade head`).

- [ ] **Step 9: Verify against the live stack**

```bash
curl -s http://localhost:8001/queue | head -c 400
curl -s -o /dev/null -w "dashboard %{http_code}\n" http://localhost:5173/
```

Expected: `/queue` risponde con `halted: false` e l'elenco `pending`; la dashboard risponde 200.

- [ ] **Step 10: Commit finale**

```bash
git commit --allow-empty -m "chore: deploy queue and live progress to the local stack"
```

---

## Note di esecuzione

- I Task 1-8 (backend) possono essere **scritti e testati** mentre lo scraping gira: i test usano SQLite in memoria e non toccano il Postgres di produzione. Solo il Task 14 Step 8 richiede lo stack fermo.
- I Task 9-13 (frontend) sono indipendenti dal backend fino all'integrazione: si possono eseguire in parallelo ai Task 1-8 se si lavora a due mani.
- Dopo il deploy, ricordare che tutte le 25 marche sono attualmente `paused=True`: la coda notturna riprenderà solo riattivandole dalla dashboard.
