"""The same car, published once per seller identity, must count once.

Autohero exposes one catalogue through nine AutoScout seller ids: a single BMW
X1 at 63,415 km and 18,999 EUR appears nine times, once per id, never twice on
the same one. Of its 12,798 listings, 9,834 are excess copies -- so the seller
looks four times its real size, its price policy votes nine times in every
median, and selling one car looks like nine sales.

Deduplication only ever happens inside a registered network. A wrong merge
does not raise an error: it erases real stock from the statistics and leaves a
plausible number behind. Seven unrelated dealers are called "City Car".
"""
from __future__ import annotations

import datetime as dt

from autosmart24.db.models import Listing
from autosmart24.networks import (
    SellerNetworks,
    deduplicate_networks,
)


def _listing(session, listing_id, *, dealer, brand="BMW", model="X1", year=2020,
             fuel="Diesel", drive="4x4", km=63415, price=18999, status="active",
             seen=None, sold_at=None):
    now = dt.datetime(2026, 8, 4, 6, 0)
    row = Listing(
        id=listing_id, brand=brand, model=model, fuel=fuel, drive_train=drive,
        first_registration=dt.date(year, 1, 1), mileage_km=km, price=price,
        url=f"https://x/{listing_id}", dealer_id=dealer,
        first_seen_at=seen or now, last_seen_at=now, last_checked_at=now,
        status=status, sold_at=sold_at, detail_scraped=True,
    )
    session.add(row)
    return row


NET = SellerNetworks(networks=[[1, 2, 3]])


def test_one_car_published_across_a_network_counts_once(db_session):
    older = dt.datetime(2026, 7, 1, 6, 0)
    _listing(db_session, "a", dealer=1, seen=older)
    _listing(db_session, "b", dealer=2)
    _listing(db_session, "c", dealer=3)
    db_session.commit()

    deduplicate_networks(db_session, NET)

    assert db_session.get(Listing, "a").duplicate_of is None, "the original is the canonical"
    assert db_session.get(Listing, "b").duplicate_of == "a"
    assert db_session.get(Listing, "c").duplicate_of == "a"


def test_the_oldest_sighting_is_the_canonical(db_session):
    """Not an arbitrary pick: first_seen_at is when the car entered the market,
    which is the date every time-to-sell figure is measured from. A copy
    published later is a replica, not a new arrival."""
    _listing(db_session, "late", dealer=1, seen=dt.datetime(2026, 8, 1, 6, 0))
    _listing(db_session, "early", dealer=2, seen=dt.datetime(2026, 6, 1, 6, 0))
    db_session.commit()

    deduplicate_networks(db_session, NET)

    assert db_session.get(Listing, "early").duplicate_of is None
    assert db_session.get(Listing, "late").duplicate_of == "early"


def test_sellers_outside_any_network_are_never_merged(db_session):
    """The City Car case: same name, seven provinces, seven businesses. Only a
    registered network deduplicates."""
    _listing(db_session, "x", dealer=90)
    _listing(db_session, "y", dealer=91)
    db_session.commit()

    deduplicate_networks(db_session, NET)

    assert db_session.get(Listing, "x").duplicate_of is None
    assert db_session.get(Listing, "y").duplicate_of is None


def test_two_genuinely_different_cars_in_one_network_stay_separate(db_session):
    """A network really can hold two distinct cars. Only an identical
    fingerprint -- including price -- merges them."""
    _listing(db_session, "one", dealer=1, km=63415)
    _listing(db_session, "two", dealer=2, km=91000)
    db_session.commit()

    deduplicate_networks(db_session, NET)

    assert db_session.get(Listing, "one").duplicate_of is None
    assert db_session.get(Listing, "two").duplicate_of is None


def test_a_price_difference_keeps_them_apart(db_session):
    """Price is part of the fingerprint. Briefly counting two is a smaller
    error than merging two real cars, which cannot be undone from the data."""
    _listing(db_session, "one", dealer=1, price=18999)
    _listing(db_session, "two", dealer=2, price=17500)
    db_session.commit()

    deduplicate_networks(db_session, NET)

    assert db_session.get(Listing, "two").duplicate_of is None


def test_when_the_canonical_disappears_a_survivor_is_promoted(db_session):
    """Autohero rotating its own catalogue must not invent a sale.

    If the canonical vanished and the copies did not, the car is still for
    sale. Leaving the copies pointing at a sold row would make every internal
    rotation look like a sale -- the very fault this project spent a week
    closing, entering by another door.
    """
    gone = dt.datetime(2026, 8, 4, 5, 0)
    _listing(db_session, "was-canon", dealer=1, seen=dt.datetime(2026, 6, 1, 6, 0),
             status="sold", sold_at=gone)
    b = _listing(db_session, "alive", dealer=2, seen=dt.datetime(2026, 6, 5, 6, 0))
    b.duplicate_of = "was-canon"
    db_session.commit()

    deduplicate_networks(db_session, NET)

    assert db_session.get(Listing, "alive").duplicate_of is None, "the survivor becomes canonical"


def test_duplicates_are_not_counted_as_separate_sales(db_session):
    """Nine listings disappearing together are one car sold, not nine."""
    gone = dt.datetime(2026, 8, 4, 5, 0)
    _listing(db_session, "canon", dealer=1, seen=dt.datetime(2026, 6, 1, 6, 0),
             status="sold", sold_at=gone)
    for i, d in enumerate((2, 3)):
        row = _listing(db_session, f"copy-{i}", dealer=d, status="sold", sold_at=gone)
        row.duplicate_of = "canon"
    db_session.commit()

    from autosmart24.networks import collapse_duplicate_sales
    collapsed = collapse_duplicate_sales(db_session, since=dt.datetime(2026, 8, 4, 0, 0))

    assert collapsed == 2
    assert db_session.get(Listing, "canon").status == "sold"
    for i in range(2):
        row = db_session.get(Listing, f"copy-{i}")
        assert row.status == "removed"
        assert row.sold_at is None


def test_a_registry_entry_can_be_rejected(db_session):
    """False positives are recorded so the monthly review does not re-ask."""
    nets = SellerNetworks(networks=[[1, 2, 3]], rejected=[(1, 2)])
    assert nets.group_of(1) is not None
    assert nets.is_rejected(1, 2) is True
    assert nets.is_rejected(2, 1) is True, "order must not matter"
    assert nets.is_rejected(1, 3) is False
