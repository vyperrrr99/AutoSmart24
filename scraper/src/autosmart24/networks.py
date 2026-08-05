"""Counts a car once when several seller identities publish it.

Autohero exposes one catalogue through nine AutoScout seller ids. A single BMW
X1 at 63,415 km and 18,999 EUR appears nine times, once per id and never twice
on the same one; of its 12,798 listings, 9,834 are excess copies. Left alone
the seller looks four times its real size, its pricing votes nine times in
every median, and selling one car reads as nine sales.

Deduplication happens ONLY inside a network listed in
`config/reti-venditori.yaml`. That file is curated rather than derived because
a wrong merge raises no error: it erases real stock from the statistics and
leaves a plausible number in its place. Seven unrelated dealers trade as
"City Car" in seven provinces, and any rule keyed on the name would have
merged them.

The registry is proposed by the monthly detector and decided by a human. It
also records the pairs that were examined and rejected, so a false positive is
not reconsidered every month.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from autosmart24.db.models import Listing

DEFAULT_REGISTRY = Path("/app/config/reti-venditori.yaml")


@dataclass
class SellerNetworks:
    networks: list[list[int]] = field(default_factory=list)
    rejected: list[tuple[int, int]] = field(default_factory=list)
    _index: dict[int, int] = field(init=False, default_factory=dict)
    _rejected: set[tuple[int, int]] = field(init=False, default_factory=set)

    def __post_init__(self) -> None:
        for n, members in enumerate(self.networks):
            for dealer_id in members:
                self._index[int(dealer_id)] = n
        for a, b in self.rejected:
            self._rejected.add((int(a), int(b)))
            self._rejected.add((int(b), int(a)))

    @classmethod
    def load(cls, path: Path | str = DEFAULT_REGISTRY) -> "SellerNetworks":
        import yaml

        p = Path(path)
        if not p.exists():
            # An absent registry means "no networks known", not a crash: the
            # scraper must keep running without it.
            return cls()
        data = yaml.safe_load(p.read_text()) or {}
        return cls(
            networks=[list(entry["id"]) for entry in (data.get("reti") or [])],
            rejected=[tuple(pair) for pair in (data.get("scartate") or [])],
        )

    def group_of(self, dealer_id: int | None) -> int | None:
        return self._index.get(dealer_id) if dealer_id is not None else None

    def is_rejected(self, a: int, b: int) -> bool:
        return (int(a), int(b)) in self._rejected


def _fingerprint(row: Listing) -> tuple:
    """Price is included deliberately.

    Two copies whose prices briefly diverge are counted twice for a day, which
    is a smaller error than merging two genuinely different cars -- that one
    cannot be detected afterwards, because the merged row looks ordinary.
    """
    return (
        row.brand,
        row.model,
        row.first_registration.year if row.first_registration else None,
        row.fuel,
        row.drive_train,
        row.mileage_km,
        row.price,
    )


def deduplicate_networks(session: Session, networks: SellerNetworks) -> int:
    """Point every copy at one canonical listing. Returns copies marked.

    Only listings still on sale take part. A canonical that has disappeared is
    replaced by a surviving copy rather than keeping the group attached to a
    sold row -- otherwise a seller rotating its own catalogue would read as a
    sale every time.
    """
    if not networks.networks:
        return 0

    known = [d for group in networks.networks for d in group]
    rows = session.execute(
        select(Listing).where(
            Listing.status == "active",
            Listing.dealer_id.in_(known),
            Listing.mileage_km.is_not(None),
        )
    ).scalars().all()

    groups: dict[tuple, list[Listing]] = defaultdict(list)
    for row in rows:
        net = networks.group_of(row.dealer_id)
        if net is None:
            continue
        groups[(net, _fingerprint(row))].append(row)

    marked = 0
    for members in groups.values():
        # Oldest sighting wins: first_seen_at is when the car entered the
        # market, and every time-to-sell figure is measured from it. A copy
        # published later is a replica, not a new arrival.
        members.sort(key=lambda r: (r.first_seen_at, r.id))
        canonical = members[0]
        if canonical.duplicate_of is not None:
            canonical.duplicate_of = None
        for copy in members[1:]:
            if copy.duplicate_of != canonical.id:
                copy.duplicate_of = canonical.id
                marked += 1
    return marked


def collapse_duplicate_sales(session: Session, since: dt.datetime) -> int:
    """When copies of one car disappear together, keep one sale. Returns the
    number of copies demoted.

    Without this, Autohero selling a single car would add nine sales to the
    figure the BI is built on.
    """
    copies = session.execute(
        select(Listing).where(
            Listing.status == "sold",
            Listing.sold_at.is_not(None),
            Listing.sold_at >= since,
            Listing.duplicate_of.is_not(None),
        )
    ).scalars().all()

    for row in copies:
        row.status = "removed"
        row.removal_reason = "duplicate_listing"
        # The sale belongs to the canonical listing alone.
        row.sold_at = None
    return len(copies)
