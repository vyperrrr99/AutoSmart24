from __future__ import annotations

import datetime as dt
import itertools
from typing import Callable, Iterable, Iterator

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from autosmart24.config import BrandConfig
from autosmart24.db.dealers import upsert_dealer
from autosmart24.db.models import Dealer, Listing, PriceHistory, ScrapeEvent, ScrapeRun
from autosmart24.scraping.change_detection import diff_sweep
from autosmart24.scraping.concurrency import run_worker_pool
from autosmart24.scraping.coverage import assess_coverage
from autosmart24.scraping.crawler import CrawlReport, crawl_brand
from autosmart24.scraping.detail_queue import fetch_detail
from autosmart24.scraping.http_client import BlockedError, RateLimitedClient
from autosmart24.scraping.sold_confirmation import looks_removed

DETAIL_DB_PAGE_SIZE = 50
NEW_LISTING_COMMIT_BATCH_SIZE = 100


def _now() -> dt.datetime:
    return dt.datetime.utcnow()


def _log_event(session: Session, run: ScrapeRun, level: str, message: str, url: str | None = None) -> None:
    session.add(
        ScrapeEvent(run_id=run.id, brand=run.brand, level=level, message=message, url=url, created_at=_now())
    )


def process_detail_backlog(
    session: Session,
    client_factory: Callable[[], RateLimitedClient],
    brand: BrandConfig,
    run: ScrapeRun,
    concurrency: int = 1,
    session_refresh_requests: int = 30,
    db_page_size: int = DETAIL_DB_PAGE_SIZE,
    fetch_detail_fn=fetch_detail,
    exclude_ids: set[str] = frozenset(),
    year_from: int | None = None,
) -> int:
    total_reported = 0
    # Rows the pool did not report on are parked here for the rest of this call,
    # so the LIMIT-ed query can never re-select the same unprocessable row
    # forever. Without this, one permanently-failing detail page becomes an
    # infinite loop hammering the site.
    failed_ids: set[str] = set()

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

    while True:
        stmt = select(Listing).where(
            Listing.brand == brand.display_name,
            Listing.status == "active",
            Listing.detail_scraped.is_(False),
            Listing.id.notin_(set(exclude_ids) | failed_ids),
        )
        if year_from is not None:
            stmt = stmt.where(
                or_(
                    Listing.first_registration.is_(None),
                    Listing.first_registration >= dt.date(year_from, 1, 1),
                )
            )
        pending = session.execute(
            stmt.order_by(Listing.first_seen_at.asc()).limit(db_page_size)
        ).scalars().all()

        if not pending:
            return total_reported

        rows_by_id = {row.id: row for row in pending}
        jobs = [(row.id, row.url) for row in pending]
        enriched = 0
        reported_removed = 0
        handled: set[str] = set()
        # Rows left active (removal reported) also get parked below: they are
        # still detail_scraped=False and active, so without this the paging
        # query would re-select them forever within this same call.
        reported_removed_ids: set[str] = set()
        now = _now()

        def _detail_worker(job: tuple[str, str], client: RateLimitedClient) -> list[tuple[str, object]]:
            listing_id, url = job
            return [(listing_id, fetch_detail_fn(client, url))]

        try:
            for listing_id, result in run_worker_pool(
                jobs, _detail_worker, client_factory, concurrency, session_refresh_requests
            ):
                handled.add(listing_id)
                row = rows_by_id[listing_id]
                row.last_checked_at = now
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
                    reported_removed_ids.add(listing_id)
                    _log_event(
                        session, run, "warning",
                        f"Detail page reported removed for {row.id}, seen alive in this sweep: left active",
                        url=row.url,
                    )
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
                row.detail_scraped = True
                enriched += 1
        except BlockedError as exc:
            run.status = "blocked"
            run.errors_count += 1
            _log_event(session, run, "blocked", str(exc), url=exc.url)
            # Fall through to the info event below before returning: on a block
            # it is exactly the "how far did we get" line an operator needs, and
            # the dashboard is this project's only monitoring channel.
            run.detail_enriched = (run.detail_enriched or 0) + enriched
            _log_event(
                session, run, "info",
                f"Detail backlog page: enriched {enriched}, reported removed {reported_removed} (page size {len(pending)})",
            )
            session.commit()
            return total_reported + reported_removed

        run.detail_enriched = (run.detail_enriched or 0) + enriched
        _log_event(
            session, run, "info",
            f"Detail backlog page: enriched {enriched}, reported removed {reported_removed} (page size {len(pending)})",
        )
        session.commit()
        total_reported += reported_removed
        failed_ids |= (set(rows_by_id) - handled) | reported_removed_ids


def _iter_batches(iterable: Iterable[dict], batch_size: int) -> Iterator[list[dict]]:
    it = iter(iterable)
    while True:
        batch = list(itertools.islice(it, batch_size))
        if not batch:
            return
        yield batch


def run_brand_sweep(
    session: Session,
    client_factory: Callable[[], RateLimitedClient],
    brand: BrandConfig,
    crawl_fn=crawl_brand,
    fetch_detail_fn=fetch_detail,
    batch_size: int = NEW_LISTING_COMMIT_BATCH_SIZE,
    concurrency: int = 1,
    year_from: int | None = None,
    session_refresh_requests: int = 30,
) -> ScrapeRun:
    run = ScrapeRun(brand=brand.display_name, started_at=_now(), status="running", phase="search")
    session.add(run)
    session.commit()

    seen_ids: set[str] = set()
    new_ids: set[str] = set()
    relisted_ids: set[str] = set()
    listings_seen = 0
    price_changes = 0

    try:
        active_stmt = select(Listing).where(
            Listing.brand == brand.display_name, Listing.status == "active"
        )
        if year_from is not None:
            # Listings registered before the floor no longer appear in our
            # searches, so they must not be mistaken for "missing" (which would
            # trigger a pointless sold-confirmation fetch on every run, forever).
            active_stmt = active_stmt.where(
                or_(
                    Listing.first_registration.is_(None),
                    Listing.first_registration >= dt.date(year_from, 1, 1),
                )
            )
        active_rows = session.execute(active_stmt).scalars().all()
        active_db_prices = {row.id: row.price for row in active_rows}
        active_rows_by_id = {row.id: row for row in active_rows}
        # id -> brand, across ALL brands rather than the one being swept.
        # AutoScout24 reassigns the id of a withdrawn ad to an unrelated car,
        # and that car can belong to a different brand: scoped to one brand the
        # lookup missed the collision, the code took the INSERT path, and the
        # primary key violation killed the whole sweep. See
        # docs/superpowers/specs/2026-07-28-listing-id-reuse-known-issue.md
        existing_brand_by_id: dict[str, str] = dict(
            session.execute(select(Listing.id, Listing.brand)).all()
        )

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
                batch_snippets = {s["id"]: s for s in batch}
                batch_prices = {listing_id: s["price"] for listing_id, s in batch_snippets.items()}
                diff = diff_sweep(batch_prices, active_db_prices)
                now = _now()

                for listing_id in diff.new_ids:
                    if listing_id in new_ids or listing_id in relisted_ids:
                        # Same listing seen again in a later batch of this same
                        # sweep (shouldn't normally happen given how crawl_fn
                        # partitions queries, but guard against a duplicate
                        # insert/PriceHistory row rather than crashing).
                        continue
                    snippet = batch_snippets[listing_id]
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
                        # Reappeared under a status other than "active" (most commonly
                        # "sold" -- either a genuine temporary delisting/relist, or a
                        # prior false-positive sold confirmation). diff_sweep's
                        # active_db_prices only covers status="active" rows, so this
                        # listing was invisible to it and landed in new_ids -- but its
                        # primary key already exists, so this must be an UPDATE, not
                        # an INSERT. Not counted as a new listing.
                        row = session.get(Listing, listing_id)
                        if snippet["price"] is not None and snippet["price"] != row.price:
                            row.price = snippet["price"]
                            session.add(PriceHistory(listing_id=listing_id, price=snippet["price"], recorded_at=now))
                        row.status = "active"
                        row.sold_at = None
                        row.cross_reference_id = snippet["cross_reference_id"]
                        row.brand = snippet["brand"] or brand.display_name
                        row.model = snippet["model"]
                        row.model_group = snippet["model_group"]
                        row.variant = snippet["variant"]
                        row.motor_type_name = snippet["motor_type_name"]
                        row.version_input = snippet["version_input"]
                        row.transmission = snippet["transmission"]
                        row.fuel = snippet["fuel"]
                        row.first_registration = snippet["first_registration"]
                        row.mileage_km = snippet["mileage_km"]
                        row.seller_type = snippet["seller_type"]
                        row.seller_company_name = snippet["seller_company_name"]
                        row.city = snippet["city"]
                        row.zip_code = snippet["zip_code"]
                        row.url = snippet["url"]
                        row.last_seen_at = now
                        row.last_checked_at = now
                        relisted_ids.add(listing_id)
                        continue
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
        except BlockedError as exc:
            run.status = "blocked"
            run.phase = None
            run.finished_at = _now()
            _log_event(session, run, "blocked", str(exc), url=exc.url)
            session.commit()
            return run

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
            backlog_removed_reports = process_detail_backlog(
                session, client_factory, brand, run,
                concurrency=concurrency, session_refresh_requests=session_refresh_requests,
                fetch_detail_fn=fetch_detail_fn, year_from=year_from,
            )
            # Same anomaly class as the success path's equivalent counter
            # below: a backlog row reporting removal here is diagnostic, not
            # a sale, but it must still surface as an error count rather than
            # vanish into a run that otherwise looks clean.
            run.errors_count += backlog_removed_reports
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

        if coverage.estimated_missing:
            # This run closes "success" below and will run sold detection --
            # but it still had a real gap, just one under the threshold. That
            # must leave a trace independent of whether any particular
            # listing ends up missing-but-alive (it may be zero, or every
            # missing listing may turn out genuinely removed): "success" must
            # not read as "the search phase was complete".
            _log_event(
                session, run, "info",
                f"Scansione con copertura parziale ma sotto soglia: {coverage.reason}",
            )

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
                    # estimated_missing == 0 means the coverage gap is known
                    # to be zero, so a listing missing from the search yet
                    # alive on its own detail page has no explanation in a
                    # lost page or model -- it is logged individually because
                    # it genuinely is an anomaly. When the gap is nonzero this
                    # is the expected outcome for every listing the crawl
                    # missed, not an anomaly: one event each would read as a
                    # serious fault on the dashboard, this project's only
                    # monitoring channel -- the summary below covers it instead.
                    if coverage.estimated_missing == 0:
                        _log_event(
                            session, run, "warning",
                            f"Listing {listing_id} not found in sweep but still active on detail page",
                            url=row.url,
                        )
        except BlockedError as exc:
            run.status = "blocked"
            run.phase = None
            run.errors_count += 1
            _log_event(session, run, "blocked", str(exc), url=exc.url)

        # estimated_missing is None exactly when a model was lost -- but that
        # case already returned above via coverage.can_detect_sales, so this
        # line is unreachable with estimated_missing is None today. Guarded
        # with a truthiness check anyway rather than `> 0`, which would raise
        # TypeError on None: the coupling between this file and coverage.py
        # deciding that is invisible, and the failure mode of trusting it is an
        # exception inside the sold-detection path.
        if coverage.estimated_missing and missing_but_alive:
            _log_event(
                session, run, "warning",
                f"{missing_but_alive} annunci non trovati nella scansione ma ancora attivi "
                f"(atteso: {coverage.reason})",
            )

        backlog_removed_reports = 0
        if run.status != "blocked":
            run.phase = "detail"
            run.search_finished_at = _now()
            session.commit()
            # sold_candidates are still status='active' and detail_scraped=False
            # at this point -- the confirmation pass below hasn't run yet -- so
            # without this exclusion the backlog's own query would pick them
            # back up and detail-fetch them a spurious third time this sweep.
            # Worse, on an id-reuse redirect the backlog only checks
            # result.sold, not looks_removed's brand comparison, so it would
            # write a different car's fields onto this row before the
            # confirmation pass marks it sold at that foreign price.
            backlog_removed_reports = process_detail_backlog(
                session, client_factory, brand, run,
                concurrency=concurrency, session_refresh_requests=session_refresh_requests,
                fetch_detail_fn=fetch_detail_fn, year_from=year_from,
                exclude_ids=set(sold_candidates),
            )
            # A backlog row reporting removal is the same class of anomaly as
            # the missing-path one above (line ~401): unlike a genuine sale it
            # gets no other counter, so without this a repeat of the incident
            # would finish status="success", errors_count=0 -- invisible on
            # the dashboard, this project's only monitoring channel.
            run.errors_count += backlog_removed_reports

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
                        # looks_removed is a bool and does not say which of
                        # its two grounds fired; result.sold directly tells us
                        # the difference. An explicit removal (404/410, or a
                        # status other than Active) is site-reported fact. A
                        # brand mismatch is a deterministic *inference* that
                        # both confirmation checks are equally blind to: if a
                        # detail page's brand ever systematically diverges
                        # from the stored Listing.brand, every missing listing
                        # of that brand gets confirmed sold this way. Logging
                        # the ground separately means a drift shows up as a
                        # spike of brand-mismatch sales before it becomes 139
                        # rows.
                        ground = "explicit removal" if result.sold else "brand mismatch"
                        _log_event(
                            session, run, "info",
                            f"Listing {listing_id} confirmed sold ({ground})",
                            url=row.url,
                        )
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

        run.listings_seen = listings_seen
        run.new_listings = len(new_ids)
        run.price_changes = price_changes
        # Only the missing-listing path can declare a sale; the backlog's
        # removal reports are diagnostic and deliberately excluded.
        run.sold_detected = sold_count
        if run.status != "blocked":
            run.status = "success"
        run.phase = None
        run.finished_at = _now()

        session.commit()
        return run
    except Exception as exc:
        # Last-resort safety net for anything NOT already handled by the
        # BlockedError-specific catches above (e.g. the DataError from a
        # too-narrow column, or any other future bug). Without this, an
        # unexpected exception propagates straight out of the sweep,
        # APScheduler merely logs it, and the ScrapeRun row is left stuck
        # at status="running" forever -- a "zombie" run indistinguishable
        # on the dashboard from one that is still legitimately in
        # progress. Roll back the failed transaction first, record the
        # failure with whatever partial progress was durably committed,
        # then re-raise so the error still surfaces in application logs.
        session.rollback()
        run.status = "error"
        run.phase = None
        run.finished_at = _now()
        # Counters are not reassigned here: every batch already persisted them
        # in its own commit, so after the rollback the row holds exactly what
        # was durably written.
        run.errors_count += 1
        message = f"Unexpected error during sweep: {exc}"
        if len(message) > 2048:
            message = message[:2048]
        _log_event(session, run, "error", message)
        session.commit()
        raise
