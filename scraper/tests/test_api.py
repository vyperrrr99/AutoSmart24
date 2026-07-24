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
