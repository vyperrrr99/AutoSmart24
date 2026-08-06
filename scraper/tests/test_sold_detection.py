import datetime as dt

from autosmart24.config import BrandConfig
from autosmart24.equipment import COLUMNS as EQUIPMENT_COLUMNS
from autosmart24.db.models import Listing, ScrapeEvent, ScrapeRun
from autosmart24.run_manager import process_detail_backlog, run_brand_sweep
from autosmart24.scraping.detail_queue import DetailResult
from autosmart24.scraping.http_client import BlockedError, RateLimitedClient

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
        # Stands in for a crawl that ran to exhaustion (crawl_brand sets
        # report.finished=True at its final block) -- the coverage gate now
        # also consults `finished`, so a stub that skips this would be
        # mistaken for an abandoned crawl and force the `partial` branch.
        report = kwargs.get("report")
        if report is not None:
            report.finished = True
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


def test_a_vanished_listing_needs_two_confirmations_to_be_sold(db_session):
    db_session.add(_listing("gone-1", detail_scraped=True))
    db_session.commit()
    calls = []

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        # Stands in for a crawl that ran to exhaustion (crawl_brand sets
        # report.finished=True at its final block) -- the coverage gate now
        # also consults `finished`, so a stub that skips this would be
        # mistaken for an abandoned crawl and force the `partial` branch.
        report = kwargs.get("report")
        if report is not None:
            report.finished = True
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
        # Stands in for a crawl that ran to exhaustion (crawl_brand sets
        # report.finished=True at its final block) -- the coverage gate now
        # also consults `finished`, so a stub that skips this would be
        # mistaken for an abandoned crawl and force the `partial` branch.
        report = kwargs.get("report")
        if report is not None:
            report.finished = True
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
    events = db_session.query(ScrapeEvent).filter_by(level="warning").all()
    assert any("flapping-1" in e.message for e in events), "la discordanza va anche registrata come evento"


def test_a_reassigned_id_counts_as_removed_on_both_checks(db_session):
    """Both checks load a live page, but for a different brand: the id was
    recycled, so our listing is gone."""
    db_session.add(_listing("reused-1", detail_scraped=True))
    db_session.commit()

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        # Stands in for a crawl that ran to exhaustion (crawl_brand sets
        # report.finished=True at its final block) -- the coverage gate now
        # also consults `finished`, so a stub that skips this would be
        # mistaken for an abandoned crawl and force the `partial` branch.
        report = kwargs.get("report")
        if report is not None:
            report.finished = True
        return iter(())

    def fake_detail(client, url):
        return DetailResult(sold=False, data={"brand": "Audi", "model": "Q3", "price": 20000})

    run = run_brand_sweep(db_session, _client, BRAND, crawl_fn=fake_crawl, fetch_detail_fn=fake_detail)

    listing = db_session.get(Listing, "reused-1")
    assert listing.status == "sold"
    assert run.sold_detected == 1


def test_no_candidates_means_no_second_pass(db_session):
    """A listing missing from the search results but still active on its own
    detail page never becomes a sold candidate, so the confirmation pass must
    issue no further requests for it -- only the single first-pass check."""
    db_session.add(_listing("present-1", detail_scraped=True))
    db_session.add(_listing("still-active-1", detail_scraped=True))
    db_session.commit()
    calls = []

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        # Stands in for a crawl that ran to exhaustion (crawl_brand sets
        # report.finished=True at its final block) -- the coverage gate now
        # also consults `finished`, so a stub that skips this would be
        # mistaken for an abandoned crawl and force the `partial` branch.
        report = kwargs.get("report")
        if report is not None:
            report.finished = True
        yield _snippet("present-1")  # still-active-1 is missing from the sweep

    def fake_detail(client, url):
        calls.append(url)
        return DetailResult(sold=False, data={"brand": "Fiat", "model": "Panda", "price": 10000})

    run_brand_sweep(db_session, _client, BRAND, crawl_fn=fake_crawl, fetch_detail_fn=fake_detail)

    assert calls == ["https://www.autoscout24.it/annunci/still-active-1"], (
        "present-1 must never be queried; still-active-1 must be checked exactly "
        "once -- looks_removed is False so it never becomes a candidate and the "
        "confirmation pass must issue no second request for it"
    )


def test_a_block_during_the_confirmation_pass_leaves_unreached_candidates_active(db_session):
    """run_worker_pool yields every result already produced and raises the
    captured exception only after the job queue is drained (see
    concurrency.py) -- so with two candidates in the confirmation pass, one
    already confirmed before the block hit must be marked sold, while the one
    the pool never reached must stay active. A block is not evidence of a
    sale for a candidate nobody actually re-checked."""
    db_session.add(_listing("blk-a", detail_scraped=True))
    db_session.add(_listing("blk-b", detail_scraped=True))
    db_session.commit()

    call_counts: dict[str, int] = {}
    second_pass_hits = {"n": 0}

    def fake_crawl(client, brand_slug, make_id, **kwargs):
        # Stands in for a crawl that ran to exhaustion (crawl_brand sets
        # report.finished=True at its final block) -- the coverage gate now
        # also consults `finished`, so a stub that skips this would be
        # mistaken for an abandoned crawl and force the `partial` branch.
        report = kwargs.get("report")
        if report is not None:
            report.finished = True
        return iter(())  # both listings are missing from the search results

    def fake_detail(client, url):
        call_counts[url] = call_counts.get(url, 0) + 1
        if call_counts[url] == 1:
            # First pass: both look removed, both become candidates.
            return DetailResult(sold=True)
        # Second pass (this URL's second call): the first candidate the pool
        # reaches confirms removed and is committed as sold; the second one
        # the pool reaches raises the block, so it never gets a verdict.
        second_pass_hits["n"] += 1
        if second_pass_hits["n"] == 1:
            return DetailResult(sold=True)
        raise BlockedError(403, url)

    run = run_brand_sweep(db_session, _client, BRAND, crawl_fn=fake_crawl, fetch_detail_fn=fake_detail)

    assert run.status == "blocked"
    listings = [db_session.get(Listing, "blk-a"), db_session.get(Listing, "blk-b")]
    sold = [l for l in listings if l.status == "sold"]
    active = [l for l in listings if l.status == "active"]
    assert len(sold) == 1, "the candidate confirmed before the block must be sold"
    assert sold[0].sold_at is not None
    assert len(active) == 1, "the candidate the pool never reached must stay active"
    assert active[0].sold_at is None


def _seed_active_listing(session, listing_id: str, *, brand: str = "Fiat") -> None:
    # detail_scraped=True: this represents a listing already known and
    # enriched before this sweep. Without it the row would also be
    # detail_scraped=False and the same-sweep backlog would pick it up and
    # call fetch_detail_fn on it too, contaminating tests that key on which
    # URLs the sold-detection passes (missing/confirm), as opposed to
    # enrichment, actually fetched.
    session.add(_listing(listing_id, brand=brand, detail_scraped=True))
    session.commit()


def _full_detail_data(**overrides) -> dict:
    """A fully-populated detail payload. Every listing crawl_fn returns lands
    in the same-sweep detail-enrichment backlog (see
    test_run_brand_sweep_fetches_detail_for_listings_new_in_this_same_sweep in
    test_run_manager.py) -- process_detail_backlog indexes this dict by every
    key unconditionally, so a stub fetch_detail_fn used anywhere a fresh
    listing might reach the backlog needs the full shape, not just the field
    a given test cares about."""
    data = {
        "price": None, "power_kw": None, "power_cv": None, "displacement_ccm": None,
        "body_type": None, "body_color": None, "num_seats": None, "num_doors": None,
        "num_previous_owners": None, "province": None, "latitude": None, "longitude": None,
        "vat_exposed": None, "price_evaluation_category": None, "price_evaluation_median": None,
        "created_at_source": None,
        "had_accident": None, "has_full_service_history": None, "gears": None, "drive_train": None,
        "cylinders": None, "weight_kg": None, "co2_emissions_g_km": None,
        "fuel_consumption_combined": None, "fuel_consumption_urban": None, "fuel_consumption_extra_urban": None,
        "emission_class": None, "upholstery": None, "upholstery_color": None,
        "is_conditional_price": None, "interaction_count": None, "favorites_count": None,
        "new_driver_suitable": None, "dealer": None,
        "paint_type": None, "body_color_original": None, "equipment": None,
        # Presi dal modulo, non riscritti a mano: aggiungere una decima
        # dotazione non deve rompere ogni test che sfiora l'arricchimento.
        **{c: None for c in EQUIPMENT_COLUMNS},
    }
    data.update(overrides)
    return data


def test_a_lost_model_suppresses_sold_detection_and_marks_the_run_partial(db_session):
    """The whole point: a listing absent from an incomplete crawl must not
    even be considered for sale."""
    _seed_active_listing(db_session, "gone-1", brand="Fiat")

    def crawl_fn(client_factory, slug, make_id, year_from=None, concurrency=1,
                 session_refresh_requests=30, report=None):
        if report is not None:
            report.lost_models.append(("modello-perso",))
            report.finished = True
        return iter([_snippet("still-here-1")])

    calls: list[str] = []
    gone_url = "https://www.autoscout24.it/annunci/gone-1"

    def fetch_detail_fn(client, url):
        # Serves the same-sweep backlog enriching "still-here-1" normally;
        # what this test verifies is which URL is ABSENT from calls, not that
        # calling raises -- run_worker_pool isolates any non-BlockedError
        # exception per job (concurrency.py), so an AssertionError raised here
        # would be silently swallowed and prove nothing.
        calls.append(url)
        return DetailResult(sold=False, data=_full_detail_data())

    run = run_brand_sweep(
        db_session, _client, BRAND, crawl_fn=crawl_fn,
        fetch_detail_fn=fetch_detail_fn, concurrency=1,
    )

    assert run.status == "partial"
    assert run.sold_detected == 0
    assert db_session.get(Listing, "gone-1").status == "active"
    assert calls, "the backlog enrichment pass must still have run"
    assert gone_url not in calls, "sold detection must not run when a model was lost"


def test_an_unfinished_crawl_report_suppresses_sold_detection_even_with_empty_loss_lists(db_session):
    """`finished` guards against reading a partial report as a clean one: a
    crawl abandoned before its final block (a future bug, or a test double
    that forgets to set it) must not enable sold detection just because it
    never got the chance to record a loss. Both loss lists stay empty here
    on purpose -- that is exactly the case a lost-models/lost-pages-only
    check would miss."""
    _seed_active_listing(db_session, "gone-1", brand="Fiat")

    def crawl_fn(client_factory, slug, make_id, year_from=None, concurrency=1,
                 session_refresh_requests=30, report=None):
        # Deliberately never sets report.finished = True, unlike every other
        # crawl_fn stub in this file -- simulates a crawl generator abandoned
        # before crawl_brand's final block.
        return iter([_snippet("still-here-1")])

    calls: list[str] = []
    gone_url = "https://www.autoscout24.it/annunci/gone-1"

    def fetch_detail_fn(client, url):
        calls.append(url)
        return DetailResult(sold=False, data=_full_detail_data())

    run = run_brand_sweep(
        db_session, _client, BRAND, crawl_fn=crawl_fn,
        fetch_detail_fn=fetch_detail_fn, concurrency=1,
    )

    assert run.status == "partial"
    assert run.sold_detected == 0
    assert db_session.get(Listing, "gone-1").status == "active"
    assert calls, "the backlog enrichment pass must still have run"
    assert gone_url not in calls, "sold detection must not run when the crawl never finished"


def test_a_small_page_gap_still_runs_sold_detection_and_the_run_is_success(db_session):
    """A gap under the threshold costs some wasted checks, not a whole cycle
    of sale data."""
    _seed_active_listing(db_session, "gone-1", brand="Fiat")

    def crawl_fn(client_factory, slug, make_id, year_from=None, concurrency=1,
                 session_refresh_requests=30, report=None):
        if report is not None:
            report.lost_pages.append((1, None, None, 5))
            report.finished = True
        return iter([_snippet(f"seen-{i}") for i in range(500)])

    calls: list[str] = []
    gone_url = "https://www.autoscout24.it/annunci/gone-1"

    def fetch_detail_fn(client, url):
        if url == gone_url:
            calls.append(url)
            return DetailResult(sold=True)
        # One of the 500 freshly-crawled listings hitting the same-sweep
        # detail-enrichment backlog -- unrelated to sold detection, so it
        # must not count toward the check/confirmation tally below.
        return DetailResult(sold=False, data=_full_detail_data())

    run = run_brand_sweep(
        db_session, _client, BRAND, crawl_fn=crawl_fn,
        fetch_detail_fn=fetch_detail_fn, concurrency=1,
    )

    assert run.status == "success"
    assert run.sold_detected == 1
    assert db_session.get(Listing, "gone-1").status == "sold"
    assert len(calls) == 2, "one check plus one confirmation"

    messages = [e.message for e in db_session.query(ScrapeEvent).all()]
    assert any("buco stimato" in m.lower() for m in messages), (
        "a success run with a sub-threshold gap must still record that gap"
    )


def test_a_large_page_gap_suppresses_sold_detection(db_session):
    _seed_active_listing(db_session, "gone-1", brand="Fiat")

    def crawl_fn(client_factory, slug, make_id, year_from=None, concurrency=1,
                 session_refresh_requests=30, report=None):
        if report is not None:
            report.lost_pages.extend((1, None, None, p) for p in range(2, 40))
            report.finished = True
        return iter([_snippet(f"seen-{i}") for i in range(100)])

    calls: list[str] = []
    gone_url = "https://www.autoscout24.it/annunci/gone-1"

    def fetch_detail_fn(client, url):
        # Same reasoning as the lost-model test above: record and check the
        # URL, don't rely on an exception that run_worker_pool would isolate
        # and discard unread.
        calls.append(url)
        return DetailResult(sold=False, data=_full_detail_data())

    run = run_brand_sweep(
        db_session, _client, BRAND, crawl_fn=crawl_fn,
        fetch_detail_fn=fetch_detail_fn, concurrency=1,
    )

    assert run.status == "partial"
    assert db_session.get(Listing, "gone-1").status == "active"
    assert calls, "the backlog enrichment pass must still have run"
    assert gone_url not in calls, "sold detection must not run over the threshold"


def test_a_partial_run_counts_backlog_removed_reports_as_errors(db_session):
    """The gate branch still runs the detail-enrichment backlog (binding
    constraint), and a backlog row reporting removal there is the same class
    of anomaly as the equivalent counter on the success path -- it must not
    vanish into a run that otherwise looks clean just because the run took
    the partial branch."""
    def crawl_fn(client_factory, slug, make_id, year_from=None, concurrency=1,
                 session_refresh_requests=30, report=None):
        if report is not None:
            report.lost_models.append(("modello-perso",))
            report.finished = True
        return iter([_snippet("new-1")])

    def fetch_detail_fn(client, url):
        return DetailResult(sold=True)  # backlog reads this as "reported removed"

    run = run_brand_sweep(
        db_session, _client, BRAND, crawl_fn=crawl_fn,
        fetch_detail_fn=fetch_detail_fn, concurrency=1,
    )

    assert run.status == "partial"
    assert run.errors_count >= 1, "a backlog removal report must count as an anomaly on a partial run too"


def test_a_partial_run_still_keeps_the_listings_it_collected(db_session):
    """Losing coverage must not lose data: the crawl's own work is committed."""
    def crawl_fn(client_factory, slug, make_id, year_from=None, concurrency=1,
                 session_refresh_requests=30, report=None):
        if report is not None:
            report.lost_models.append(("modello-perso",))
            report.finished = True
        return iter([_snippet("new-1"), _snippet("new-2")])

    run = run_brand_sweep(
        db_session, _client, BRAND, crawl_fn=crawl_fn,
        fetch_detail_fn=lambda c, u: DetailResult(sold=False, data=_full_detail_data()), concurrency=1,
    )

    assert run.status == "partial"
    assert db_session.get(Listing, "new-1") is not None
    assert db_session.get(Listing, "new-2") is not None


def test_a_partial_run_records_why(db_session):
    def crawl_fn(client_factory, slug, make_id, year_from=None, concurrency=1,
                 session_refresh_requests=30, report=None):
        if report is not None:
            report.lost_models.append(("modello-perso",))
            report.finished = True
        return iter([])

    run = run_brand_sweep(
        db_session, _client, BRAND, crawl_fn=crawl_fn,
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
            report.finished = True
        return iter([_snippet(f"seen-{i}") for i in range(1000)])

    def fetch_detail_fn(client, url):
        # Serves both the missing-listing pass (which only reads "brand") and
        # the same-sweep backlog enriching the 1000 freshly-crawled listings
        # (which needs the full shape), so the payload has to satisfy both.
        return DetailResult(sold=False, data=_full_detail_data(brand="Fiat"))

    run = run_brand_sweep(
        db_session, _client, BRAND, crawl_fn=crawl_fn,
        fetch_detail_fn=fetch_detail_fn, concurrency=1,
    )

    events = db_session.query(ScrapeEvent).all()
    per_listing = [
        e for e in events
        if "non trovato nella scansione" in e.message.lower()
        or "not found in sweep" in e.message.lower()
    ]
    assert per_listing == [], f"expected no per-listing events, got {len(per_listing)}"

    summaries = [e for e in events if "annunci non trovati nella scansione" in e.message.lower()]
    # Exactly one, not "at most one": an assertion of "<= 1" is satisfied by
    # deleting the summary entirely, which would leave the missing-but-alive
    # gap completely unrecorded.
    assert len(summaries) == 1, f"expected exactly one summary, got {len(summaries)}"
    assert "40" in summaries[0].message, "the summary must record how many listings were affected"
    # Not just the events: a healthy run with a declared, sub-threshold gap
    # must not show "Errori: 40" on the dashboard either -- these 40 are the
    # expected consequence of the declared gap, not a real anomaly, and the
    # summary event above already carries the number.
    assert run.errors_count == 0, "a declared sub-threshold gap must not inflate errors_count"


def test_a_candidate_whose_confirmation_times_out_stays_active(db_session):
    """The heart of the principle: absence of proof is not proof of absence.

    This is asserted explicitly rather than assumed from the fact that the
    29/07 code happened to behave this way -- an assumption is what a later
    refactor breaks silently, and the failure mode is inventing sales.
    """
    _seed_active_listing(db_session, "gone-1", brand="Fiat")
    gone_url = "https://www.autoscout24.it/annunci/gone-1"
    calls: list[str] = []

    def crawl_fn(client_factory, slug, make_id, year_from=None, concurrency=1,
                 session_refresh_requests=30, report=None):
        if report is not None:
            report.finished = True
        return iter([_snippet(f"seen-{i}") for i in range(10)])

    def fetch_detail_fn(client, url):
        if url != gone_url:
            # Same-sweep backlog enriching the 10 freshly-crawled listings --
            # unrelated to gone-1's sold-detection check/confirmation.
            return DetailResult(sold=False, data=_full_detail_data())
        calls.append(url)
        if len(calls) == 1:
            return DetailResult(sold=True)   # first check: looks removed
        raise TimeoutError("timed out")      # confirmation: we could not ask

    run = run_brand_sweep(
        db_session, _client, BRAND, crawl_fn=crawl_fn,
        fetch_detail_fn=fetch_detail_fn, concurrency=1,
    )

    assert db_session.get(Listing, "gone-1").status == "active"
    assert db_session.get(Listing, "gone-1").sold_at is None
    assert run.sold_detected == 0
    # Negative assertions alone would pass identically if the candidate were
    # never collected or the confirmation pass never ran at all. This proves
    # the confirmation was actually attempted and its failure is what left
    # the listing active, not the absence of an attempt.
    assert calls.count(gone_url) == 2, "first check plus a confirmation attempt"


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
            if report is not None:
                report.finished = True
            return iter([_snippet("seen-1")])

        def fetch_detail_fn(client, url, _at=phase_at_call):
            calls["n"] += 1
            if calls["n"] >= _at:
                raise BlockedError(403, url)
            return DetailResult(sold=True)

        run = run_brand_sweep(
            db_session, _client, BRAND, crawl_fn=crawl_fn,
            fetch_detail_fn=fetch_detail_fn, concurrency=1,
        )

        assert run.status == "blocked", f"a block at call {phase_at_call} must stop the sweep"
        assert db_session.get(Listing, f"gone-{phase_at_call}").status == "active"
