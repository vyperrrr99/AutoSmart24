# Resilienza dello scraper — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Un elemento difettoso — una pagina irraggiungibile, un annuncio non scrivibile — non deve più far cadere la scansione di un'intera marca.

**Architecture:** Il pool di worker smette di trattare come fatale l'errore di un singolo job e riferisce quali job sono falliti. La ricerca li ritenta una volta e restituisce un rapporto di copertura. Una funzione pura decide, dal rapporto, se la rilevazione vendite ha basi sufficienti per girare. Le scritture a database isolano il riuso degli id prima che violi la chiave primaria, e proteggono il commit di ogni lotto. Infine una marca finita male torna in fondo alla coda, una volta.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0.35, httpx, APScheduler 3.10.4, pytest. Frontend React + Vite + Vitest.

## Global Constraints

- `BlockedError` resta fatale ovunque e conserva il comportamento attuale: svuota la coda e rilancia. Non deve mai finire fra i fallimenti isolati.
- Gli invarianti documentati in testa a `scraping/concurrency.py` restano validi: coda dei risultati illimitata, marcatore di fine sempre dietro a ogni risultato, nessun ordinamento garantito fra i risultati.
- Una pagina persa si stima **20 annunci** (`PAGE_SIZE = 20`).
- La soglia è **5%** degli annunci visti (`MAX_MISSING_FRACTION = 0.05`).
- Lo stato `partial` significa esattamente una cosa: **questo giro non ha valutato le vendite**. Una run con un buco sotto soglia che ha comunque fatto il controllo chiude `success`.
- Il riaccodamento avviene **una volta sola**, mai per `blocked`, mai per `success`.
- I timestamp restano `datetime.utcnow()` naive, come tutto il progetto. Non introdurre datetime aware.
- I messaggi degli eventi persistiti sono in italiano, **tranne** `"Detail backlog page: enriched N"` che gli script di monitoraggio parsano via regex e non va tradotta.
- I 219 test esistenti devono continuare a passare.

**Comando di test backend** (SQLite in memoria, sicuro durante lo scraping):

```bash
sudo -n docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/<file> -v
```

**Comando di test frontend:**

```bash
cd dashboard && npx vitest run && npx tsc -b
```

## File Structure

| file | responsabilità | azione |
|---|---|---|
| `scraper/src/autosmart24/scraping/concurrency.py` | pool di worker; isolamento del job | modifica |
| `scraper/src/autosmart24/scraping/crawler.py` | scansione a due fasi; recupero e rapporto | modifica |
| `scraper/src/autosmart24/scraping/coverage.py` | **nuovo** — decisione pura sulla copertura, nessun I/O | crea |
| `scraper/src/autosmart24/run_manager.py` | orchestrazione dello sweep; scritture | modifica |
| `scraper/src/autosmart24/api/app.py` | riaccodamento | modifica |
| `dashboard/src/...` | resa dello stato `partial` | modifica |
| `run-completo.sh`, `run-recupero2.sh` | attesa degli stati terminali | modifica |

`coverage.py` è un modulo nuovo e non un metodo su una classe esistente per la stessa ragione di `sold_confirmation.py`: è una decisione con conseguenze pesanti, e isolarla senza I/O la rende verificabile con una tabella di casi invece che con un intero sweep simulato.

---

### Task 1: Isolamento del singolo job nel pool

**Files:**
- Modify: `scraper/src/autosmart24/scraping/concurrency.py`
- Test: `scraper/tests/test_concurrency.py`

**Interfaces:**
- Consumes: niente da task precedenti.
- Produces: `JobFailure` (dataclass con campi `job: object` e `error: BaseException`) e il parametro keyword `failures: list[JobFailure] | None = None` di `run_worker_pool`. I task 2 e 4 li usano.

- [ ] **Step 1: Scrivere il test che riproduce il difetto**

In `scraper/tests/test_concurrency.py`, in coda al file:

```python
def test_run_worker_pool_isolates_a_failing_job_and_still_yields_the_others():
    """One unreachable page must cost that page, not the whole brand.

    Written against the pre-fix code this FAILS: the pool drained the queue on
    the first exception and re-raised, so the surviving jobs never arrived.
    """
    def worker_fn(job, client):
        if job == 3:
            raise TimeoutError("timed out")
        return [job * 2]

    failures: list = []
    results = sorted(
        run_worker_pool(
            list(range(6)), worker_fn, _client_factory,
            concurrency=2, session_refresh_requests=100, failures=failures,
        )
    )

    assert results == [0, 2, 4, 8, 10]
    assert [f.job for f in failures] == [3]
    assert isinstance(failures[0].error, TimeoutError)


def test_run_worker_pool_still_aborts_everything_on_a_block():
    """A block is the one case where continuing makes things worse."""
    started = threading.Event()

    def worker_fn(job, client):
        started.set()
        raise BlockedError(403, f"https://example.test/{job}")

    failures: list = []
    with pytest.raises(BlockedError):
        list(
            run_worker_pool(
                list(range(20)), worker_fn, _client_factory,
                concurrency=1, session_refresh_requests=100, failures=failures,
            )
        )
    assert started.is_set()
    assert failures == []


def test_run_worker_pool_without_a_failures_list_still_isolates_the_job():
    """Callers that infer failure from missing results need no accumulator."""
    def worker_fn(job, client):
        if job == 1:
            raise ValueError("boom")
        return [job]

    assert sorted(
        run_worker_pool(
            [0, 1, 2], worker_fn, _client_factory,
            concurrency=1, session_refresh_requests=100,
        )
    ) == [0, 2]


def test_run_worker_pool_client_factory_failure_is_still_fatal():
    """A factory that cannot build a client is systemic, not a bad page."""
    def factory():
        raise RuntimeError("no client")

    def worker_fn(job, client):
        raise AssertionError("must not be called")

    with pytest.raises(RuntimeError):
        list(run_worker_pool([1, 2], worker_fn, factory, concurrency=1, session_refresh_requests=100))
```

- [ ] **Step 2: Eseguire i test e verificare che falliscano**

```bash
sudo -n docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/test_concurrency.py -v
```

Atteso: `test_run_worker_pool_isolates_a_failing_job_and_still_yields_the_others` FALLISCE con `TimeoutError` propagato. Gli altri tre falliscono su `TypeError: unexpected keyword argument 'failures'` o su `ValueError` propagato.

- [ ] **Step 3: Implementare**

In `scraper/src/autosmart24/scraping/concurrency.py`, aggiungere l'import `from dataclasses import dataclass` in testa e la dataclass dopo le TypeVar:

```python
@dataclass
class JobFailure:
    """One job that could not be completed. The rest of the queue is unaffected."""

    job: object
    error: BaseException
```

Cambiare la firma:

```python
def run_worker_pool(
    jobs: list[JobT],
    worker_fn: Callable[[JobT, RateLimitedClient], list[ResultT]],
    client_factory: Callable[[], RateLimitedClient],
    concurrency: int,
    session_refresh_requests: int,
    failures: list[JobFailure] | None = None,
) -> Iterator[ResultT]:
```

Subito dopo `if not jobs: return`, aggiungere:

```python
    # Callers that can infer failure from a missing result (the detail backlog
    # parks unreported rows; the confirmation pass simply declares nothing)
    # pass no list and read nothing back.
    failure_sink = failures if failures is not None else []
```

Sostituire il corpo del `while` nel worker:

```python
            while not stop.is_set():
                try:
                    job = job_queue.get_nowait()
                except queue.Empty:
                    return
                if processed >= session_refresh_requests:
                    client.close()
                    client = client_factory()
                    processed = 0
                try:
                    job_results = worker_fn(job, client)
                except BlockedError:
                    # The site is refusing us. Pressing on lengthens the block,
                    # so this stays fatal and reaches the outer handler below.
                    raise
                except Exception as exc:
                    # One unreachable page costs that page. Counted as processed
                    # so the session-refresh cadence still advances: a client
                    # that just failed is a client worth rotating.
                    with error_lock:
                        failure_sink.append(JobFailure(job, exc))
                    processed += 1
                    continue
                processed += 1
                for item in job_results:
                    results.put(item)
```

Il blocco `except BaseException` esterno e il suo `_drain_queue()` restano **invariati**: ora coprono `BlockedError`, il fallimento di `client_factory()`, e le eccezioni che non derivano da `Exception` come `KeyboardInterrupt`.

- [ ] **Step 4: Aggiornare la nota degli invarianti**

Nella docstring del modulo, dopo il punto sui risultati fuori ordine, inserire:

```
* A job whose ``worker_fn`` raises anything other than ``BlockedError`` is
  isolated: the failure is recorded in the caller's ``failures`` list and the
  remaining jobs still run. ``BlockedError``, a failure of
  ``client_factory``, and non-``Exception`` throwables remain fatal and still
  drain the queue. Callers that pass no ``failures`` list still get the
  isolation — they simply cannot tell which job was lost.
```

- [ ] **Step 5: Eseguire tutti i test del pool**

```bash
sudo -n docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/test_concurrency.py -v
```

Atteso: tutti PASS.

- [ ] **Step 6: Commit**

```bash
git add scraper/src/autosmart24/scraping/concurrency.py scraper/tests/test_concurrency.py
git commit -m "fix: isolate a failing job instead of discarding the whole queue"
```

---

### Task 2: Recupero e rapporto di copertura nella ricerca

**Files:**
- Modify: `scraper/src/autosmart24/scraping/crawler.py`
- Test: `scraper/tests/test_crawler.py`

**Interfaces:**
- Consumes: `JobFailure` e il parametro `failures=` di `run_worker_pool` dal Task 1.
- Produces: `CrawlReport` (dataclass con `lost_models: list`, `lost_pages: list`, proprietà `complete: bool`) e il parametro keyword `report: CrawlReport | None = None` di `crawl_brand`. Il Task 4 li usa.

- [ ] **Step 1: Scrivere i test**

In `scraper/tests/test_crawler.py`, in coda al file:

```python
from autosmart24.scraping.crawler import CrawlReport, crawl_brand


def test_crawl_report_is_complete_when_nothing_was_lost():
    assert CrawlReport().complete is True
    assert CrawlReport(lost_pages=[("a",)]).complete is False
    assert CrawlReport(lost_models=[("m",)]).complete is False


def test_crawl_brand_recovers_a_discovery_that_failed_the_first_time(monkeypatch):
    """A model lost in discovery costs the whole model, so it is retried first
    and its pages must then be fetched like any other model's."""
    attempts: dict[str, int] = {}

    def fake_discover(model, client, brand_slug, make_id, year_from):
        attempts[model.model_id] = attempts.get(model.model_id, 0) + 1
        if model.model_id == 2 and attempts[2] == 1:
            raise TimeoutError("timed out")
        unit = QueryUnit(model.model_id, None, None, 2)
        return [(unit, [{"id": f"m{model.model_id}-p1"}])]

    def fake_page(client, url):
        return {"listings": []}

    monkeypatch.setattr("autosmart24.scraping.crawler._discover_model_units", fake_discover)
    monkeypatch.setattr("autosmart24.scraping.crawler.discover_models",
                        lambda c, s, m: [ModelInfo(1, "one"), ModelInfo(2, "two")])
    monkeypatch.setattr("autosmart24.scraping.crawler.fetch_page_data",
                        lambda client, url: {"listings": [{"id": url}]})
    monkeypatch.setattr("autosmart24.scraping.crawler.map_snippet_listing", lambda raw: raw)

    report = CrawlReport()
    out = list(crawl_brand(_client_factory, "brand", 7, concurrency=1,
                           session_refresh_requests=100, report=report))

    assert attempts[2] == 2, "the failed discovery must be retried exactly once"
    assert report.complete is True
    ids = {item["id"] for item in out if "id" in item}
    assert "m2-p1" in ids, "the recovered model's first page must be yielded"
    assert any("page=2" in str(item.get("id", "")) for item in out), \
        "the recovered model's remaining pages must be fetched too"


def test_crawl_brand_reports_a_discovery_it_could_not_recover(monkeypatch):
    def always_fails(model, client, brand_slug, make_id, year_from):
        raise TimeoutError("timed out")

    monkeypatch.setattr("autosmart24.scraping.crawler._discover_model_units", always_fails)
    monkeypatch.setattr("autosmart24.scraping.crawler.discover_models",
                        lambda c, s, m: [ModelInfo(1, "one")])

    report = CrawlReport()
    out = list(crawl_brand(_client_factory, "brand", 7, concurrency=1,
                           session_refresh_requests=100, report=report))

    assert out == []
    assert len(report.lost_models) == 1
    assert report.lost_pages == []
    assert report.complete is False


def test_crawl_brand_reports_a_page_it_could_not_recover(monkeypatch):
    def fake_discover(model, client, brand_slug, make_id, year_from):
        return [(QueryUnit(model.model_id, None, None, 2), [])]

    def failing_page(client, url):
        raise TimeoutError("timed out")

    monkeypatch.setattr("autosmart24.scraping.crawler._discover_model_units", fake_discover)
    monkeypatch.setattr("autosmart24.scraping.crawler.discover_models",
                        lambda c, s, m: [ModelInfo(1, "one")])
    monkeypatch.setattr("autosmart24.scraping.crawler.fetch_page_data", failing_page)

    report = CrawlReport()
    list(crawl_brand(_client_factory, "brand", 7, concurrency=1,
                     session_refresh_requests=100, report=report))

    assert report.lost_models == []
    assert len(report.lost_pages) == 1
    assert report.complete is False


def test_crawl_brand_works_without_a_report():
    """The report is optional so existing callers and tests keep working."""
    assert list(crawl_brand(_client_factory, "brand", 7, concurrency=1,
                            session_refresh_requests=100)) is not None
```

In testa al file di test, se non già presenti, aggiungere gli import `from autosmart24.scraping.crawler import QueryUnit` e `from autosmart24.scraping.brand_catalog import ModelInfo`, e definire:

```python
def _client_factory() -> RateLimitedClient:
    return RateLimitedClient(min_delay_seconds=0, max_delay_seconds=0, sleep_fn=lambda _: None)
```

*Nota per chi implementa:* verificare da quale modulo provengono davvero `QueryUnit`, `ModelInfo` e `map_snippet_listing` prima di scrivere gli import e i `monkeypatch.setattr` — i percorsi vanno riferiti al modulo `crawler`, non a quello di origine, altrimenti la sostituzione non ha effetto. L'ultimo test va adattato al modo in cui il file di test già costruisce un crawl finto.

- [ ] **Step 2: Eseguire i test e verificare che falliscano**

```bash
sudo -n docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/test_crawler.py -v
```

Atteso: FALLISCONO su `ImportError: cannot import name 'CrawlReport'`.

- [ ] **Step 3: Implementare**

In `scraper/src/autosmart24/scraping/crawler.py`, aggiungere gli import `from dataclasses import dataclass, field` e `from autosmart24.scraping.concurrency import JobFailure, run_worker_pool`, poi la dataclass:

```python
@dataclass
class CrawlReport:
    """What the crawl could not fetch, kept separate by severity.

    A lost page is worth roughly PAGE_SIZE listings and can be estimated. A
    lost model was dropped while learning how many pages it has, so its size
    is unknown and no estimate is possible -- which is why the two are not
    merged into a single counter.
    """

    lost_models: list = field(default_factory=list)
    lost_pages: list = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.lost_models and not self.lost_pages
```

Sostituire il corpo di `crawl_brand` dopo `discover_models`:

```python
    def _discovery_worker(model: ModelInfo, client: RateLimitedClient) -> list[tuple[QueryUnit, list[dict]]]:
        return _discover_model_units(model, client, brand_slug, make_id, year_from)

    units: list[QueryUnit] = []
    discovery_failures: list[JobFailure] = []
    for unit, listings in run_worker_pool(
        models, _discovery_worker, client_factory, concurrency, session_refresh_requests,
        failures=discovery_failures,
    ):
        units.append(unit)
        yield from listings

    # Retry the lost models before the page list is built: a model recovered
    # here still contributes its pages below, whereas one recovered afterwards
    # would silently contribute only its first page. Minutes have passed since
    # the first attempt, which is what makes a retry worth making at all.
    lost_models: list[JobFailure] = []
    if discovery_failures:
        retry_models = [f.job for f in discovery_failures]
        for unit, listings in run_worker_pool(
            retry_models, _discovery_worker, client_factory, concurrency, session_refresh_requests,
            failures=lost_models,
        ):
            units.append(unit)
            yield from listings

    def _page_worker(job: tuple[int, int | None, int | None, int], client: RateLimitedClient) -> list[dict]:
        model_id, yf, yt, page = job
        url = build_search_url(brand_slug, page=page, make_id=make_id, model_id=model_id, year_from=yf, year_to=yt)
        return list(_iter_listings_from_page(fetch_page_data(client, url)))

    page_jobs: list[tuple[int, int | None, int | None, int]] = []
    for unit in units:
        for page in range(2, unit.number_of_pages + 1):
            page_jobs.append((unit.model_id, unit.year_from, unit.year_to, page))

    page_failures: list[JobFailure] = []
    yield from run_worker_pool(
        page_jobs, _page_worker, client_factory, concurrency, session_refresh_requests,
        failures=page_failures,
    )

    lost_pages: list[JobFailure] = []
    if page_failures:
        yield from run_worker_pool(
            [f.job for f in page_failures], _page_worker, client_factory,
            concurrency, session_refresh_requests, failures=lost_pages,
        )

    if report is not None:
        report.lost_models.extend(f.job for f in lost_models)
        report.lost_pages.extend(f.job for f in lost_pages)
```

E la firma diventa:

```python
def crawl_brand(
    client_factory: Callable[[], RateLimitedClient],
    brand_slug: str,
    make_id: int,
    year_from: int | None = None,
    concurrency: int = 1,
    session_refresh_requests: int = 30,
    report: CrawlReport | None = None,
) -> Iterator[dict]:
```

- [ ] **Step 4: Eseguire i test**

```bash
sudo -n docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/test_crawler.py -v
```

Atteso: tutti PASS.

- [ ] **Step 5: Commit**

```bash
git add scraper/src/autosmart24/scraping/crawler.py scraper/tests/test_crawler.py
git commit -m "feat: retry lost search jobs once and report what stayed lost"
```

---

### Task 3: La decisione sulla copertura

**Files:**
- Create: `scraper/src/autosmart24/scraping/coverage.py`
- Test: `scraper/tests/test_coverage.py` (nuovo)

**Interfaces:**
- Consumes: niente. Modulo puro, nessun I/O, nessun import da `run_manager` o dal database.
- Produces: `PAGE_SIZE = 20`, `MAX_MISSING_FRACTION = 0.05`, `CoverageVerdict` (dataclass congelata con `can_detect_sales: bool`, `estimated_missing: int`, `reason: str`) e `assess_coverage(lost_models: int, lost_pages: int, listings_seen: int) -> CoverageVerdict`. Il Task 4 la usa.

- [ ] **Step 1: Scrivere i test**

Creare `scraper/tests/test_coverage.py`:

```python
from autosmart24.scraping.coverage import (
    MAX_MISSING_FRACTION,
    PAGE_SIZE,
    assess_coverage,
)


def test_a_complete_crawl_can_detect_sales():
    v = assess_coverage(lost_models=0, lost_pages=0, listings_seen=10_000)
    assert v.can_detect_sales is True
    assert v.estimated_missing == 0


def test_a_small_page_gap_still_allows_detection():
    # 10 pages ~ 200 listings out of 10,000 = 2%, under the 5% threshold.
    v = assess_coverage(lost_models=0, lost_pages=10, listings_seen=10_000)
    assert v.can_detect_sales is True
    assert v.estimated_missing == 200


def test_a_large_page_gap_suppresses_detection():
    # 30 pages ~ 600 listings out of 10,000 = 6%, over the threshold.
    v = assess_coverage(lost_models=0, lost_pages=30, listings_seen=10_000)
    assert v.can_detect_sales is False
    assert v.estimated_missing == 600


def test_the_threshold_boundary_is_inclusive():
    # Exactly 5% must still be allowed: the spec says "buco <= 5%".
    seen = 10_000
    pages = int(seen * MAX_MISSING_FRACTION / PAGE_SIZE)
    assert assess_coverage(lost_models=0, lost_pages=pages, listings_seen=seen).can_detect_sales is True
    assert assess_coverage(lost_models=0, lost_pages=pages + 1, listings_seen=seen).can_detect_sales is False


def test_a_lost_model_suppresses_detection_whatever_the_page_count():
    """A model was dropped while learning its page count, so the size of the
    hole is unknown. There is no fraction to compare against a threshold."""
    for pages in (0, 1, 1000):
        v = assess_coverage(lost_models=1, lost_pages=pages, listings_seen=1_000_000)
        assert v.can_detect_sales is False
        assert "modell" in v.reason.lower()


def test_nothing_seen_and_something_lost_suppresses_detection():
    """Guards the division and states the obvious case: if the crawl saw
    nothing but lost pages, coverage is zero, not complete."""
    v = assess_coverage(lost_models=0, lost_pages=1, listings_seen=0)
    assert v.can_detect_sales is False


def test_nothing_seen_and_nothing_lost_is_a_complete_if_empty_crawl():
    v = assess_coverage(lost_models=0, lost_pages=0, listings_seen=0)
    assert v.can_detect_sales is True


def test_the_reason_is_always_populated():
    for args in [(0, 0, 100), (0, 1, 100), (1, 0, 100), (0, 1, 0)]:
        assert assess_coverage(*args).reason
```

- [ ] **Step 2: Eseguire i test e verificare che falliscano**

```bash
sudo -n docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/test_coverage.py -v
```

Atteso: `ModuleNotFoundError: No module named 'autosmart24.scraping.coverage'`.

- [ ] **Step 3: Implementare**

Creare `scraper/src/autosmart24/scraping/coverage.py`:

```python
"""Whether a crawl covered enough ground for sold detection to run.

Pure decision, no I/O: it is the gate in front of the only code path in the
project that can write ``status = "sold"``, and a gate is worth being able to
check with a table of cases rather than a simulated sweep.

What this does NOT protect against is a false sale. That is guaranteed
upstream: a sale is declared only where ``fetch_detail`` returned a result,
which requires either a page that loaded or an explicit 404/410 -- a timeout
raises and produces nothing. A listing missed by an incomplete crawl is
therefore opened, answers Active, and stays active. This threshold governs
wasted work, not correctness.
"""

from __future__ import annotations

from dataclasses import dataclass

# AutoScout24 serves 20 results per page (config.MAX_RESULTS_PER_QUERY = 4000
# over its 200-page pagination cap). A lost page was never fetched, so its
# real size is unknown and this is an estimate -- acceptable precisely because
# the threshold below decides cost rather than correctness.
PAGE_SIZE = 20

MAX_MISSING_FRACTION = 0.05


@dataclass(frozen=True)
class CoverageVerdict:
    can_detect_sales: bool
    estimated_missing: int
    reason: str


def assess_coverage(lost_models: int, lost_pages: int, listings_seen: int) -> CoverageVerdict:
    estimated_missing = max(0, lost_pages) * PAGE_SIZE

    if lost_models > 0:
        # No estimate is possible: the job died while learning the model's page
        # count, so the hole is somewhere between fifty and five thousand
        # listings and nothing on hand narrows it down.
        return CoverageVerdict(
            can_detect_sales=False,
            estimated_missing=estimated_missing,
            reason=f"{lost_models} modelli non recuperati: dimensione del buco non stimabile",
        )

    if estimated_missing == 0:
        return CoverageVerdict(True, 0, "scansione completa")

    if listings_seen <= 0:
        return CoverageVerdict(
            can_detect_sales=False,
            estimated_missing=estimated_missing,
            reason="nessun annuncio visto ma pagine perse: copertura nulla",
        )

    fraction = estimated_missing / listings_seen
    if fraction > MAX_MISSING_FRACTION:
        return CoverageVerdict(
            can_detect_sales=False,
            estimated_missing=estimated_missing,
            reason=(
                f"buco stimato {estimated_missing} annunci su {listings_seen} visti "
                f"({fraction:.1%}), oltre la soglia del {MAX_MISSING_FRACTION:.0%}"
            ),
        )

    return CoverageVerdict(
        can_detect_sales=True,
        estimated_missing=estimated_missing,
        reason=(
            f"buco stimato {estimated_missing} annunci su {listings_seen} visti "
            f"({fraction:.1%}), entro la soglia"
        ),
    )
```

- [ ] **Step 4: Eseguire i test**

```bash
sudo -n docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/test_coverage.py -v
```

Atteso: tutti PASS.

- [ ] **Step 5: Commit**

```bash
git add scraper/src/autosmart24/scraping/coverage.py scraper/tests/test_coverage.py
git commit -m "feat: decide from crawl coverage whether sold detection may run"
```

---

### Task 4: Lo sweep rispetta la copertura e introduce lo stato `partial`

**Files:**
- Modify: `scraper/src/autosmart24/run_manager.py` (funzione `run_brand_sweep`)
- Test: `scraper/tests/test_sold_detection.py`

**Interfaces:**
- Consumes: `CrawlReport` dal Task 2, `assess_coverage` e `CoverageVerdict` dal Task 3.
- Produces: lo stato di run `"partial"`, usato dal Task 7 (riaccodamento) e dal Task 8 (dashboard e script).

- [ ] **Step 1: Scrivere i test**

In `scraper/tests/test_sold_detection.py`, in coda al file. Usare la fixture `db_session` e le utilità di costruzione dello sweep già presenti nel file — **leggere come i test esistenti costruiscono `crawl_fn` e `fetch_detail_fn` e seguire lo stesso schema**, perché `crawl_fn` ora riceve anche `report=`.

```python
def test_a_lost_model_suppresses_sold_detection_and_marks_the_run_partial(db_session):
    """The whole point: a listing absent from an incomplete crawl must not
    even be considered for sale."""
    _seed_active_listing(db_session, "gone-1", brand="Fiat")

    def crawl_fn(client_factory, slug, make_id, year_from=None, concurrency=1,
                 session_refresh_requests=30, report=None):
        if report is not None:
            report.lost_models.append(("modello-perso",))
        return iter([_snippet("still-here-1")])

    def fetch_detail_fn(client, url):
        raise AssertionError("sold detection must not run when a model was lost")

    run = run_brand_sweep(
        db_session, _client_factory, _brand(), crawl_fn=crawl_fn,
        fetch_detail_fn=fetch_detail_fn, concurrency=1,
    )

    assert run.status == "partial"
    assert run.sold_detected == 0
    assert db_session.get(Listing, "gone-1").status == "active"


def test_a_small_page_gap_still_runs_sold_detection_and_the_run_is_success(db_session):
    """A gap under the threshold costs some wasted checks, not a whole cycle
    of sale data."""
    _seed_active_listing(db_session, "gone-1", brand="Fiat")

    def crawl_fn(client_factory, slug, make_id, year_from=None, concurrency=1,
                 session_refresh_requests=30, report=None):
        if report is not None:
            report.lost_pages.append((1, None, None, 5))
        return iter([_snippet(f"seen-{i}") for i in range(500)])

    calls: list[str] = []

    def fetch_detail_fn(client, url):
        calls.append(url)
        return DetailResult(sold=True)

    run = run_brand_sweep(
        db_session, _client_factory, _brand(), crawl_fn=crawl_fn,
        fetch_detail_fn=fetch_detail_fn, concurrency=1,
    )

    assert run.status == "success"
    assert run.sold_detected == 1
    assert db_session.get(Listing, "gone-1").status == "sold"
    assert len(calls) == 2, "one check plus one confirmation"


def test_a_large_page_gap_suppresses_sold_detection(db_session):
    _seed_active_listing(db_session, "gone-1", brand="Fiat")

    def crawl_fn(client_factory, slug, make_id, year_from=None, concurrency=1,
                 session_refresh_requests=30, report=None):
        if report is not None:
            report.lost_pages.extend((1, None, None, p) for p in range(2, 40))
        return iter([_snippet(f"seen-{i}") for i in range(100)])

    def fetch_detail_fn(client, url):
        raise AssertionError("sold detection must not run over the threshold")

    run = run_brand_sweep(
        db_session, _client_factory, _brand(), crawl_fn=crawl_fn,
        fetch_detail_fn=fetch_detail_fn, concurrency=1,
    )

    assert run.status == "partial"
    assert db_session.get(Listing, "gone-1").status == "active"


def test_a_partial_run_still_keeps_the_listings_it_collected(db_session):
    """Losing coverage must not lose data: the crawl's own work is committed."""
    def crawl_fn(client_factory, slug, make_id, year_from=None, concurrency=1,
                 session_refresh_requests=30, report=None):
        if report is not None:
            report.lost_models.append(("modello-perso",))
        return iter([_snippet("new-1"), _snippet("new-2")])

    run = run_brand_sweep(
        db_session, _client_factory, _brand(), crawl_fn=crawl_fn,
        fetch_detail_fn=lambda c, u: DetailResult(sold=False, data={}), concurrency=1,
    )

    assert run.status == "partial"
    assert db_session.get(Listing, "new-1") is not None
    assert db_session.get(Listing, "new-2") is not None


def test_a_partial_run_records_why(db_session):
    def crawl_fn(client_factory, slug, make_id, year_from=None, concurrency=1,
                 session_refresh_requests=30, report=None):
        if report is not None:
            report.lost_models.append(("modello-perso",))
        return iter([])

    run = run_brand_sweep(
        db_session, _client_factory, _brand(), crawl_fn=crawl_fn,
        fetch_detail_fn=lambda c, u: DetailResult(sold=False, data={}), concurrency=1,
    )

    messages = [e.message for e in db_session.query(ScrapeEvent).all()]
    assert any("vendite" in m.lower() and "modell" in m.lower() for m in messages)


def test_a_gap_under_threshold_logs_one_summary_not_one_event_per_listing(db_session):
    """A near-threshold gap sends hundreds of listings down the 'missing but
    alive' path. One event each would read as a serious fault on the only
    monitoring channel this project has."""
    for i in range(40):
        _seed_active_listing(db_session, f"unseen-{i}", brand="Fiat")

    def crawl_fn(client_factory, slug, make_id, year_from=None, concurrency=1,
                 session_refresh_requests=30, report=None):
        if report is not None:
            report.lost_pages.append((1, None, None, 5))
        return iter([_snippet(f"seen-{i}") for i in range(1000)])

    def fetch_detail_fn(client, url):
        return DetailResult(sold=False, data={"brand": "Fiat"})

    run_brand_sweep(
        db_session, _client_factory, _brand(), crawl_fn=crawl_fn,
        fetch_detail_fn=fetch_detail_fn, concurrency=1,
    )

    per_listing = [
        e for e in db_session.query(ScrapeEvent).all()
        if "non trovato nella scansione" in e.message.lower()
        or "not found in sweep" in e.message.lower()
    ]
    assert len(per_listing) <= 1, f"expected a single summary, got {len(per_listing)}"


def test_a_candidate_whose_confirmation_times_out_stays_active(db_session):
    """The heart of the principle: absence of proof is not proof of absence.

    This is asserted explicitly rather than assumed from the fact that the
    29/07 code happened to behave this way -- an assumption is what a later
    refactor breaks silently, and the failure mode is inventing sales.
    """
    _seed_active_listing(db_session, "gone-1", brand="Fiat")
    calls = {"n": 0}

    def crawl_fn(client_factory, slug, make_id, year_from=None, concurrency=1,
                 session_refresh_requests=30, report=None):
        return iter([_snippet(f"seen-{i}") for i in range(10)])

    def fetch_detail_fn(client, url):
        calls["n"] += 1
        if calls["n"] == 1:
            return DetailResult(sold=True)   # first check: looks removed
        raise TimeoutError("timed out")      # confirmation: we could not ask

    run = run_brand_sweep(
        db_session, _client_factory, _brand(), crawl_fn=crawl_fn,
        fetch_detail_fn=fetch_detail_fn, concurrency=1,
    )

    assert db_session.get(Listing, "gone-1").status == "active"
    assert db_session.get(Listing, "gone-1").sold_at is None
    assert run.sold_detected == 0


def test_a_block_still_stops_the_sweep_in_each_phase(db_session):
    """BlockedError stays fatal everywhere. Job isolation must not have
    quietly turned a block into a skipped page in any of the four phases."""
    for phase_at_call in (1, 2):
        db_session.query(Listing).delete()
        db_session.commit()
        _seed_active_listing(db_session, f"gone-{phase_at_call}", brand="Fiat")
        calls = {"n": 0}

        def crawl_fn(client_factory, slug, make_id, year_from=None, concurrency=1,
                     session_refresh_requests=30, report=None):
            return iter([_snippet("seen-1")])

        def fetch_detail_fn(client, url, _at=phase_at_call):
            calls["n"] += 1
            if calls["n"] >= _at:
                raise BlockedError(403, url)
            return DetailResult(sold=True)

        run = run_brand_sweep(
            db_session, _client_factory, _brand(), crawl_fn=crawl_fn,
            fetch_detail_fn=fetch_detail_fn, concurrency=1,
        )

        assert run.status == "blocked", f"a block at call {phase_at_call} must stop the sweep"
        assert db_session.get(Listing, f"gone-{phase_at_call}").status == "active"
```

*Nota per chi implementa:* `_seed_active_listing`, `_snippet`, `_brand` e `_client_factory` sono nomi segnaposto per le utilità che il file di test già usa. Riusare quelle esistenti invece di crearne di nuove; se non esistono, definirle una volta in cima al blocco nuovo.

- [ ] **Step 2: Eseguire i test e verificare che falliscano**

```bash
sudo -n docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/test_sold_detection.py -v
```

Atteso: i test nuovi FALLISCONO — la run chiude `success` e la rilevazione vendite gira comunque, quindi gli `AssertionError` dentro `fetch_detail_fn` scattano.

- [ ] **Step 3: Implementare**

In `run_manager.py`, aggiungere in testa:

```python
from autosmart24.scraping.coverage import assess_coverage
from autosmart24.scraping.crawler import CrawlReport
```

Nel corpo di `run_brand_sweep`, creare il rapporto prima del ciclo dei lotti e passarlo a `crawl_fn`:

```python
        crawl_report = CrawlReport()
        try:
            for batch in _iter_batches(
                crawl_fn(
                    client_factory, brand.slug, brand.make_id,
                    year_from=year_from, concurrency=concurrency,
                    session_refresh_requests=session_refresh_requests,
                    report=crawl_report,
                ),
                batch_size,
            ):
```

Subito dopo il blocco `except BlockedError` che chiude la fase di ricerca (attualmente riga ~375), prima del calcolo di `missing_ids`, inserire:

```python
        coverage = assess_coverage(
            lost_models=len(crawl_report.lost_models),
            lost_pages=len(crawl_report.lost_pages),
            listings_seen=listings_seen,
        )
        if not coverage.can_detect_sales:
            # The crawl did not cover enough ground for "active in the database
            # but not seen on the site" to mean anything. The listings it did
            # collect are already committed batch by batch and stay; only the
            # judgement is deferred to the next cycle, which is the same
            # delay-over-falsehood trade the 28/07 spec already accepted.
            run.phase = "detail"
            run.search_finished_at = _now()
            _log_event(
                session, run, "warning",
                f"Rilevazione vendite saltata, scansione incompleta: {coverage.reason}",
            )
            session.commit()
            process_detail_backlog(
                session, client_factory, brand, run,
                concurrency=concurrency, session_refresh_requests=session_refresh_requests,
                fetch_detail_fn=fetch_detail_fn, year_from=year_from,
            )
            run.listings_seen = listings_seen
            run.new_listings = len(new_ids)
            run.price_changes = price_changes
            run.sold_detected = 0
            if run.status != "blocked":
                run.status = "partial"
            run.phase = None
            run.finished_at = _now()
            session.commit()
            return run
```

Nel passaggio sui mancanti, sostituire l'evento per annuncio con un conteggio, ed emettere un riassunto:

```python
        missing_but_alive = 0
        try:
            for listing_id, result in run_worker_pool(
                missing_jobs, _missing_worker, client_factory, concurrency, session_refresh_requests
            ):
                row = active_rows_by_id[listing_id]
                row.last_checked_at = now
                if looks_removed(result, row.brand):
                    sold_candidates.append(listing_id)
                else:
                    missing_but_alive += 1
                    run.errors_count += 1
                    # With a known coverage gap this path is the expected
                    # outcome for every listing the crawl missed, not an
                    # anomaly: one event each would read as a serious fault on
                    # the dashboard, this project's only monitoring channel.
                    if coverage.estimated_missing == 0:
                        _log_event(
                            session, run, "warning",
                            f"Listing {listing_id} not found in sweep but still active on detail page",
                            url=row.url,
                        )
        except BlockedError as exc:
            ...  # invariato
        if coverage.estimated_missing > 0 and missing_but_alive:
            _log_event(
                session, run, "warning",
                f"{missing_but_alive} annunci non trovati nella scansione ma ancora attivi "
                f"(atteso: {coverage.reason})",
            )
```

- [ ] **Step 4: Eseguire i test**

```bash
sudo -n docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/test_sold_detection.py tests/test_run_manager.py tests/test_run_manager_progress.py -v
```

Atteso: tutti PASS. Se qualche test esistente passa un `crawl_fn` finto senza il parametro `report`, aggiornare quelle firme — è un adeguamento legittimo, non un aggiramento.

- [ ] **Step 5: Commit**

```bash
git add scraper/src/autosmart24/run_manager.py scraper/tests/test_sold_detection.py
git commit -m "feat: gate sold detection on crawl coverage, add partial run status"
```

---

### Task 5: Riconoscere il riuso degli id prima che rompa la scrittura

**Files:**
- Modify: `scraper/src/autosmart24/run_manager.py` (righe ~246-253 e il ciclo su `diff.new_ids`)
- Test: `scraper/tests/test_run_manager.py`

**Interfaces:**
- Consumes: niente dai task precedenti; indipendente da 1-4.
- Produces: niente per i task successivi.

- [ ] **Step 1: Scrivere i test**

In `scraper/tests/test_run_manager.py`, in coda al file:

```python
def test_run_brand_sweep_skips_a_listing_id_that_belongs_to_another_brand(db_session):
    """AutoScout24 reassigns the id of a withdrawn ad to an unrelated car, and
    the new car can belong to a different brand. The existing-id guard used to
    be scoped to the brand being swept, so the sweep took the INSERT path and
    died on listings_pkey -- taking 28,000 healthy listings with it. Audi hit
    this five times on the same id across two machines.
    """
    db_session.add(
        Listing(
            id="reused-1", brand="Mercedes-Benz", status="sold", url="https://x/old",
            price=10000, first_seen_at=dt.datetime(2026, 7, 1), last_seen_at=dt.datetime(2026, 7, 1),
            last_checked_at=dt.datetime(2026, 7, 1), detail_scraped=True,
        )
    )
    db_session.commit()

    def crawl_fn(client_factory, slug, make_id, year_from=None, concurrency=1,
                 session_refresh_requests=30, report=None):
        return iter([_snippet("reused-1", brand="Audi"), _snippet("fresh-1", brand="Audi")])

    run = run_brand_sweep(
        db_session, _client_factory, _brand(slug="audi", display_name="Audi"),
        crawl_fn=crawl_fn, fetch_detail_fn=lambda c, u: DetailResult(sold=False, data={}),
        concurrency=1,
    )

    # The sweep must reach the end, not just avoid crashing.
    assert run.status in ("success", "partial")
    assert run.finished_at is not None
    # The other listing in the same batch is unaffected.
    assert db_session.get(Listing, "fresh-1") is not None
    # The pre-existing row keeps its own brand: overwriting it would attribute
    # one car's price history to another.
    stale = db_session.get(Listing, "reused-1")
    assert stale.brand == "Mercedes-Benz"
    assert stale.status == "sold"


def test_run_brand_sweep_records_each_id_reuse_it_meets(db_session):
    """Frequency was estimated from a single observed case. Logging every
    occurrence is what turns it into a measurement."""
    db_session.add(
        Listing(
            id="reused-1", brand="Mercedes-Benz", status="sold", url="https://x/old",
            price=10000, first_seen_at=dt.datetime(2026, 7, 1), last_seen_at=dt.datetime(2026, 7, 1),
            last_checked_at=dt.datetime(2026, 7, 1), detail_scraped=True,
        )
    )
    db_session.commit()

    def crawl_fn(client_factory, slug, make_id, year_from=None, concurrency=1,
                 session_refresh_requests=30, report=None):
        return iter([_snippet("reused-1", brand="Audi")])

    run = run_brand_sweep(
        db_session, _client_factory, _brand(slug="audi", display_name="Audi"),
        crawl_fn=crawl_fn, fetch_detail_fn=lambda c, u: DetailResult(sold=False, data={}),
        concurrency=1,
    )

    messages = [e.message for e in db_session.query(ScrapeEvent).all()]
    assert any("reused-1" in m and "Mercedes-Benz" in m for m in messages)
    assert run.errors_count >= 1


def test_run_brand_sweep_still_relists_a_reappearing_listing_of_the_same_brand(db_session):
    """The global lookup must not break the existing relist path: an id that
    comes back under its OWN brand is a relist, not a reuse."""
    db_session.add(
        Listing(
            id="back-1", brand="Fiat", status="sold", url="https://x/back",
            price=9000, first_seen_at=dt.datetime(2026, 7, 1), last_seen_at=dt.datetime(2026, 7, 1),
            last_checked_at=dt.datetime(2026, 7, 1), detail_scraped=True,
            sold_at=dt.datetime(2026, 7, 5),
        )
    )
    db_session.commit()

    def crawl_fn(client_factory, slug, make_id, year_from=None, concurrency=1,
                 session_refresh_requests=30, report=None):
        return iter([_snippet("back-1", brand="Fiat")])

    run_brand_sweep(
        db_session, _client_factory, _brand(), crawl_fn=crawl_fn,
        fetch_detail_fn=lambda c, u: DetailResult(sold=False, data={}), concurrency=1,
    )

    row = db_session.get(Listing, "back-1")
    assert row.status == "active"
    assert row.sold_at is None
```

*Nota per chi implementa:* `_snippet` deve accettare un `brand`. Se l'utilità esistente non lo prevede, estenderla senza cambiare il comportamento di default.

- [ ] **Step 2: Eseguire i test e verificare che falliscano**

```bash
sudo -n docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/test_run_manager.py -v
```

Atteso: i primi due FALLISCONO con `IntegrityError: UNIQUE constraint failed: listings.id`. Il terzo passa già ed è lì per impedire una regressione.

- [ ] **Step 3: Implementare**

In `run_manager.py`, sostituire il caricamento di `existing_ids` (righe ~246-253):

```python
        # id -> brand, across ALL brands rather than the one being swept.
        # AutoScout24 reassigns the id of a withdrawn ad to an unrelated car,
        # and that car can belong to a different brand: scoped to one brand the
        # lookup missed the collision, the code took the INSERT path, and the
        # primary key violation killed the whole sweep. See
        # docs/superpowers/specs/2026-07-28-listing-id-reuse-known-issue.md
        existing_brand_by_id: dict[str, str] = dict(
            session.execute(select(Listing.id, Listing.brand)).all()
        )
```

Nel ciclo su `diff.new_ids`, sostituire `if listing_id in existing_ids:` con:

```python
                    existing_brand = existing_brand_by_id.get(listing_id)
                    if existing_brand is not None and existing_brand != brand.display_name:
                        # The id now belongs to a different car. Updating the row
                        # in place would write this car's fields, and its future
                        # price history, onto the other brand's record. Skip it:
                        # the new car is not captured, which is the limit this
                        # deliberately accepts -- see the known-issue document
                        # for what a semantically complete fix would require.
                        run.errors_count += 1
                        _log_event(
                            session, run, "warning",
                            f"Id {listing_id} già presente sotto la marca {existing_brand}: "
                            f"riuso id di AutoScout, annuncio saltato",
                            url=snippet["url"],
                        )
                        continue
                    if existing_brand is not None:
```

Il corpo del ramo di relist resta invariato.

- [ ] **Step 4: Eseguire i test**

```bash
sudo -n docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/test_run_manager.py tests/test_sold_detection.py -v
```

Atteso: tutti PASS.

- [ ] **Step 5: Commit**

```bash
git add scraper/src/autosmart24/run_manager.py scraper/tests/test_run_manager.py
git commit -m "fix: recognise a reassigned listing id instead of crashing the sweep"
```

---

### Task 6: Un lotto che non si scrive non fa cadere la marca

**Files:**
- Modify: `scraper/src/autosmart24/run_manager.py` (il commit del lotto, riga ~368)
- Test: `scraper/tests/test_run_manager.py`

**Interfaces:**
- Consumes: niente. Indipendente.
- Produces: niente.

- [ ] **Step 1: Scrivere il test**

```python
def test_run_brand_sweep_survives_a_batch_whose_commit_fails(db_session, monkeypatch):
    """Id reuse is the write failure we know about, not the only one possible.
    A malformed value or a future schema change must cost its batch, not the
    28,000 listings around it."""
    calls = {"n": 0}
    real_commit = db_session.commit

    def flaky_commit():
        calls["n"] += 1
        if calls["n"] == 2:
            raise IntegrityError("boom", None, Exception("synthetic"))
        return real_commit()

    def crawl_fn(client_factory, slug, make_id, year_from=None, concurrency=1,
                 session_refresh_requests=30, report=None):
        return iter([_snippet(f"item-{i}") for i in range(10)])

    monkeypatch.setattr(db_session, "commit", flaky_commit)
    run = run_brand_sweep(
        db_session, _client_factory, _brand(), crawl_fn=crawl_fn,
        fetch_detail_fn=lambda c, u: DetailResult(sold=False, data={}),
        concurrency=1, batch_size=5,
    )
    monkeypatch.undo()

    assert run.status in ("success", "partial"), "the sweep must reach the end"
    assert run.finished_at is not None
    messages = [e.message for e in db_session.query(ScrapeEvent).all()]
    assert any("lotto" in m.lower() for m in messages)


def test_a_dropped_batch_does_not_send_its_listings_down_the_missing_path(db_session, monkeypatch):
    """The listings of a dropped batch were genuinely on the site. Letting them
    fall into missing_ids would hand them to the one code path that can declare
    a sale -- reopening, from a new direction, the hole closed on 29/07."""
    _seed_active_listing(db_session, "item-0", brand="Fiat")
    calls = {"n": 0}
    real_commit = db_session.commit

    def flaky_commit():
        calls["n"] += 1
        if calls["n"] == 2:
            raise IntegrityError("boom", None, Exception("synthetic"))
        return real_commit()

    def crawl_fn(client_factory, slug, make_id, year_from=None, concurrency=1,
                 session_refresh_requests=30, report=None):
        return iter([_snippet(f"item-{i}") for i in range(10)])

    def fetch_detail_fn(client, url):
        raise AssertionError("a listing seen on the site must not be checked as missing")

    monkeypatch.setattr(db_session, "commit", flaky_commit)
    run_brand_sweep(
        db_session, _client_factory, _brand(), crawl_fn=crawl_fn,
        fetch_detail_fn=fetch_detail_fn, concurrency=1, batch_size=5,
    )
    monkeypatch.undo()

    assert db_session.get(Listing, "item-0").status == "active"
```

*Nota per chi implementa:* il test dipende da quale commit è il secondo, il che dipende da quanti ne fa lo sweep prima del primo lotto. Verificare contando, e se il numero è diverso adeguare `calls["n"] == 2` al primo commit di lotto reale — non cambiare il senso del test per farlo passare.

- [ ] **Step 2: Eseguire il test e verificare che fallisca**

```bash
sudo -n docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/test_run_manager.py::test_run_brand_sweep_survives_a_batch_whose_commit_fails -v
```

Atteso: FALLISCE — l'`IntegrityError` risale al gestore esterno e la run chiude `error`.

- [ ] **Step 3: Implementare**

Aggiungere l'import `from sqlalchemy.exc import SQLAlchemyError` e sostituire il commit del lotto:

```python
                run.listings_seen = listings_seen
                run.new_listings = len(new_ids)
                run.price_changes = price_changes
                try:
                    session.commit()
                except SQLAlchemyError as exc:
                    # A batch that cannot be written costs its own few hundred
                    # listings; before this it cost the entire brand. The rows
                    # are not lost for good -- they carry no state of their own
                    # yet, so the next sweep inserts them normally.
                    #
                    # seen_ids already holds this batch's ids and is deliberately
                    # NOT rolled back: those listings were genuinely seen on the
                    # site, so letting them fall into missing_ids would invite
                    # exactly the false-sale path this project spent 29/07
                    # closing.
                    session.rollback()
                    run.errors_count += 1
                    _log_event(
                        session, run, "error",
                        f"Lotto di {len(batch_snippets)} annunci non scritto e saltato: {type(exc).__name__}",
                    )
                    session.commit()
```

- [ ] **Step 4: Eseguire i test**

```bash
sudo -n docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/test_run_manager.py -v
```

Atteso: tutti PASS.

- [ ] **Step 5: Commit**

```bash
git add scraper/src/autosmart24/run_manager.py scraper/tests/test_run_manager.py
git commit -m "fix: skip a batch that cannot be committed instead of failing the sweep"
```

---

### Task 7: Riaccodamento di una marca finita male

**Files:**
- Modify: `scraper/src/autosmart24/api/app.py` (`_run_fn`, righe 73-118)
- Test: `scraper/tests/test_app_wiring.py`

**Interfaces:**
- Consumes: lo stato `"partial"` dal Task 4.
- Produces: niente.

- [ ] **Step 1: Scrivere i test**

Il file usa già la fixture `imported_app_module`, che importa `autosmart24.api.app` con `DATABASE_URL` puntato a SQLite in memoria. Aggiungere in coda:

```python
class _FakeRun:
    def __init__(self, status: str):
        self.status = status


@pytest.fixture()
def requeue_probe(imported_app_module, monkeypatch):
    """Runs _run_fn against a stubbed sweep and records any job it schedules."""
    module = imported_app_module
    added: list[dict] = []

    def fake_add_job(fn, **kwargs):
        added.append(kwargs)

    monkeypatch.setattr(module.scheduler.scheduler, "add_job", fake_add_job)
    # The guard is process-global; a leftover entry would make _run_fn return
    # early and every assertion below would pass for the wrong reason.
    module.run_guard.release("fiat")

    def run_with(status: str, is_retry: bool = False):
        added.clear()
        monkeypatch.setattr(module, "run_brand_sweep", lambda *a, **k: _FakeRun(status))
        module._run_fn(BrandConfig(slug="fiat", display_name="Fiat", make_id=31), is_retry=is_retry)
        return added

    return run_with


def test_a_failed_sweep_is_requeued_once_at_the_back_of_the_queue(requeue_probe):
    """The executor has a single worker, so a date-triggered job lands behind
    everything already submitted -- hours later, by which time a transient
    network fault has resolved. Fiat proved it on 29/07: failed at 15:12,
    recovered at 18:30 with 1,267 sales detected."""
    added = requeue_probe("error")
    assert len(added) == 1
    assert added[0]["trigger"] == "date"
    assert added[0]["kwargs"] == {"is_retry": True}


def test_a_partial_sweep_is_requeued(requeue_probe):
    """partial means the run did not evaluate sales -- its main job undone."""
    assert len(requeue_probe("partial")) == 1


def test_a_retry_that_fails_again_is_not_requeued(requeue_probe):
    """Audi failed identically five times on the same listing. A deterministic
    fault does not resolve by repetition, and a second retry only burns time."""
    assert requeue_probe("error", is_retry=True) == []


def test_a_blocked_sweep_is_never_requeued(requeue_probe):
    """Pressing on after a block lengthens the block."""
    assert requeue_probe("blocked") == []


def test_a_successful_sweep_is_not_requeued(requeue_probe):
    assert requeue_probe("success") == []
```

*Nota per chi implementa:* verificare la firma reale di `BrandConfig` prima di costruirlo, e che `module.queue_controller` non risulti fermo — se lo fosse, `_run_fn` uscirebbe prima di arrivare allo sweep e tutti i test passerebbero per la ragione sbagliata. Prima di considerare fatto questo blocco, rimuovere la condizione `not is_retry` dal codice e pretendere che `test_a_retry_that_fails_again_is_not_requeued` fallisca.

- [ ] **Step 2: Eseguire i test e verificare che falliscano**

```bash
sudo -n docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/test_app_wiring.py -v
```

Atteso: FALLISCONO, nessun job di ritentativo viene aggiunto.

- [ ] **Step 3: Implementare**

In `api/app.py`, cambiare la firma di `_run_fn` e aggiungere il riaccodamento:

```python
def _run_fn(brand: BrandConfig, is_retry: bool = False) -> None:
```

Dentro il `try` interno, dopo il controllo del blocco:

```python
            if run is not None and run.status == "blocked":
                queue_controller.halt(f"blocco rilevato su {brand.display_name}")
            elif run is not None and run.status in ("error", "partial") and not is_retry:
                # Back of the queue, not immediately: the executor has a single
                # worker, so a date-triggered job runs after everything already
                # submitted -- hours, by which time a transient network fault
                # has cleared. Once only: a deterministic fault fails identically
                # every time, as Audi demonstrated five times on one listing.
                scheduler.scheduler.add_job(
                    _run_fn, args=[brand], kwargs={"is_retry": True}, trigger="date",
                    id=f"retry-{brand.slug}-{int(time.time())}",
                )
                logger.warning(
                    "Sweep for %s ended %s; requeued once at the back of the queue",
                    brand.slug, run.status,
                )
```

- [ ] **Step 4: Eseguire i test**

```bash
sudo -n docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/test_app_wiring.py tests/test_scheduler.py -v
```

Atteso: tutti PASS.

- [ ] **Step 5: Commit**

```bash
git add scraper/src/autosmart24/api/app.py scraper/tests/test_app_wiring.py
git commit -m "feat: requeue a failed or partial sweep once at the back of the queue"
```

---

### Task 8: `partial` sulla dashboard e negli script

**Files:**
- Modify: `dashboard/src/components/BrandCard.tsx` (funzione `statusLabel`, righe 12-18)
- Modify: `run-completo.sh`, `run-recupero2.sh`
- Test: `dashboard/src/components/BrandCard.test.tsx` (creare se assente)

**Interfaces:**
- Consumes: lo stato `"partial"` dal Task 4.
- Produces: niente.

Il difetto attuale è silenzioso: `statusLabel` restituisce `"Attivo"` per qualunque stato non previsto, quindi una run `partial` oggi apparirebbe **come se fosse andata bene**.

- [ ] **Step 1: Scrivere il test**

```tsx
function brandWith(status: string) {
  return {
    slug: "fiat", brand: "Fiat", paused: false,
    last_run: { status, sold_detected: 0, new_listings: 0, listings_seen: 0 },
  } as never
}

const noop = () => {}

it("rende una run parziale con la sua etichetta", () => {
  render(<BrandCard brand={brandWith("partial")} onPause={noop} onResume={noop}
                    onRunNow={noop} onSelect={noop} />)
  expect(screen.getByText(/parziale/i)).toBeInTheDocument()
})

it("non spaccia una run parziale per riuscita", () => {
  // The pre-fix statusLabel fell through to "Attivo" for any unknown status,
  // so a run that never evaluated sales looked like a healthy one.
  render(<BrandCard brand={brandWith("partial")} onPause={noop} onResume={noop}
                    onRunNow={noop} onSelect={noop} />)
  expect(screen.queryByText("Attivo")).not.toBeInTheDocument()
})

it("distingue parziale da errore", () => {
  const { unmount } = render(<BrandCard brand={brandWith("partial")} onPause={noop}
                                        onResume={noop} onRunNow={noop} onSelect={noop} />)
  const partialClass = screen.getByText(/parziale/i).className
  unmount()
  render(<BrandCard brand={brandWith("error")} onPause={noop} onResume={noop}
                    onRunNow={noop} onSelect={noop} />)
  expect(screen.getByText(/errore/i).className).not.toBe(partialClass)
})
```

Adeguare la forma di `brandWith` al tipo `BrandStatusOut` reale.

- [ ] **Step 2: Eseguire il test e verificare che fallisca**

```bash
cd dashboard && npx vitest run
```

Atteso: FALLISCE — `partial` cade nel ramo finale e viene reso `"Attivo"`.

- [ ] **Step 3: Implementare**

In `BrandCard.tsx`, inserire il caso **prima** del `return "Attivo"` finale:

```tsx
  if (brand.last_run?.status === "partial") return "Parziale";
```

Nel foglio di stile aggiungere `.status-parziale` con un colore distinto sia da `.status-errore` sia dallo stato attivo: una run parziale non è un guasto — si riaccoda da sola — e colorarla come un errore manderebbe a cercare un problema che non c'è.

`filterBrands` in `BrandFilters.tsx` resta invariato: il filtro "error" seleziona ciò che richiede intervento umano, e una run parziale non lo richiede.

- [ ] **Step 4: Aggiornare gli script**

Negli script, estendere i rami terminali:

```bash
      success|error|blocked|partial)
```

In `run-completo.sh` la riga che segnala il blocco resta invariata; aggiungere sotto:

```bash
        [ "$S" = "partial" ] && echo "  ATTENZIONE: scansione incompleta, vendite non valutate per $BRAND"
```

- [ ] **Step 5: Eseguire i test**

```bash
cd dashboard && npx vitest run && npx tsc -b
```

Atteso: tutti PASS e nessun errore di tipo. `tsc -b` è obbligatorio: Vitest non fa controllo dei tipi, e in un lavoro precedente un errore di tipo è rimasto invisibile per cinque task con la suite completamente verde.

- [ ] **Step 6: Verificare l'intera suite backend**

```bash
sudo -n docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest -v
```

Atteso: i 219 test preesistenti più quelli nuovi, tutti PASS.

- [ ] **Step 7: Commit**

```bash
git add dashboard/src run-completo.sh run-recupero2.sh
git commit -m "feat: surface the partial run status in the dashboard and runners"
```

---

## Note per chi esegue

**Non c'è deploy in questo piano.** Ricostruire il container interrompe qualsiasi scansione in corso, quindi il deploy è una decisione dell'utente da chiedere alla fine, non un passo da eseguire di iniziativa. Verificare prima che non ci siano run attive con `curl -s http://localhost:8001/queue`.

**Un test che non può fallire non è copertura.** Per ognuno dei test qui sopra, prima di considerarlo fatto, rompere di proposito il codice che dovrebbe difendere e pretendere che il test lo denunci. In questo progetto quattro difetti sono passati perché i test non potevano fallire: dati di prova non discriminanti, conteggi di mock che si accumulavano, un'asserzione su una costante invece che su un comportamento.

**L'ordine dei task non è casuale.** I task 1-4 sono una catena: il 2 usa l'interfaccia del 1, il 4 quelle del 2 e del 3. I task 5 e 6 sono indipendenti e possono essere fatti in qualunque momento. I task 7 e 8 richiedono che il 4 sia concluso, perché consumano lo stato `partial`.
