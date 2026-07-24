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
