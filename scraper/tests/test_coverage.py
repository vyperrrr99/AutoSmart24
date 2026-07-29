from autosmart24.scraping.coverage import (
    MAX_MISSING_FRACTION,
    PAGE_SIZE,
    assess_coverage,
)


def test_a_complete_crawl_can_detect_sales():
    v = assess_coverage(lost_models=0, lost_pages=0, listings_seen=10_000)
    assert v.can_detect_sales is True
    assert v.estimated_missing == 0


def test_a_small_page_gap_still_allows_detection():
    # 10 pages ~ 200 listings out of 10,000 = 2%, under the 5% threshold.
    v = assess_coverage(lost_models=0, lost_pages=10, listings_seen=10_000)
    assert v.can_detect_sales is True
    assert v.estimated_missing == 200


def test_a_large_page_gap_suppresses_detection():
    # 30 pages ~ 600 listings out of 10,000 = 6%, over the threshold.
    v = assess_coverage(lost_models=0, lost_pages=30, listings_seen=10_000)
    assert v.can_detect_sales is False
    assert v.estimated_missing == 600


def test_the_threshold_boundary_is_inclusive():
    # Exactly 5% must still be allowed: the spec says "buco <= 5%".
    seen = 10_000
    pages = int(seen * MAX_MISSING_FRACTION / PAGE_SIZE)
    assert assess_coverage(lost_models=0, lost_pages=pages, listings_seen=seen).can_detect_sales is True
    assert assess_coverage(lost_models=0, lost_pages=pages + 1, listings_seen=seen).can_detect_sales is False


def test_a_lost_model_suppresses_detection_whatever_the_page_count():
    """A model was dropped while learning its page count, so the size of the
    hole is unknown. There is no fraction to compare against a threshold."""
    for pages in (0, 1, 1000):
        v = assess_coverage(lost_models=1, lost_pages=pages, listings_seen=1_000_000)
        assert v.can_detect_sales is False
        assert "modell" in v.reason.lower()
        # The size of the hole is not estimable when a model is lost.
        assert v.estimated_missing is None


def test_nothing_seen_and_something_lost_suppresses_detection():
    """Guards the division and states the obvious case: if the crawl saw
    nothing but lost pages, coverage is zero, not complete."""
    v = assess_coverage(lost_models=0, lost_pages=1, listings_seen=0)
    assert v.can_detect_sales is False


def test_nothing_seen_and_nothing_lost_is_a_complete_if_empty_crawl():
    v = assess_coverage(lost_models=0, lost_pages=0, listings_seen=0)
    assert v.can_detect_sales is True


def test_the_reason_is_always_populated():
    for args in [(0, 0, 100), (0, 1, 100), (1, 0, 100), (0, 1, 0)]:
        assert assess_coverage(*args).reason
