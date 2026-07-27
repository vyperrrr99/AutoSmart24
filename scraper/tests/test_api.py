import datetime as dt

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi.testclient import TestClient

from autosmart24.api.main import create_app
from autosmart24.db.models import BrandCatalog, ScrapeRun, TrackedBrand
from autosmart24.queue_control import QueueController
from autosmart24.scheduler import BrandScheduler
from autosmart24.scraping.brand_catalog import CatalogEntry

FIAT_CATALOG = CatalogEntry(make_id=28, display_name="Fiat", slug="fiat")
BMW_CATALOG = CatalogEntry(make_id=13, display_name="BMW", slug="bmw")


def _seed_catalog(db_session, entries=(FIAT_CATALOG, BMW_CATALOG)):
    now = dt.datetime.utcnow()
    for entry in entries:
        db_session.add(BrandCatalog(make_id=entry.make_id, display_name=entry.display_name, slug=entry.slug, synced_at=now))
    db_session.commit()


def _seed_tracked(db_session, entry=FIAT_CATALOG, **overrides):
    defaults = dict(
        make_id=entry.make_id, slug=entry.slug, display_name=entry.display_name,
        paused=False, year_from_years=None, schedule_day_of_week=None,
        schedule_hour=3, schedule_minute=0, created_at=dt.datetime.utcnow(),
    )
    defaults.update(overrides)
    row = TrackedBrand(**defaults)
    db_session.add(row)
    db_session.commit()
    return row


def _app_with_session(db_session, run_now_fn=None, run_fn=None, refresh_catalog_fn=None):
    scheduler = BrandScheduler(BackgroundScheduler())
    for row in db_session.query(TrackedBrand).all():
        scheduler.schedule_brand(row, run_fn=lambda brand: None, hour=row.schedule_hour, minute=row.schedule_minute)

    app = create_app(
        session_factory=lambda: db_session,
        scheduler=scheduler,
        run_now_fn=run_now_fn or (lambda brand: None),
        run_fn=run_fn or (lambda brand: None),
        refresh_catalog_fn=refresh_catalog_fn or (lambda: []),
        queue_controller=QueueController(),
    )
    return app, scheduler


def test_list_brands_returns_all_tracked_brands_with_slug(db_session):
    _seed_catalog(db_session)
    _seed_tracked(db_session, FIAT_CATALOG)
    _seed_tracked(db_session, BMW_CATALOG)

    app, _ = _app_with_session(db_session)
    client = TestClient(app)

    response = client.get("/brands")

    assert response.status_code == 200
    slugs = {row["slug"] for row in response.json()}
    assert slugs == {"fiat", "bmw"}


def test_list_brands_reports_last_run(db_session):
    _seed_catalog(db_session)
    _seed_tracked(db_session, FIAT_CATALOG)
    now = dt.datetime.utcnow()
    db_session.add(ScrapeRun(brand="Fiat", started_at=now, finished_at=now, status="success"))
    db_session.commit()

    app, _ = _app_with_session(db_session)
    client = TestClient(app)

    response = client.get("/brands")
    fiat_row = next(row for row in response.json() if row["slug"] == "fiat")
    assert fiat_row["last_run"]["status"] == "success"


def test_list_brands_includes_year_and_schedule(db_session):
    _seed_catalog(db_session)
    _seed_tracked(db_session, FIAT_CATALOG, year_from_years=5, schedule_day_of_week="mon", schedule_hour=4, schedule_minute=30)

    app, _ = _app_with_session(db_session)
    client = TestClient(app)

    fiat_row = next(row for row in client.get("/brands").json() if row["slug"] == "fiat")
    assert fiat_row["year_from_years"] == 5
    assert fiat_row["schedule_day_of_week"] == "mon"
    assert fiat_row["schedule_hour"] == 4
    assert fiat_row["schedule_minute"] == 30


def test_get_brand_catalog_returns_seeded_entries(db_session):
    _seed_catalog(db_session)

    app, _ = _app_with_session(db_session)
    client = TestClient(app)

    response = client.get("/brand-catalog")

    assert response.status_code == 200
    slugs = {row["slug"] for row in response.json()}
    assert slugs == {"fiat", "bmw"}


def test_refresh_brand_catalog_upserts_entries(db_session):
    called = []

    def fake_refresh():
        called.append(1)
        return [FIAT_CATALOG, BMW_CATALOG]

    app, _ = _app_with_session(db_session, refresh_catalog_fn=fake_refresh)
    client = TestClient(app)

    response = client.post("/brand-catalog/refresh")

    assert response.status_code == 200
    assert response.json() == {"count": 2}
    assert called == [1]
    assert {row["slug"] for row in client.get("/brand-catalog").json()} == {"fiat", "bmw"}


def test_refresh_brand_catalog_preserves_a_manually_corrected_slug(db_session):
    _seed_catalog(db_session, entries=(FIAT_CATALOG,))
    manually_corrected = db_session.get(BrandCatalog, FIAT_CATALOG.make_id)
    manually_corrected.slug = "fiat-corrected"
    db_session.commit()

    def fake_refresh():
        return [FIAT_CATALOG]  # re-derives "fiat", which must NOT clobber the correction

    app, _ = _app_with_session(db_session, refresh_catalog_fn=fake_refresh)
    client = TestClient(app)

    client.post("/brand-catalog/refresh")

    row = db_session.get(BrandCatalog, FIAT_CATALOG.make_id)
    assert row.slug == "fiat-corrected"
    assert row.display_name == "Fiat"  # display_name still refreshes normally


def test_add_brands_bulk_creates_tracked_rows_and_schedules_jobs(db_session):
    _seed_catalog(db_session)

    app, scheduler = _app_with_session(db_session)
    client = TestClient(app)

    response = client.post(
        "/brands/bulk",
        json={"make_ids": [28, 13], "year_from_years": 5, "schedule_hour": 4, "schedule_minute": 15},
    )

    assert response.status_code == 200
    body = response.json()
    assert {row["slug"] for row in body} == {"fiat", "bmw"}
    assert all(row["year_from_years"] == 5 for row in body)
    assert scheduler.scheduler.get_job("fiat") is not None
    assert scheduler.scheduler.get_job("bmw") is not None


def test_add_brands_bulk_rejects_unknown_make_id(db_session):
    _seed_catalog(db_session)

    app, _ = _app_with_session(db_session)
    client = TestClient(app)

    response = client.post("/brands/bulk", json={"make_ids": [999999]})

    assert response.status_code == 400


def test_add_brands_bulk_mixed_valid_and_unknown_make_id_leaves_no_orphan_job(db_session):
    _seed_catalog(db_session)

    app, scheduler = _app_with_session(db_session)
    client = TestClient(app)

    response = client.post("/brands/bulk", json={"make_ids": [28, 999999]})

    assert response.status_code == 400
    assert db_session.query(TrackedBrand).count() == 0
    assert scheduler.scheduler.get_job("fiat") is None


def test_update_brand_patches_only_provided_fields(db_session):
    _seed_catalog(db_session)
    _seed_tracked(db_session, FIAT_CATALOG, year_from_years=5, schedule_hour=3, schedule_minute=0)

    app, _ = _app_with_session(db_session)
    client = TestClient(app)

    response = client.patch("/brands/fiat", json={"schedule_hour": 7})

    assert response.status_code == 200
    body = response.json()
    assert body["schedule_hour"] == 7
    assert body["year_from_years"] == 5  # untouched, since it was not in the request body


def test_update_brand_can_explicitly_clear_year_filter(db_session):
    _seed_catalog(db_session)
    _seed_tracked(db_session, FIAT_CATALOG, year_from_years=5)

    app, _ = _app_with_session(db_session)
    client = TestClient(app)

    response = client.patch("/brands/fiat", json={"year_from_years": None})

    assert response.status_code == 200
    assert response.json()["year_from_years"] is None


def test_apply_defaults_overwrites_all_tracked_brands(db_session):
    _seed_catalog(db_session)
    _seed_tracked(db_session, FIAT_CATALOG, year_from_years=5)
    _seed_tracked(db_session, BMW_CATALOG, year_from_years=10)

    app, _ = _app_with_session(db_session)
    client = TestClient(app)

    response = client.patch("/brands/apply-defaults", json={"year_from_years": 3, "schedule_hour": 2, "schedule_minute": 0})

    assert response.status_code == 200
    body = response.json()
    assert all(row["year_from_years"] == 3 for row in body)
    assert all(row["schedule_hour"] == 2 for row in body)


def test_apply_defaults_leaves_omitted_field_untouched(db_session):
    _seed_catalog(db_session)
    _seed_tracked(db_session, FIAT_CATALOG, schedule_day_of_week="mon")

    app, _ = _app_with_session(db_session)
    client = TestClient(app)

    # schedule_day_of_week is deliberately omitted from the body.
    response = client.patch(
        "/brands/apply-defaults", json={"year_from_years": 3, "schedule_hour": 2, "schedule_minute": 0}
    )

    assert response.status_code == 200
    body = response.json()
    fiat_row = next(row for row in body if row["slug"] == "fiat")
    assert fiat_row["schedule_day_of_week"] == "mon"


def test_delete_brand_removes_row_and_job(db_session):
    _seed_catalog(db_session)
    _seed_tracked(db_session, FIAT_CATALOG)

    app, scheduler = _app_with_session(db_session)
    client = TestClient(app)

    response = client.delete("/brands/fiat")

    assert response.status_code == 200
    assert client.get("/brands/fiat/runs").status_code == 404
    assert scheduler.scheduler.get_job("fiat") is None


def test_pause_and_resume_brand_via_api(db_session):
    _seed_catalog(db_session)
    _seed_tracked(db_session, FIAT_CATALOG)

    app, scheduler = _app_with_session(db_session)
    client = TestClient(app)

    response = client.post("/brands/fiat/pause")
    assert response.status_code == 200
    assert scheduler.is_paused("fiat") is True

    response = client.post("/brands/fiat/resume")
    assert response.status_code == 200
    assert scheduler.is_paused("fiat") is False


def test_pause_tracked_brand_without_scheduler_job_still_persists_paused(db_session):
    _seed_catalog(db_session)
    _seed_tracked(db_session, FIAT_CATALOG)

    app, scheduler = _app_with_session(db_session)
    client = TestClient(app)
    # Simulate a tracked brand with no live scheduler job (e.g. never
    # (re)scheduled after creation, or removed out-of-band).
    scheduler.scheduler.remove_job("fiat")
    assert scheduler.scheduler.get_job("fiat") is None

    response = client.post("/brands/fiat/pause")

    assert response.status_code == 200
    row = db_session.get(TrackedBrand, FIAT_CATALOG.make_id)
    assert row.paused is True


def test_pause_persists_to_the_tracked_brand_row(db_session):
    _seed_catalog(db_session)
    _seed_tracked(db_session, FIAT_CATALOG)

    app, _ = _app_with_session(db_session)
    client = TestClient(app)

    client.post("/brands/fiat/pause")

    row = db_session.get(TrackedBrand, 28)
    assert row.paused is True


def test_list_brands_reports_paused_true_after_pause(db_session):
    _seed_catalog(db_session)
    _seed_tracked(db_session, FIAT_CATALOG)

    app, _ = _app_with_session(db_session)
    client = TestClient(app)

    client.post("/brands/fiat/pause")
    fiat_row = next(row for row in client.get("/brands").json() if row["slug"] == "fiat")

    assert fiat_row["paused"] is True


def test_patching_schedule_of_a_paused_brand_leaves_it_paused(db_session):
    _seed_catalog(db_session)
    _seed_tracked(db_session, FIAT_CATALOG)

    app, scheduler = _app_with_session(db_session)
    # Must run against a *started* scheduler: add_job(replace_existing=True)
    # only actually replaces the existing job once queued pending jobs are
    # flushed to the jobstore on start(); against a non-started scheduler the
    # stale (still-paused) job object would be returned regardless of
    # whether _reschedule's re-pause branch runs, silently testing nothing.
    scheduler.scheduler.start(paused=True)
    try:
        client = TestClient(app)

        client.post("/brands/fiat/pause")
        response = client.patch("/brands/fiat", json={"schedule_hour": 7})

        assert response.status_code == 200
        assert scheduler.is_paused("fiat") is True
        assert response.json()["paused"] is True
    finally:
        scheduler.shutdown()


def test_patch_brand_with_invalid_schedule_hour_returns_422(db_session):
    _seed_catalog(db_session)
    _seed_tracked(db_session, BMW_CATALOG)

    app, _ = _app_with_session(db_session)
    client = TestClient(app)

    response = client.patch("/brands/bmw", json={"schedule_hour": 99})

    assert response.status_code == 422


def test_run_now_triggers_callback(db_session):
    _seed_catalog(db_session)
    _seed_tracked(db_session, FIAT_CATALOG)
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


def test_cors_header_present_for_allowed_origin(db_session):
    app, _ = _app_with_session(db_session)
    client = TestClient(app)

    response = client.get("/brands", headers={"Origin": "http://localhost:5173"})

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"
