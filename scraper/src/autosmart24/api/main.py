from __future__ import annotations

import datetime as dt
import os
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import Session

from autosmart24.api.progress import eta_seconds, percent, phase_progress, rates_from_history, run_metrics
from autosmart24.api.schemas import (
    AddBrandsRequest,
    ApplyDefaultsRequest,
    BrandCatalogEntryOut,
    BrandStatusOut,
    EventOut,
    QueueCurrentOut,
    QueueOut,
    QueuePendingOut,
    RunMetricsOut,
    RunOut,
    UpdateBrandRequest,
)
from autosmart24.config import BrandConfig
from autosmart24.db.models import BrandCatalog, ScrapeEvent, ScrapeRun, TrackedBrand
from autosmart24.queue_control import QueueController
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
    queue_controller: QueueController,
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
                # slug is deliberately NOT overwritten here: it is the
                # manual-correction safety net for a mis-derived slug (see
                # the design spec's slug-validation risk note), applied
                # directly against the database. Clobbering it on every
                # refresh would silently undo that correction.
                existing.display_name = entry.display_name
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
        # Validate ALL make_ids against the catalog before creating any rows
        # or touching the live scheduler. Otherwise a later invalid id in the
        # same request would abort the loop (and roll back the DB) after an
        # earlier brand's cron job was already registered, leaving an orphan
        # scheduler job for a brand the API reports as untracked.
        for make_id in body.make_ids:
            if session.get(BrandCatalog, make_id) is None:
                raise HTTPException(status_code=400, detail=f"Unknown make_id in catalog: {make_id}")

        touched: list[TrackedBrand] = []
        for make_id in body.make_ids:
            catalog_entry = session.get(BrandCatalog, make_id)
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

    # This route MUST stay declared before @app.patch("/brands/{brand_slug}"),
    # or FastAPI will match "apply-defaults" against the {brand_slug} path
    # parameter of that route instead of this one.
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
        scheduler.pause_brand(brand_slug)
        session.commit()
        return {"paused": True}

    @app.post("/brands/{brand_slug}/resume")
    def resume_brand(brand_slug: str, session: Session = Depends(get_session)):
        row = _find_tracked_brand(session, brand_slug)
        row.paused = False
        scheduler.resume_brand(brand_slug)
        session.commit()
        return {"paused": False}

    @app.post("/brands/{brand_slug}/run-now")
    def run_now(brand_slug: str, session: Session = Depends(get_session)):
        row = _find_tracked_brand(session, brand_slug)
        run_now_fn(_to_brand_config(row))
        return {"triggered": True}

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

    return app
