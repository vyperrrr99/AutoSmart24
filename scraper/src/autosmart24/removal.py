"""Separates disappearances that are sales from disappearances that are not.

A listing vanishing from the search results is the only evidence this project
has that a car sold. It is weak evidence, and three things imitate it:

* the dealer had the same car listed twice and dropped one copy;
* the dealer republished the ad under a new AutoScout id;
* the dealer shut for August and pulled the whole forecourt.

Measured on a week of live data: 5,366 of 26,536 disappearances -- one in five
-- had an identical car still on sale at the same dealer, and a single night
showed 50 dealers losing 100% of their stock at once.

Time-to-sell is the metric the BI is built on, so each of these left as a sale
is not noise around a true value: it is an invented event that drags the median
down. The rules here are therefore deliberately conservative. Every one of them
only ever REMOVES rows from the sold set, so being too eager destroys the
metric just as surely as leaving the false sales in -- which is why each rule
needs corroboration rather than a single suggestive signal.

Runs after a full round, not inside a brand sweep: the closure rule needs a
dealer's whole stock, and a dealer sells across brands that are swept hours
apart.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from autosmart24.db.models import Listing

# A dealer must lose BOTH a meaningful number of cars AND most of its stock.
# Either alone is ordinary: a big dealer can sell eight cars in a good week,
# and a dealer with three cars can sell two.
CLOSURE_MIN_CARS = 5
CLOSURE_MIN_SHARE = 0.5


class RemovalReason:
    TWIN_ON_SALE = "twin_on_sale"
    REPUBLISHED = "republished"
    DEALER_CLOSURE = "dealer_closure"
    QUARANTINE_EXPIRED = "quarantine_expired"


# How long a wholesale disappearance is held before absence is accepted as
# evidence of sale.
QUARANTINE_DAYS = 30

# Thirty days is enough all year except in summer. An Italian dealer who shuts
# on the first of August is still shut on the thirtieth day, and calling their
# stock sold would invent a month of sales that never happened. In August 2026
# quarantine collected nearly 6,000 listings, so this is not a hypothetical.
#
# Dates rather than a longer window: the wait needs to end when the market
# reopens, not a fixed span later. Derived from the year the car vanished, so
# the rule repeats every year instead of being true only for 2026.
CHIUSURA_ESTIVA = ((7, 15), (8, 31))
RIPRESA_AUTUNNALE = (9, 15)


def risolvibile_dal(sold_at: dt.datetime, days: int = QUARANTINE_DAYS) -> dt.datetime:
    """Quando l'assenza di questo annuncio puo' valere come vendita.

    La ripresa e' un pavimento, non un sostituto: chi sparisce il 25 agosto
    aspetta comunque i suoi trenta giorni, che scadono dopo. Prendere sempre
    la data di ripresa accorcerebbe l'attesa invece di allungarla.
    """
    normale = sold_at + dt.timedelta(days=days)
    (mese_da, giorno_da), (mese_a, giorno_a) = CHIUSURA_ESTIVA
    inizio = dt.datetime(sold_at.year, mese_da, giorno_da)
    fine = dt.datetime(sold_at.year, mese_a, giorno_a, 23, 59, 59)
    if inizio <= sold_at <= fine:
        return max(normale, dt.datetime(sold_at.year, *RIPRESA_AUTUNNALE))
    return normale


def _fingerprint(row: Listing) -> tuple:
    """What identifies a car well enough to compare two listings.

    Mileage carries most of the weight: two different cars rarely share an
    exact six-figure reading, which is why the duplicate rate falls from 2.5%
    below 20,000 km to 0.55% above 60,000.
    """
    return (
        row.dealer_id,
        row.brand,
        row.model,
        row.first_registration.year if row.first_registration else None,
        row.fuel,
        row.drive_train,
        row.mileage_km,
    )


def reclassify_removals(session: Session, since: dt.datetime) -> Counter:
    """Re-examine sales declared since `since`; return the counts changed.

    Only this round's disappearances are considered. Older sales are settled,
    and re-judging them nightly would let a car listed months later
    retroactively undo a sale that really happened.
    """
    candidates = session.execute(
        select(Listing).where(
            Listing.status == "sold",
            Listing.sold_at.is_not(None),
            Listing.sold_at >= since,
            Listing.dealer_id.is_not(None),
        )
    ).scalars().all()
    if not candidates:
        return Counter()

    dealer_ids = {row.dealer_id for row in candidates}
    alive = session.execute(
        select(Listing).where(
            Listing.status == "active",
            Listing.dealer_id.in_(dealer_ids),
        )
    ).scalars().all()

    alive_by_fingerprint: dict[tuple, list[Listing]] = defaultdict(list)
    alive_by_reference: dict[tuple, list[Listing]] = defaultdict(list)
    alive_per_dealer: Counter = Counter()
    for row in alive:
        alive_by_fingerprint[_fingerprint(row)].append(row)
        alive_per_dealer[row.dealer_id] += 1
        if row.cross_reference_id:
            alive_by_reference[(row.dealer_id, row.cross_reference_id)].append(row)

    gone_per_dealer: Counter = Counter()
    for row in candidates:
        gone_per_dealer[row.dealer_id] += 1

    # A dealer whose whole stock went at once. Computed before the per-listing
    # rules so a closure is attributed as such even where a twin also survives.
    closed_dealers = set()
    for dealer_id, gone in gone_per_dealer.items():
        total = gone + alive_per_dealer.get(dealer_id, 0)
        if gone >= CLOSURE_MIN_CARS and total and gone / total >= CLOSURE_MIN_SHARE:
            closed_dealers.add(dealer_id)

    changed: Counter = Counter()
    for row in candidates:
        reason = None

        # Ordered from the most specific explanation to the least. All three
        # lead to the same treatment, so the order only decides which reason is
        # recorded -- but that label is what a later reader uses to judge
        # whether the rule was right, so the sharpest one wins.
        if row.dealer_id in closed_dealers:
            reason = RemovalReason.DEALER_CLOSURE
        elif row.cross_reference_id:
            # Both signals or neither. Dealers recycle a stock reference onto
            # the next car, and they mistype mileage; requiring the reference
            # AND the fingerprint means one error is not enough to fabricate
            # a link between unrelated cars.
            for candidate in alive_by_reference.get((row.dealer_id, row.cross_reference_id), []):
                if _fingerprint(candidate) == _fingerprint(row):
                    reason = RemovalReason.REPUBLISHED
                    break

        if reason is None and alive_by_fingerprint.get(_fingerprint(row)):
            # No identity is claimed here: the dealer is still selling a car
            # indistinguishable from this one, so nothing can be inferred to
            # have sold.
            reason = RemovalReason.TWIN_ON_SALE

        if reason is None:
            continue

        if reason == RemovalReason.DEALER_CLOSURE:
            # The only rule of the three that infers rather than observes, and
            # it can be wrong the other way: a disorganised dealer may leave
            # sold cars online and clear them out monthly, making a wholesale
            # disappearance a batch of real sales recorded late. So it is held,
            # not decided -- and sold_at is KEPT, because it records when the
            # car left the market. Resolving later with the confirmation date
            # would add a month to every quarantined car's time-to-sell.
            row.status = "quarantine"
        else:
            row.status = "removed"
            # These two are observations, not inferences: the car is visibly
            # still on sale. The sale date goes with the sale, and leaving it
            # set would let any query filtering on the date alone keep counting
            # this as one.
            row.sold_at = None
        row.removal_reason = reason
        changed[reason] += 1

    return changed


def resolve_quarantine(session: Session, now: dt.datetime, days: int = QUARANTINE_DAYS) -> int:
    """Turn long-unseen quarantined listings into sales. Returns how many.

    Absence sustained for a month is the best evidence available that a car
    really left the market: a dealer on holiday is back, and an ad paused for
    editing has returned. What has not come back, sold.

    `sold_at` is untouched, so the sale is dated when the car vanished rather
    than when we accepted it.

    Summer disappearances wait longer -- see risolvibile_dal.
    """
    cutoff = now - dt.timedelta(days=days)
    rows = session.execute(
        select(Listing).where(
            Listing.status == "quarantine",
            Listing.sold_at.is_not(None),
            Listing.sold_at < cutoff,
        )
    ).scalars().all()
    # Il filtro SQL e' solo un prefiltro veloce: la finestra estiva sposta la
    # scadenza in avanti, mai indietro, quindi chi non ha superato i giorni
    # normali non puo' comunque essere pronto.
    rows = [r for r in rows if risolvibile_dal(r.sold_at, days) <= now]
    for row in rows:
        row.status = "sold"
        # Distinct from a directly observed sale: this one rests on a month of
        # absence, and a later reader should be able to tell the two apart.
        row.removal_reason = RemovalReason.QUARANTINE_EXPIRED
    return len(rows)
