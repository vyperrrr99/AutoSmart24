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
        session, client, brand, run, fetch_detail_fn=fetch_detail_fn, exclude_ids=diff.new_ids
    )

    run.listings_seen = len(current_snippets)
    run.new_listings = len(diff.new_ids)
    run.price_changes = len(diff.price_changed)
    run.sold_detected = sold_count + backlog_sold_count
    if run.status != "blocked":
        run.status = "success"
    run.finished_at = _now()

    session.commit()
    return run
