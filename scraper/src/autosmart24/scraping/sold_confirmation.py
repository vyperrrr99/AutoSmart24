from __future__ import annotations

from autosmart24.scraping.detail_queue import DetailResult


def looks_removed(result: DetailResult, expected_brand: str) -> bool:
    """Whether a detail response means the listing we asked about is gone.

    Two ways a listing can be gone, and only one is obvious:

    * the site says so — 404/410, or a status other than Active
    * the id was reassigned. AutoScout recycles a retired listing's id for an
      unrelated car and 308-redirects the old URL to it. The HTTP client
      follows redirects, so the page loads, looks perfectly active, and
      describes a different car. Without the brand comparison below the caller
      would read that as "still on sale" and keep a retired listing active
      forever.

    A missing brand field is deliberately NOT treated as reassignment: that is
    absent information, and concluding "removed" from it would manufacture
    sales out of parsing gaps.
    """
    if result.sold:
        return True

    if not result.data:
        return False

    actual_brand = result.data.get("brand")
    if not actual_brand:
        return False

    return actual_brand.strip().casefold() != expected_brand.strip().casefold()
