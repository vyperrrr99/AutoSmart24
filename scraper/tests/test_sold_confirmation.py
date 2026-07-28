from autosmart24.scraping.detail_queue import DetailResult
from autosmart24.scraping.sold_confirmation import looks_removed


def _detail(brand: str | None) -> dict:
    return {"brand": brand, "model": "Panda", "price": 10000}


def test_an_explicit_removal_counts_as_removed():
    assert looks_removed(DetailResult(sold=True), "Fiat") is True


def test_a_live_page_for_the_same_brand_is_not_removed():
    assert looks_removed(DetailResult(sold=False, data=_detail("Fiat")), "Fiat") is False


def test_a_live_page_for_a_different_brand_means_the_id_was_reassigned():
    """AutoScout reuses a retired listing's id for another car and 308-redirects
    the old URL to it. The client follows redirects, so the page loads fine and
    looks active — but it is a different car, which means the listing we asked
    about is gone."""
    assert looks_removed(DetailResult(sold=False, data=_detail("Audi")), "Mercedes-Benz") is True


def test_a_missing_brand_is_not_treated_as_reassignment():
    """An absent brand field is missing information, not evidence of reuse.
    Concluding 'removed' from it would invent sales out of parsing gaps."""
    assert looks_removed(DetailResult(sold=False, data=_detail(None)), "Fiat") is False


def test_a_result_without_data_is_not_removed():
    assert looks_removed(DetailResult(sold=False, data=None), "Fiat") is False


def test_brand_comparison_ignores_case_and_padding():
    """Snippet and detail pages have been seen to differ in casing; that is not
    a reassignment."""
    assert looks_removed(DetailResult(sold=False, data=_detail(" fiat ")), "Fiat") is False
