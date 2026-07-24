from __future__ import annotations

import datetime as dt
import itertools
from typing import Iterable, Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from autosmart24.config import BrandConfig
from autosmart24.db.models import Listing, PriceHistory, ScrapeEvent, ScrapeRun
from autosmart24.scraping.change_detection import diff_sweep
from autosmart24.scraping.crawler import crawl_brand
from autosmart24.scraping.detail_queue import fetch_detail
from autosmart24.scraping.http_client import BlockedError, RateLimitedClient

DETAIL_BATCH_SIZE = 50
NEW_LISTING_COMMIT_BATCH_SIZE = 100


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
    exclude_ids: set[str] = frozenset(),
) -> int:
    pending = session.execute(
        select(Listing)
        .where(
            Listing.brand == brand.display_name,
            Listing.status == "active",
            Listing.detail_scraped.is_(False),
            Listing.id.notin_(exclude_ids),
        )
        .order_by(Listing.first_seen_at.asc())
        .limit(batch_size)
    ).scalars().all()

    if not pending:
        return 0

    enriched = 0
    sold = 0
    now = _now()

    for row in pending:
        try:
            result = fetch_detail_fn(client, row.url)
        except BlockedError as exc:
            run.status = "blocked"
            run.errors_count += 1
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

    return sold


def _iter_batches(iterable: Iterable[dict], batch_size: int) -> Iterator[list[dict]]:
    it = iter(iterable)
    while True:
        batch = list(itertools.islice(it, batch_size))
        if not batch:
            return
        yield batch


def run_brand_sweep(
    session: Session,
    client: RateLimitedClient,
    brand: BrandConfig,
    crawl_fn=crawl_brand,
    fetch_detail_fn=fetch_detail,
    batch_size: int = NEW_LISTING_COMMIT_BATCH_SIZE,
) -> ScrapeRun:
    run = ScrapeRun(brand=brand.display_name, started_at=_now(), status="running")
    session.add(run)
    session.commit()

    seen_ids: set[str] = set()
    new_ids: set[str] = set()
    listings_seen = 0
    price_changes = 0

    try:
        active_rows = session.execute(
            select(Listing).where(Listing.brand == brand.display_name, Listing.status == "active")
        ).scalars().all()
        active_db_prices = {row.id: row.price for row in active_rows}
        active_rows_by_id = {row.id: row for row in active_rows}

        try:
            for batch in _iter_batches(crawl_fn(client, brand.slug, brand.make_id), batch_size):
                batch_snippets = {s["id"]: s for s in batch}
                batch_prices = {listing_id: s["price"] for listing_id, s in batch_snippets.items()}
                diff = diff_sweep(batch_prices, active_db_prices)
                now = _now()

                for listing_id in diff.new_ids:
                    if listing_id in new_ids:
                        # Same listing seen again in a later batch of this same
                        # sweep (shouldn't normally happen given how crawl_fn
                        # partitions queries, but guard against a duplicate
                        # insert/PriceHistory row rather than crashing).
                        continue
                    snippet = batch_snippets[listing_id]
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

                session.commit()

                # Only credit these counters once the batch is durably
                # committed, so that if a later batch's commit ever fails
                # with an unexpected error, the outer except Exception
                # handler below reports counts that match what is actually
                # persisted in the DB (not an in-memory count that includes
                # a batch whose commit never succeeded).
                seen_ids.update(batch_snippets.keys())
                new_ids.update(diff.new_ids)
                listings_seen += len(batch_snippets)
                price_changes += len(diff.price_changed)
        except BlockedError as exc:
            run.status = "blocked"
            run.listings_seen = listings_seen
            run.new_listings = len(new_ids)
            run.price_changes = price_changes
            run.finished_at = _now()
            _log_event(session, run, "blocked", str(exc), url=exc.url)
            session.commit()
            return run

        missing_ids = set(active_db_prices.keys()) - seen_ids

        sold_count = 0
        now = _now()
        for listing_id in missing_ids:
            row = active_rows_by_id[listing_id]
            try:
                result = fetch_detail_fn(client, row.url)
            except BlockedError as exc:
                run.status = "blocked"
                run.errors_count += 1
                _log_event(session, run, "blocked", str(exc), url=exc.url)
                break

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

        backlog_sold_count = process_detail_backlog(
            session, client, brand, run, fetch_detail_fn=fetch_detail_fn, exclude_ids=new_ids
        )

        run.listings_seen = listings_seen
        run.new_listings = len(new_ids)
        run.price_changes = price_changes
        run.sold_detected = sold_count + backlog_sold_count
        if run.status != "blocked":
            run.status = "success"
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
        run.finished_at = _now()
        run.listings_seen = listings_seen
        run.new_listings = len(new_ids)
        run.price_changes = price_changes
        run.errors_count += 1
        message = f"Unexpected error during sweep: {exc}"
        if len(message) > 2048:
            message = message[:2048]
        _log_event(session, run, "error", message)
        session.commit()
        raise
