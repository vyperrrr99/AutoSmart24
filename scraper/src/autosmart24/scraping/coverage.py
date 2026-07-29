"""Whether a crawl covered enough ground for sold detection to run.

Pure decision, no I/O: it is the gate in front of the only code path in the
project that can write ``status = "sold"``, and a gate is worth being able to
check with a table of cases rather than a simulated sweep.

What this does NOT protect against is a false sale. That is guaranteed
upstream: a sale is declared only where ``fetch_detail`` returned a result,
which requires either a page that loaded or an explicit 404/410 -- a timeout
raises and produces nothing. A listing missed by an incomplete crawl is
therefore opened, answers Active, and stays active. This threshold governs
wasted work, not correctness.
"""

from __future__ import annotations

from dataclasses import dataclass

# AutoScout24 serves 20 results per page (config.MAX_RESULTS_PER_QUERY = 4000
# over its 200-page pagination cap). A lost page was never fetched, so its
# real size is unknown and this is an estimate -- acceptable precisely because
# the threshold below decides cost rather than correctness.
PAGE_SIZE = 20

MAX_MISSING_FRACTION = 0.05


@dataclass(frozen=True)
class CoverageVerdict:
    """Decision about whether sold detection may run.

    estimated_missing: int | None
        A lost page can be estimated at PAGE_SIZE listings; a lost model cannot
        be estimated at all (returns None). A None value signals "unknown" to
        callers rather than an apparently confident but wrong zero.
    """
    can_detect_sales: bool
    estimated_missing: int | None
    reason: str


def assess_coverage(lost_models: int, lost_pages: int, listings_seen: int) -> CoverageVerdict:
    estimated_missing = max(0, lost_pages) * PAGE_SIZE

    if lost_models > 0:
        # No estimate is possible: the job died while learning the model's page
        # count, so the hole is somewhere between fifty and five thousand
        # listings and nothing on hand narrows it down.
        return CoverageVerdict(
            can_detect_sales=False,
            estimated_missing=None,
            reason=f"{lost_models} modelli non recuperati: dimensione del buco non stimabile",
        )

    if estimated_missing == 0:
        return CoverageVerdict(True, 0, "scansione completa")

    if listings_seen <= 0:
        return CoverageVerdict(
            can_detect_sales=False,
            estimated_missing=estimated_missing,
            reason="nessun annuncio visto ma pagine perse: copertura nulla",
        )

    fraction = estimated_missing / listings_seen
    if fraction > MAX_MISSING_FRACTION:
        return CoverageVerdict(
            can_detect_sales=False,
            estimated_missing=estimated_missing,
            reason=(
                f"buco stimato {estimated_missing} annunci su {listings_seen} visti "
                f"({fraction:.1%}), oltre la soglia del {MAX_MISSING_FRACTION:.0%}"
            ),
        )

    return CoverageVerdict(
        can_detect_sales=True,
        estimated_missing=estimated_missing,
        reason=(
            f"buco stimato {estimated_missing} annunci su {listings_seen} visti "
            f"({fraction:.1%}), entro la soglia"
        ),
    )
