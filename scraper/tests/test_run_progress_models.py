import datetime as dt

from autosmart24.db.models import ScrapeRun


def test_scrape_run_accepts_progress_fields(db_session):
    run = ScrapeRun(
        brand="Fiat",
        started_at=dt.datetime(2026, 7, 27, 3, 0, 0),
        status="running",
        phase="search",
        search_finished_at=None,
        search_total=15776,
        detail_total=None,
        detail_enriched=0,
    )
    db_session.add(run)
    db_session.commit()

    stored = db_session.query(ScrapeRun).one()
    assert stored.phase == "search"
    assert stored.search_total == 15776
    assert stored.detail_enriched == 0


def test_scrape_run_progress_fields_default_to_empty(db_session):
    run = ScrapeRun(brand="Fiat", started_at=dt.datetime(2026, 7, 27, 3, 0, 0), status="running")
    db_session.add(run)
    db_session.commit()

    stored = db_session.query(ScrapeRun).one()
    assert stored.phase is None
    assert stored.search_finished_at is None
    assert stored.search_total is None
    assert stored.detail_total is None
    assert stored.detail_enriched == 0
