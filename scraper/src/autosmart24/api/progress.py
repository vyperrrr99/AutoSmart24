from __future__ import annotations

from autosmart24.db.models import ScrapeRun

# Measured on 2026-07-27 (Citroën, concurrency 6): 7256 listings in 478s of
# search, 6995 in 7152s of detail. Used only until a brand has finished runs
# of its own; responses flag it so the UI can call the estimate approximate.
FALLBACK_SEARCH_RATE_PER_MIN = 911.0
FALLBACK_DETAIL_RATE_PER_MIN = 59.0

MIN_RATE_PER_MIN = 1.0


def phase_progress(run: ScrapeRun) -> tuple[int, int | None]:
    """(done, total) for whichever phase the run is currently in."""
    if run.phase == "detail":
        return (run.detail_enriched or 0), run.detail_total
    return (run.listings_seen or 0), run.search_total


def percent(done: int, total: int | None) -> float | None:
    if not total or total <= 0:
        return None
    return min(100.0, round(done * 100.0 / total, 1))


def _seconds(start, end) -> int | None:
    if start is None or end is None:
        return None
    return int((end - start).total_seconds())


def _rate(items: int | None, seconds: int | None) -> float | None:
    if not items or not seconds or seconds <= 0:
        return None
    return items * 60.0 / seconds


def rates_from_history(runs: list[ScrapeRun]) -> tuple[float, float, bool]:
    """Average search/detail throughput over finished runs.

    Returns the fallback constants (and is_fallback=True) when no finished
    run carries usable timings, so a brand's first run still gets an ETA.
    """
    search_rates: list[float] = []
    detail_rates: list[float] = []
    for run in runs:
        search = _rate(run.listings_seen, _seconds(run.started_at, run.search_finished_at))
        detail = _rate(run.detail_enriched, _seconds(run.search_finished_at, run.finished_at))
        if search:
            search_rates.append(search)
        if detail:
            detail_rates.append(detail)

    if not search_rates and not detail_rates:
        return FALLBACK_SEARCH_RATE_PER_MIN, FALLBACK_DETAIL_RATE_PER_MIN, True

    search_avg = sum(search_rates) / len(search_rates) if search_rates else FALLBACK_SEARCH_RATE_PER_MIN
    detail_avg = sum(detail_rates) / len(detail_rates) if detail_rates else FALLBACK_DETAIL_RATE_PER_MIN
    return max(search_avg, MIN_RATE_PER_MIN), max(detail_avg, MIN_RATE_PER_MIN), False


def eta_seconds(run: ScrapeRun, search_rate: float, detail_rate: float) -> int | None:
    done, total = phase_progress(run)
    if not total or total <= 0:
        return None
    remaining = max(0, total - done)
    rate = detail_rate if run.phase == "detail" else search_rate
    return int(remaining * 60.0 / max(rate, MIN_RATE_PER_MIN))


def run_metrics(run: ScrapeRun) -> dict | None:
    """One calibration row for a finished run; None while it is still going."""
    if run.finished_at is None:
        return None
    search_seconds = _seconds(run.started_at, run.search_finished_at)
    detail_seconds = _seconds(run.search_finished_at, run.finished_at)
    return {
        "run_id": run.id,
        "started_at": run.started_at,
        "status": run.status,
        "search_seconds": search_seconds,
        "search_items": run.listings_seen,
        "search_rate_per_min": _rate(run.listings_seen, search_seconds),
        "detail_seconds": detail_seconds,
        "detail_items": run.detail_enriched,
        "detail_rate_per_min": _rate(run.detail_enriched, detail_seconds),
    }
