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
