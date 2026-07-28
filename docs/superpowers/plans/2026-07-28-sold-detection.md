# Rilevazione vendite affidabile — Piano di implementazione

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Impedire che risposte anomale del sito vengano scambiate per vendite, senza rinunciare a rilevare quelle vere.

**Architecture:** L'arricchimento smette del tutto di dichiarare vendite — quegli annunci sono appena stati visti vivi, quindi il segnale è contraddittorio. Gli annunci spariti dalla ricerca restano l'unico percorso che può dichiarare una vendita, ma solo dopo due verifiche indipendenti che confrontano anche l'identità dell'annuncio, per non farsi ingannare dal riuso degli id.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0.35, pytest 8.3.3, httpx 0.27.2

**Spec di riferimento:** `docs/superpowers/specs/2026-07-28-sold-detection-design.md`

## Global Constraints

- **Branch:** `master`. Il repository è condiviso con una seconda macchina che pubblica sullo stesso ramo: fare `git pull --rebase` prima di ogni push, mai `--force`.
- **Test backend** (l'unico modo su questa macchina — niente pip/venv, e il Dockerfile non copia `tests/`):
  ```bash
  cd /home/vperrone/AutoSmart24 && sudo -n docker compose run --rm --no-deps \
    -v "$PWD/scraper:/app" app pytest tests/<file> -v
  ```
  Usa SQLite in memoria: sicuro anche con uno scraping in corso.
- **Non ricostruire, riavviare o fermare alcun container.** `docker compose run --rm --no-deps` crea un container usa-e-getta e non tocca i servizi attivi; `up`, `down` e `build` sono vietati.
- Codice e commenti in inglese; `datetime.utcnow()` naive come nel resto del progetto.
- **La stringa `"Detail backlog page: enriched N"` è parsata via regex** da `monitor_batch2.sh` e `monitor_scrape.sh` (`enriched (\d+)`). La parte `enriched N` va lasciata intatta anche modificando il resto del messaggio.
- La suite parte da **201 test** che passano e non deve regredire, tranne dove questo piano prescrive esplicitamente di riscrivere un test.

---

### Task 1: L'arricchimento smette di dichiarare vendite

**Files:**
- Modify: `scraper/src/autosmart24/run_manager.py` (`process_detail_backlog`, il ramo `if result.sold:` intorno a riga 109; il messaggio di log della pagina; il chiamante in `run_brand_sweep`)
- Test: `scraper/tests/test_sold_detection.py` (nuovo), `scraper/tests/test_run_manager.py` (due test da riscrivere)

**Interfaces:**
- Produces: `process_detail_backlog(...) -> int` continua a restituire un intero, ma ora è il numero di annunci per cui la pagina ha **riportato una rimozione** senza che venga dichiarata una vendita. Il chiamante non lo somma più a `run.sold_detected`.

- [ ] **Step 1: Scrivere il test che riproduce l'incidente**

Nuovo file `scraper/tests/test_sold_detection.py`. Riproduce il caso Lancia: annuncio visto vivo nella ricerca, la cui pagina di dettaglio risponde poi come rimossa.

```python
import datetime as dt

from autosmart24.config import BrandConfig
from autosmart24.db.models import Listing, ScrapeEvent, ScrapeRun
from autosmart24.run_manager import process_detail_backlog, run_brand_sweep
from autosmart24.scraping.detail_queue import DetailResult
from autosmart24.scraping.http_client import RateLimitedClient

BRAND = BrandConfig(slug="fiat", make_id=28, display_name="Fiat")


def _client() -> RateLimitedClient:
    return RateLimitedClient(min_delay_seconds=0, max_delay_seconds=0, sleep_fn=lambda _: None)


def _listing(listing_id: str, *, brand: str = "Fiat", detail_scraped: bool = False) -> Listing:
    now = dt.datetime(2026, 7, 28, 9, 0, 0)
    return Listing(
        id=listing_id, brand=brand, url=f"https://www.autoscout24.it/annunci/{listing_id}",
        first_seen_at=now, last_seen_at=now, last_checked_at=now,
        status="active", detail_scraped=detail_scraped, price=10000,
        first_registration=dt.date(2020, 1, 1),
    )


def _snippet(listing_id: str, price: int = 10000) -> dict:
    return {
        "id": listing_id, "cross_reference_id": listing_id, "brand": "Fiat",
        "model": "Panda", "model_group": "Panda", "variant": None,
        "motor_type_name": "1.0", "version_input": None, "transmission": "Manuale",
        "fuel": "Benzina", "first_registration": dt.date(2020, 1, 1), "mileage_km": 50000,
        "seller_type": "Dealer", "seller_company_name": "Test Dealer",
        "city": "Roma - Roma - RM", "zip_code": "00100", "price": price,
        "url": f"https://www.autoscout24.it/annunci/{listing_id}",
    }


def test_enrichment_does_not_sell_a_listing_seen_alive_in_the_same_sweep(db_session):
    """The Lancia incident: 139 listings were seen alive in the search results,
    then their detail pages answered 410 during enrichment and every one was
    marked sold. All were still live on the site. A detail-page removal cannot
    outweigh a search-listing sighting made minutes earlier."""
    db_session.add(_listing("seen-alive-1"))
    db_session.commit()

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        yield _snippet("seen-alive-1")

    def fake_detail(client, url):
        return DetailResult(sold=True)

    run = run_brand_sweep(db_session, _client, BRAND, crawl_fn=fake_crawl, fetch_detail_fn=fake_detail)

    listing = db_session.get(Listing, "seen-alive-1")
    assert listing.status == "active", "una pagina che risponde rimossa non deve battere un avvistamento nella ricerca"
    assert listing.sold_at is None
    assert run.sold_detected == 0


def test_enrichment_keeps_the_listing_in_the_backlog_for_a_later_retry(db_session):
    """detail_scraped must stay false: the listing was never actually enriched,
    so marking it done would turn a false sale into permanently missing data."""
    db_session.add(_listing("retry-me-1"))
    db_session.commit()
    run = ScrapeRun(brand="Fiat", started_at=dt.datetime.utcnow(), status="running")
    db_session.add(run)
    db_session.flush()

    def fake_detail(client, url):
        return DetailResult(sold=True)

    process_detail_backlog(db_session, _client, BRAND, run, fetch_detail_fn=fake_detail)

    listing = db_session.get(Listing, "retry-me-1")
    assert listing.detail_scraped is False
    assert listing.status == "active"


def test_enrichment_records_the_anomaly_as_an_event(db_session):
    db_session.add(_listing("anomaly-1"))
    db_session.commit()
    run = ScrapeRun(brand="Fiat", started_at=dt.datetime.utcnow(), status="running")
    db_session.add(run)
    db_session.flush()

    def fake_detail(client, url):
        return DetailResult(sold=True)

    process_detail_backlog(db_session, _client, BRAND, run, fetch_detail_fn=fake_detail)

    events = db_session.query(ScrapeEvent).filter_by(level="warning").all()
    assert any("anomaly-1" in e.message for e in events)
```

- [ ] **Step 2: Eseguire per vederlo fallire**

Run: `cd /home/vperrone/AutoSmart24 && sudo -n docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/test_sold_detection.py -v`

Expected: FAIL. Il primo test fallisce con `assert 'sold' == 'active'` — è la riproduzione fedele del difetto. Se passasse già, il test non starebbe misurando ciò che crediamo: fermarsi e capire perché.

- [ ] **Step 3: Rimuovere la dichiarazione di vendita dall'arricchimento**

In `process_detail_backlog`, sostituire il ramo:

```python
                if result.sold:
                    row.status = "sold"
                    row.sold_at = now
                    sold += 1
                    continue
```

con:

```python
                if result.sold:
                    # This listing is in the enrichment queue precisely because
                    # the search results just showed it alive, so a removal
                    # reported here contradicts an observation made minutes ago
                    # rather than confirming one. A transient site failure
                    # produced 139 false sales this way on 2026-07-28.
                    #
                    # Leave the row active and detail_scraped false: it was
                    # never actually enriched, and if it really did sell it will
                    # be absent from the next sweep's search results and judged
                    # by the missing-listing path, which has grounds to decide.
                    reported_removed += 1
                    _log_event(
                        session, run, "warning",
                        f"Detail page reported removed for {row.id}, seen alive in this sweep: left active",
                        url=row.url,
                    )
                    continue
```

Rinominare il contatore `sold = 0` in `reported_removed = 0` (inizializzato accanto a `enriched = 0`), e aggiornare il messaggio di fine pagina mantenendo **intatto** il frammento `enriched {enriched}`, che gli script di monitoraggio parsano via regex:

```python
        _log_event(
            session, run, "info",
            f"Detail backlog page: enriched {enriched}, reported removed {reported_removed} (page size {len(pending)})",
        )
```

Lo stesso messaggio compare **due volte** nella funzione — nel ramo `except BlockedError` e nel flusso normale di fine pagina: aggiornarli entrambi.

La funzione ha inoltre **tre punti** che usano il contatore, tutti da rinominare in modo coerente:

1. l'inizializzazione `total_sold = 0` in cima → `total_reported = 0`
2. `return total_sold + sold` nel ramo `except BlockedError` → `return total_reported + reported_removed`
3. `total_sold += sold` e `return total_sold` nel flusso normale → `total_reported += reported_removed` e `return total_reported`

Verificare con `grep -n "total_sold\|sold" scraper/src/autosmart24/run_manager.py` che in `process_detail_backlog` non resti alcun riferimento al vecchio nome: un residuo comprometterebbe il conteggio senza far fallire alcun test, perché nessuno asserisce più su quel valore.

- [ ] **Step 4: Scollegare il valore restituito da `sold_detected`**

In `run_brand_sweep`, il chiamante:

```python
        backlog_sold_count = 0
        if run.status != "blocked":
            ...
            backlog_sold_count = process_detail_backlog(...)
```

diventa:

```python
        backlog_removed_reports = 0
        if run.status != "blocked":
            ...
            backlog_removed_reports = process_detail_backlog(...)
```

e la riga che compone il totale:

```python
        run.sold_detected = sold_count + backlog_sold_count
```

diventa:

```python
        # Only the missing-listing path can declare a sale; the backlog's
        # removal reports are diagnostic and deliberately excluded.
        run.sold_detected = sold_count
```

`backlog_removed_reports` resta inutilizzato nel calcolo: va lasciato assegnato perché il valore compare nei log ed è utile in diagnosi. Se il linter dovesse lamentarsene, prefissarlo con `_`.

- [ ] **Step 5: Verificare che i nuovi test passino**

Run: `cd /home/vperrone/AutoSmart24 && sudo -n docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/test_sold_detection.py -v`
Expected: PASS (3 test)

- [ ] **Step 6: Riscrivere i due test che asserivano il comportamento vecchio**

Falliranno, ed è corretto. **Non aggiustarli per farli passare**: documentavano il difetto. Vanno riscritti per asserire il nuovo comportamento.

In `scraper/tests/test_run_manager.py`, sostituire `test_process_detail_backlog_returns_sold_count` con:

```python
def test_process_detail_backlog_reports_removals_without_selling(db_session):
    """A detail page reporting removal no longer sells the listing: it is in the
    backlog because the search results just showed it alive. The count returned
    is diagnostic only."""
    db_session.add(_existing_listing("backlog-sold-1", 10000, detail_scraped=False))
    db_session.commit()
    run = ScrapeRun(brand="Fiat", started_at=dt.datetime.utcnow(), status="running")
    db_session.add(run)
    db_session.flush()

    def fake_fetch_detail(client, url):
        return DetailResult(sold=True)

    reported = process_detail_backlog(db_session, _client, BRAND, run, fetch_detail_fn=fake_fetch_detail)

    assert reported == 1
    listing = db_session.get(Listing, "backlog-sold-1")
    assert listing.status == "active"
    assert listing.detail_scraped is False
```

e sostituire `test_run_brand_sweep_counts_backlog_confirmed_sold_in_sold_detected` con:

```python
def test_run_brand_sweep_does_not_count_backlog_removals_as_sales(db_session):
    """The listing IS present in the current sweep, so it never reaches the
    missing_ids path; the backlog pass sees its detail page report a removal.
    Before 2026-07-28 that marked it sold, which produced 139 false sales in a
    single Lancia run. It must now stay active."""
    db_session.add(_existing_listing("backlog-sold-2", 10000, detail_scraped=False))
    db_session.commit()

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        yield _fake_snippet("backlog-sold-2", 10000)

    def fake_fetch_detail(client, url):
        return DetailResult(sold=True)

    run = run_brand_sweep(db_session, _client, BRAND, crawl_fn=fake_crawl, fetch_detail_fn=fake_fetch_detail)

    assert run.sold_detected == 0
    listing = db_session.get(Listing, "backlog-sold-2")
    assert listing.status == "active"
```

- [ ] **Step 7: Eseguire l'intera suite**

Run: `cd /home/vperrone/AutoSmart24 && sudo -n docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/ -q`

Expected: tutti verdi. Se fallisce un test diverso dai due riscritti, **non modificarlo**: segnalarlo. Gli altri riferimenti a `sold` riguardano il percorso degli annunci spariti, che questo task non tocca, e devono passare invariati.

- [ ] **Step 8: Commit**

```bash
cd /home/vperrone/AutoSmart24
git add scraper/src/autosmart24/run_manager.py scraper/tests/test_sold_detection.py scraper/tests/test_run_manager.py
git commit -m "fix: stop the enrichment pass from declaring sales"
```

---

### Task 2: Riconoscere un annuncio rimosso, distinguendolo da un id riassegnato

**Files:**
- Create: `scraper/src/autosmart24/scraping/sold_confirmation.py`
- Test: `scraper/tests/test_sold_confirmation.py`

**Interfaces:**
- Consumes: `DetailResult` da `autosmart24.scraping.detail_queue` (campi `sold: bool`, `data: dict | None`)
- Produces: `looks_removed(result: DetailResult, expected_brand: str) -> bool`

**Perché serve.** `RateLimitedClient` è costruito con `follow_redirects=True`. AutoScout riassegna l'id di un annuncio ritirato a un'auto diversa e fa rispondere il vecchio URL con un `308` verso la nuova: seguendo il redirect si atterra su una pagina **attiva ma di un'altra auto**. Senza questo controllo il codice concluderebbe "non venduto" per un annuncio che invece è sparito — l'errore speculare a quello corretto nel Task 1. Il difetto del riuso id è documentato in `docs/superpowers/specs/2026-07-28-listing-id-reuse-known-issue.md`.

- [ ] **Step 1: Scrivere i test**

```python
# scraper/tests/test_sold_confirmation.py
from autosmart24.scraping.detail_queue import DetailResult
from autosmart24.scraping.sold_confirmation import looks_removed


def _detail(brand: str | None) -> dict:
    return {"brand": brand, "model": "Panda", "price": 10000}


def test_an_explicit_removal_counts_as_removed():
    assert looks_removed(DetailResult(sold=True), "Fiat") is True


def test_a_live_page_for_the_same_brand_is_not_removed():
    assert looks_removed(DetailResult(sold=False, data=_detail("Fiat")), "Fiat") is False


def test_a_live_page_for_a_different_brand_means_the_id_was_reassigned():
    """AutoScout reuses a retired listing's id for another car and 308-redirects
    the old URL to it. The client follows redirects, so the page loads fine and
    looks active — but it is a different car, which means the listing we asked
    about is gone."""
    assert looks_removed(DetailResult(sold=False, data=_detail("Audi")), "Mercedes-Benz") is True


def test_a_missing_brand_is_not_treated_as_reassignment():
    """An absent brand field is missing information, not evidence of reuse.
    Concluding 'removed' from it would invent sales out of parsing gaps."""
    assert looks_removed(DetailResult(sold=False, data=_detail(None)), "Fiat") is False


def test_a_result_without_data_is_not_removed():
    assert looks_removed(DetailResult(sold=False, data=None), "Fiat") is False


def test_brand_comparison_ignores_case_and_padding():
    """Snippet and detail pages have been seen to differ in casing; that is not
    a reassignment."""
    assert looks_removed(DetailResult(sold=False, data=_detail(" fiat ")), "Fiat") is False
```

- [ ] **Step 2: Eseguire per vederli fallire**

Run: `cd /home/vperrone/AutoSmart24 && sudo -n docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/test_sold_confirmation.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'autosmart24.scraping.sold_confirmation'`

- [ ] **Step 3: Implementare**

```python
# scraper/src/autosmart24/scraping/sold_confirmation.py
from __future__ import annotations

from autosmart24.scraping.detail_queue import DetailResult


def looks_removed(result: DetailResult, expected_brand: str) -> bool:
    """Whether a detail response means the listing we asked about is gone.

    Two ways a listing can be gone, and only one is obvious:

    * the site says so — 404/410, or a status other than Active
    * the id was reassigned. AutoScout recycles a retired listing's id for an
      unrelated car and 308-redirects the old URL to it. The HTTP client
      follows redirects, so the page loads, looks perfectly active, and
      describes a different car. Without the brand comparison below the caller
      would read that as "still on sale" and keep a retired listing active
      forever.

    A missing brand field is deliberately NOT treated as reassignment: that is
    absent information, and concluding "removed" from it would manufacture
    sales out of parsing gaps.
    """
    if result.sold:
        return True

    if not result.data:
        return False

    actual_brand = result.data.get("brand")
    if not actual_brand:
        return False

    return actual_brand.strip().casefold() != expected_brand.strip().casefold()
```

- [ ] **Step 4: Verificare che passino**

Run: `cd /home/vperrone/AutoSmart24 && sudo -n docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/test_sold_confirmation.py -v`
Expected: PASS (6 test)

- [ ] **Step 5: Commit**

```bash
cd /home/vperrone/AutoSmart24
git add scraper/src/autosmart24/scraping/sold_confirmation.py scraper/tests/test_sold_confirmation.py
git commit -m "feat: recognise a removed listing, including a reassigned id"
```

---

### Task 3: Doppia conferma prima di dichiarare una vendita

**Files:**
- Modify: `scraper/src/autosmart24/run_manager.py` (il blocco `missing_ids` in `run_brand_sweep`)
- Test: `scraper/tests/test_sold_detection.py` (aggiunte)

**Interfaces:**
- Consumes: `looks_removed(result, expected_brand)` dal Task 2
- Produces: nessuna nuova firma pubblica; cambia il comportamento di `run_brand_sweep` sul percorso degli annunci spariti

**Il punto.** Oggi un annuncio assente dalla ricerca viene verificato una volta e, se la pagina lo dà rimosso, marcato venduto. Una sola richiesta caduta in una finestra anomala basta a inventare una vendita. La verifica diventa quindi in due tempi: i candidati emergono durante lo sweep e vengono riconfermati **alla fine**, quando sono passati minuti — un guasto breve non colpisce entrambe le richieste.

- [ ] **Step 1: Scrivere i test**

Aggiungere a `scraper/tests/test_sold_detection.py`:

```python
def test_a_vanished_listing_needs_two_confirmations_to_be_sold(db_session):
    db_session.add(_listing("gone-1", detail_scraped=True))
    db_session.commit()
    calls = []

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        return iter(())          # non compare più nella ricerca

    def fake_detail(client, url):
        calls.append(url)
        return DetailResult(sold=True)

    run = run_brand_sweep(db_session, _client, BRAND, crawl_fn=fake_crawl, fetch_detail_fn=fake_detail)

    assert len([u for u in calls if "gone-1" in u]) == 2, "servono due verifiche indipendenti"
    listing = db_session.get(Listing, "gone-1")
    assert listing.status == "sold"
    assert run.sold_detected == 1


def test_a_listing_that_reappears_on_the_second_check_stays_active(db_session):
    """The transient-failure case: the first check answers removed, the second —
    minutes later — finds it alive. No sale is declared."""
    db_session.add(_listing("flapping-1", detail_scraped=True))
    db_session.commit()
    seen = {"n": 0}

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        return iter(())

    def fake_detail(client, url):
        seen["n"] += 1
        if seen["n"] == 1:
            return DetailResult(sold=True)
        return DetailResult(sold=False, data={"brand": "Fiat", "model": "Panda", "price": 10000})

    run = run_brand_sweep(db_session, _client, BRAND, crawl_fn=fake_crawl, fetch_detail_fn=fake_detail)

    listing = db_session.get(Listing, "flapping-1")
    assert listing.status == "active"
    assert run.sold_detected == 0
    assert run.errors_count >= 1, "la discordanza va registrata come anomalia"


def test_a_reassigned_id_counts_as_removed_on_both_checks(db_session):
    """Both checks load a live page, but for a different brand: the id was
    recycled, so our listing is gone."""
    db_session.add(_listing("reused-1", detail_scraped=True))
    db_session.commit()

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        return iter(())

    def fake_detail(client, url):
        return DetailResult(sold=False, data={"brand": "Audi", "model": "Q3", "price": 20000})

    run = run_brand_sweep(db_session, _client, BRAND, crawl_fn=fake_crawl, fetch_detail_fn=fake_detail)

    listing = db_session.get(Listing, "reused-1")
    assert listing.status == "sold"
    assert run.sold_detected == 1


def test_no_candidates_means_no_second_pass(db_session):
    """A listing still present in the search results never becomes a candidate,
    so the confirmation pass must issue no requests at all."""
    db_session.add(_listing("present-1", detail_scraped=True))
    db_session.commit()
    calls = []

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        yield _snippet("present-1")

    def fake_detail(client, url):
        calls.append(url)
        return DetailResult(sold=True)

    run_brand_sweep(db_session, _client, BRAND, crawl_fn=fake_crawl, fetch_detail_fn=fake_detail)

    assert calls == []
```

- [ ] **Step 2: Eseguire per vederli fallire**

Run: `cd /home/vperrone/AutoSmart24 && sudo -n docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/test_sold_detection.py -v -k "two_confirmations or reappears or reassigned or no_candidates"`
Expected: FAIL — il primo test riporta una sola chiamata invece di due.

- [ ] **Step 3: Trasformare il primo controllo in una raccolta di candidati**

In `run_brand_sweep`, sostituire il blocco che segue `missing_ids = ...`:

```python
        missing_ids = set(active_db_prices.keys()) - seen_ids
        now = _now()
        sold_count = 0
        missing_jobs = [(listing_id, active_rows_by_id[listing_id].url) for listing_id in missing_ids]

        def _missing_worker(job: tuple[str, str], client: RateLimitedClient) -> list[tuple[str, object]]:
            listing_id, url = job
            return [(listing_id, fetch_detail_fn(client, url))]

        try:
            for listing_id, result in run_worker_pool(
                missing_jobs, _missing_worker, client_factory, concurrency, session_refresh_requests
            ):
                row = active_rows_by_id[listing_id]
                row.last_checked_at = now
                if result.sold:
                    row.status = "sold"
                    row.sold_at = now
                    sold_count += 1
                else:
                    run.errors_count += 1
                    _log_event(
                        session, run, "warning",
                        f"Listing {listing_id} not found in sweep but still active on detail page",
                        url=row.url,
                    )
        except BlockedError as exc:
            run.status = "blocked"
            run.errors_count += 1
            _log_event(session, run, "blocked", str(exc), url=exc.url)
```

con:

```python
        missing_ids = set(active_db_prices.keys()) - seen_ids
        now = _now()
        sold_count = 0
        missing_jobs = [(listing_id, active_rows_by_id[listing_id].url) for listing_id in missing_ids]

        # First pass: absence from the search results is only a suspicion. A
        # single request that lands inside a bad window is enough to invent a
        # sale, so nothing is decided here — candidates are re-checked at the
        # end of the sweep, minutes later.
        sold_candidates: list[str] = []

        def _missing_worker(job: tuple[str, str], client: RateLimitedClient) -> list[tuple[str, object]]:
            listing_id, url = job
            return [(listing_id, fetch_detail_fn(client, url))]

        try:
            for listing_id, result in run_worker_pool(
                missing_jobs, _missing_worker, client_factory, concurrency, session_refresh_requests
            ):
                row = active_rows_by_id[listing_id]
                row.last_checked_at = now
                if looks_removed(result, row.brand):
                    sold_candidates.append(listing_id)
                else:
                    run.errors_count += 1
                    _log_event(
                        session, run, "warning",
                        f"Listing {listing_id} not found in sweep but still active on detail page",
                        url=row.url,
                    )
        except BlockedError as exc:
            run.status = "blocked"
            run.errors_count += 1
            _log_event(session, run, "blocked", str(exc), url=exc.url)
```

Aggiungere l'import in cima al file, accanto agli altri di `autosmart24.scraping`:

```python
from autosmart24.scraping.sold_confirmation import looks_removed
```

- [ ] **Step 4: Aggiungere la conferma finale**

Subito **dopo** la chiamata a `process_detail_backlog` e **prima** del blocco che assegna `run.status`, `run.phase` e `run.finished_at`, inserire:

```python
        # Second pass: re-check every candidate. Minutes have gone by since the
        # first check, so a brief site failure cannot fool both. Only listings
        # that look removed twice are declared sold.
        if sold_candidates and run.status != "blocked":
            confirm_now = _now()
            confirm_jobs = [(lid, active_rows_by_id[lid].url) for lid in sold_candidates]

            def _confirm_worker(job: tuple[str, str], client: RateLimitedClient) -> list[tuple[str, object]]:
                listing_id, url = job
                return [(listing_id, fetch_detail_fn(client, url))]

            try:
                for listing_id, result in run_worker_pool(
                    confirm_jobs, _confirm_worker, client_factory, concurrency, session_refresh_requests
                ):
                    row = active_rows_by_id[listing_id]
                    row.last_checked_at = confirm_now
                    if looks_removed(result, row.brand):
                        row.status = "sold"
                        row.sold_at = confirm_now
                        sold_count += 1
                    else:
                        # Removed on the first check, alive on the second: the
                        # site was answering badly. Exactly the case that
                        # produced 139 false sales before this pass existed.
                        run.errors_count += 1
                        _log_event(
                            session, run, "warning",
                            f"Listing {listing_id} looked removed then active again: no sale declared",
                            url=row.url,
                        )
                session.commit()
            except BlockedError as exc:
                # Unconfirmed candidates stay active: a block is not evidence
                # of a sale.
                run.status = "blocked"
                run.errors_count += 1
                _log_event(session, run, "blocked", str(exc), url=exc.url)
```

- [ ] **Step 5: Verificare che i test passino**

Run: `cd /home/vperrone/AutoSmart24 && sudo -n docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/test_sold_detection.py -v`
Expected: PASS (7 test)

- [ ] **Step 6: Eseguire l'intera suite**

Run: `cd /home/vperrone/AutoSmart24 && sudo -n docker compose run --rm --no-deps -v "$PWD/scraper:/app" app pytest tests/ -q`

Expected: tutti verdi. Attenzione a `test_run_brand_sweep_confirms_sold_when_detail_confirms` e a `test_run_brand_sweep_marks_blocked_and_stops_on_block_during_missing_ids_loop`: usano il percorso appena modificato e **devono continuare a passare**. Se il primo fallisce perché ora servono due chiamate, è legittimo aggiornarne il fake per rispondere due volte — ma **non** cambiare ciò che asserisce.

- [ ] **Step 7: Commit**

```bash
cd /home/vperrone/AutoSmart24
git add scraper/src/autosmart24/run_manager.py scraper/tests/test_sold_detection.py
git commit -m "fix: require two independent confirmations before declaring a sale"
```

---

### Task 4: Verifica sui dati reali e deploy

**Files:** nessuna modifica al codice

Il difetto è stato scoperto confrontando il database con il sito, non leggendo il codice: la stessa verifica va rifatta dopo il fix.

- [ ] **Step 1: Controllare che non restino record sospetti**

```bash
cd /home/vperrone/AutoSmart24
sudo -n docker compose exec -T postgres psql -U autosmart24 -d autosmart24 -tAc "
SELECT count(*) FILTER (WHERE sold_at - first_seen_at < interval '60 minutes')||' sospetti su '||count(*)||' venduti'
FROM listings WHERE status='sold';"
```

Expected: `0 sospetti`. I 167 record precedenti sono già stati corretti il 28/07; se ne compaiono altri, sono stati prodotti da uno scraping successivo alla correzione e vanno riportati ad `active` con lo stesso criterio.

- [ ] **Step 2: Chiedere all'utente il via libera per il deploy**

Il deploy ricostruisce il container `app` e **interrompe qualsiasi scraping in corso**. Non procedere di iniziativa: verificare prima che non ci siano run attive.

```bash
curl -s http://localhost:8001/queue | python3 -c "import sys,json;c=json.load(sys.stdin)['current'];print('run attiva:', c['brand'] if c else 'nessuna')"
```

- [ ] **Step 3: Deploy (solo con stack libero e via libera ottenuto)**

```bash
cd /home/vperrone/AutoSmart24
sudo -n docker compose build app
sudo -n docker compose up -d app
sudo -n docker compose logs --tail=20 app
```

Nessuna migrazione: questo lavoro non tocca lo schema.

- [ ] **Step 4: Verificare sul campo dopo la prima run**

Dopo il primo sweep completo su una marca, ripetere la verifica che ha scoperto il difetto: estrarre a campione annunci marcati `sold` e aprire le loro pagine, controllando che risultino davvero rimossi (o che descrivano un'auto di un'altra marca, nel caso del riuso id).

```bash
sudo -n docker compose exec -T postgres psql -U autosmart24 -d autosmart24 -tAc "
SELECT url FROM listings WHERE status='sold' AND sold_at > now() - interval '3 hours' ORDER BY random() LIMIT 10;"
```

Un campione interamente confermato è il segnale che il fix regge sui dati veri. Anche un solo annuncio ancora attivo va indagato prima di considerare chiuso il lavoro.

---

## Note di esecuzione

- I Task 1-3 si scrivono e si testano con lo scraping in corso: la suite usa SQLite in memoria.
- Solo il Task 4 tocca la produzione, e richiede stack libero più il via libera esplicito.
- Il repository è condiviso con la seconda macchina: `git pull --rebase` prima di ogni push.
