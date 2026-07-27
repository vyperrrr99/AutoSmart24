import datetime as dt

from autosmart24.api.progress import (
    FALLBACK_DETAIL_RATE_PER_MIN,
    eta_seconds,
    percent,
    phase_progress,
    rates_from_history,
    run_metrics,
)
from autosmart24.db.models import ScrapeRun


def _run(**kw) -> ScrapeRun:
    base = dict(
        brand="Fiat", started_at=dt.datetime(2026, 7, 27, 3, 0, 0), status="running",
        listings_seen=0, new_listings=0, price_changes=0, sold_detected=0, errors_count=0,
        phase=None, search_finished_at=None, search_total=None, detail_total=None, detail_enriched=0,
    )
    base.update(kw)
    return ScrapeRun(**base)


def test_phase_progress_uses_listings_seen_during_search():
    run = _run(phase="search", listings_seen=1200, search_total=7000)

    assert phase_progress(run) == (1200, 7000)


def test_phase_progress_uses_enriched_during_detail():
    run = _run(phase="detail", detail_enriched=340, detail_total=6800)

    assert phase_progress(run) == (340, 6800)


def test_percent_is_none_when_the_total_is_unknown():
    assert percent(120, None) is None


def test_percent_rounds_to_one_decimal():
    assert percent(1449, 6800) == 21.3


def test_percent_never_exceeds_one_hundred():
    """failed_ids can leave the denominator unreached, but a rerun of the
    same page must never push the bar past full."""
    assert percent(7000, 6800) == 100.0


def test_rates_fall_back_when_there_is_no_history():
    search, detail, is_fallback = rates_from_history([])

    assert is_fallback is True
    assert detail == FALLBACK_DETAIL_RATE_PER_MIN


def test_rates_are_derived_from_finished_runs():
    finished = _run(
        status="success",
        started_at=dt.datetime(2026, 7, 27, 3, 0, 0),
        search_finished_at=dt.datetime(2026, 7, 27, 3, 8, 0),   # 480s
        finished_at=dt.datetime(2026, 7, 27, 5, 0, 0),          # 6720s di dettaglio
        listings_seen=7200, detail_enriched=6720,
    )

    search, detail, is_fallback = rates_from_history([finished])

    assert is_fallback is False
    assert round(search) == 900   # 7200 annunci / 8 min
    assert round(detail) == 60    # 6720 annunci / 112 min


def test_eta_uses_the_remaining_items_of_the_current_phase():
    run = _run(phase="detail", detail_enriched=1000, detail_total=4000)

    # 3000 rimanenti a 60/min = 3000 secondi
    assert eta_seconds(run, search_rate=900.0, detail_rate=60.0) == 3000


def test_eta_is_none_without_a_total():
    run = _run(phase="detail", detail_enriched=1000, detail_total=None)

    assert eta_seconds(run, search_rate=900.0, detail_rate=60.0) is None


def test_run_metrics_reports_both_phases():
    finished = _run(
        status="success",
        started_at=dt.datetime(2026, 7, 27, 3, 0, 0),
        search_finished_at=dt.datetime(2026, 7, 27, 3, 8, 0),
        finished_at=dt.datetime(2026, 7, 27, 5, 0, 0),
        listings_seen=7200, detail_enriched=6720,
    )
    finished.id = 42

    metrics = run_metrics(finished)

    assert metrics["run_id"] == 42
    assert metrics["search_seconds"] == 480
    assert metrics["search_items"] == 7200
    assert round(metrics["search_rate_per_min"]) == 900
    assert metrics["detail_seconds"] == 6720
    assert round(metrics["detail_rate_per_min"]) == 60


def test_run_metrics_is_none_for_a_run_still_going():
    assert run_metrics(_run(phase="search")) is None
