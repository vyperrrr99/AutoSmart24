"""A disappearance is only a sale when nothing else explains it.

Three explanations are checked, in order of how little they have to assume:

1. the same dealer still lists an identical car -- the vehicle is demonstrably
   still for sale, so nothing was sold;
2. the same dealer's stock reference reappears on a new listing whose
   fingerprint also matches -- the ad was republished under a new id;
3. the dealer's stock vanished wholesale -- an August closure in Italy, not
   thirty simultaneous sales.

Measured on a week of live data before this was written: 5,366 of 26,536
disappearances had a twin still on sale (20%), and one night showed 50 dealers
losing 100% of their stock at once. The BI's central metric is time-to-sell, so
each of these left as `sold` is a fabricated sale in the number the product
exists to compute.
"""
from __future__ import annotations

import datetime as dt

import pytest

from autosmart24.db.models import Listing
from autosmart24.removal import (
    RemovalReason,
    reclassify_removals,
    resolve_quarantine,
    strip_unverifiable_redirect_sales,
)


def _listing(session, listing_id, *, dealer=1, brand="Fiat", model="Panda", year=2019,
             fuel="Benzina", drive="Anteriore", km=80000, price=10000, status="active",
             sold_at=None, ref=None, seen=None):
    now = dt.datetime(2026, 8, 2, 6, 0)
    row = Listing(
        id=listing_id, brand=brand, model=model, fuel=fuel, drive_train=drive,
        first_registration=dt.date(year, 1, 1), mileage_km=km, price=price,
        url=f"https://x/{listing_id}", dealer_id=dealer, cross_reference_id=ref,
        first_seen_at=seen or now, last_seen_at=now, last_checked_at=now,
        status=status, sold_at=sold_at, detail_scraped=True,
    )
    session.add(row)
    return row


SINCE = dt.datetime(2026, 8, 2, 0, 0)
SOLD_AT = dt.datetime(2026, 8, 2, 5, 0)


def test_a_disappearance_with_an_identical_car_still_on_sale_is_not_a_sale(db_session):
    """The strongest of the three: it assumes no identity at all.

    We do not claim the two rows are the same car. We observe that the dealer
    is still selling one indistinguishable from it, so no sale can be inferred
    from the disappearance.
    """
    _listing(db_session, "gone", status="sold", sold_at=SOLD_AT)
    _listing(db_session, "twin", status="active")
    db_session.commit()

    reclassify_removals(db_session, since=SINCE)

    row = db_session.get(Listing, "gone")
    assert row.status == "removed"
    assert row.removal_reason == RemovalReason.TWIN_ON_SALE
    assert row.sold_at is None, "a removal is not a sale and must not carry a sale date"
    assert db_session.get(Listing, "twin").status == "active", "the survivor is untouched"


def test_a_genuine_sale_survives(db_session):
    """The guard that matters most: this must not reclassify real sales.

    Every rule here removes rows from the sold set, so a rule that is too eager
    destroys the metric just as thoroughly as the false sales it removes.
    """
    _listing(db_session, "sold-1", status="sold", sold_at=SOLD_AT)
    _listing(db_session, "other", status="active", km=120000)   # different car
    db_session.commit()

    reclassify_removals(db_session, since=SINCE)

    row = db_session.get(Listing, "sold-1")
    assert row.status == "sold"
    assert row.removal_reason is None
    assert row.sold_at == SOLD_AT


def test_a_twin_at_a_different_dealer_does_not_count(db_session):
    """Two dealers selling the same model with the same mileage is a
    coincidence, not evidence about either one's stock."""
    _listing(db_session, "gone", status="sold", sold_at=SOLD_AT, dealer=1)
    _listing(db_session, "elsewhere", status="active", dealer=2)
    db_session.commit()

    reclassify_removals(db_session, since=SINCE)

    assert db_session.get(Listing, "gone").status == "sold"


def test_a_republished_ad_is_recognised_by_reference_and_fingerprint_together(db_session):
    """Neither signal is trusted alone.

    Dealers recycle a stock reference onto the next car -- one was found using
    the same reference for a Toyota Aygo, two Yaris and a MINI Clubman -- and
    they also mistype mileage, so fingerprints drift. Requiring both means an
    error in either one is not enough to fabricate a link.
    """
    _listing(db_session, "old", status="sold", sold_at=SOLD_AT, ref="STOCK-7")
    _listing(db_session, "new", status="active", ref="STOCK-7")
    db_session.commit()

    reclassify_removals(db_session, since=SINCE)

    row = db_session.get(Listing, "old")
    assert row.status == "removed"
    assert row.removal_reason == RemovalReason.REPUBLISHED


def test_a_recycled_reference_on_a_different_car_is_not_a_republication(db_session):
    """The reference alone is not enough -- this is the Auto Scala case."""
    _listing(db_session, "old", status="sold", sold_at=SOLD_AT, ref="STOCK-7",
             brand="Toyota", model="Aygo", year=2021, km=37300)
    _listing(db_session, "new", status="active", ref="STOCK-7",
             brand="MINI", model="Cooper SD Clubman", year=2019, km=43076)
    db_session.commit()

    reclassify_removals(db_session, since=SINCE)

    assert db_session.get(Listing, "old").status == "sold"


def test_a_dealer_whose_stock_vanished_wholesale_is_held_not_sold(db_session):
    """Nobody sells their whole forecourt overnight -- but nor is this proof
    of a closure, so it is held rather than decided. See the quarantine tests
    below for why this case alone waits."""
    for i in range(8):
        _listing(db_session, f"closed-{i}", status="sold", sold_at=SOLD_AT, km=50000 + i * 1000)
    _listing(db_session, "left", status="active", km=99000)
    db_session.commit()

    reclassify_removals(db_session, since=SINCE)

    for i in range(8):
        row = db_session.get(Listing, f"closed-{i}")
        assert row.status == "quarantine", f"closed-{i} should be held"
        assert row.removal_reason == RemovalReason.DEALER_CLOSURE


def test_a_dealer_losing_a_few_cars_is_just_selling(db_session):
    """Below either threshold nothing happens: five cars out of forty is a
    good week, not a shutter coming down."""
    for i in range(4):
        _listing(db_session, f"s-{i}", status="sold", sold_at=SOLD_AT, km=50000 + i * 1000)
    for i in range(40):
        _listing(db_session, f"a-{i}", status="active", km=90000 + i * 1000)
    db_session.commit()

    reclassify_removals(db_session, since=SINCE)

    for i in range(4):
        assert db_session.get(Listing, f"s-{i}").status == "sold"


def test_both_closure_thresholds_must_be_met(db_session):
    """Six of ten is over half but a plausible fortnight; six of six is not."""
    for i in range(6):
        _listing(db_session, f"p-{i}", status="sold", sold_at=SOLD_AT, km=50000 + i * 1000)
    for i in range(30):
        _listing(db_session, f"q-{i}", status="active", km=90000 + i * 1000)
    db_session.commit()

    reclassify_removals(db_session, since=SINCE)

    # 6 gone of 36 = 17%, over the count threshold but under the share one.
    for i in range(6):
        assert db_session.get(Listing, f"p-{i}").status == "sold"


def test_only_disappearances_from_this_round_are_examined(db_session):
    """Older sales are settled. Re-judging them every night would let a car
    listed months later retroactively undo a sale that did happen."""
    old = dt.datetime(2026, 7, 20, 5, 0)
    _listing(db_session, "ancient", status="sold", sold_at=old)
    _listing(db_session, "twin", status="active")
    db_session.commit()

    reclassify_removals(db_session, since=SINCE)

    assert db_session.get(Listing, "ancient").status == "sold"


def test_listings_without_a_dealer_are_left_alone(db_session):
    """All three rules reason about a dealer's stock. A private seller has
    none, so none of them apply and the disappearance stays a sale."""
    _listing(db_session, "private", status="sold", sold_at=SOLD_AT, dealer=None)
    _listing(db_session, "twin", status="active", dealer=None)
    db_session.commit()

    reclassify_removals(db_session, since=SINCE)

    assert db_session.get(Listing, "private").status == "sold"


def test_it_reports_what_it_changed(db_session):
    """The counts go into an event: a step that silently removes a fifth of
    the sales figure has to say so."""
    _listing(db_session, "gone", status="sold", sold_at=SOLD_AT)
    _listing(db_session, "twin", status="active")
    _listing(db_session, "real", status="sold", sold_at=SOLD_AT, km=150000)
    db_session.commit()

    result = reclassify_removals(db_session, since=SINCE)

    assert result[RemovalReason.TWIN_ON_SALE] == 1
    assert sum(result.values()) == 1, "the genuine sale is not counted"


# --- Quarantena: le chiusure sono un'inferenza, non un'osservazione ---------

def test_a_dealer_closure_goes_to_quarantine_not_straight_out_of_the_sales(db_session):
    """The closure rule is the only one of the three that infers rather than
    observes, and it can be wrong in the opposite direction: a disorganised
    dealer may leave sold cars online and clear them out monthly, in which case
    the wholesale disappearance IS a batch of real sales recorded late.

    So it waits instead of deciding. The disappearance date is kept, because
    that is when the car left the market -- resolving with the confirmation
    date instead would add a month to every quarantined car's time-to-sell.
    """
    for i in range(8):
        _listing(db_session, f"c-{i}", status="sold", sold_at=SOLD_AT, km=50000 + i * 1000)
    _listing(db_session, "left", status="active", km=99000)
    db_session.commit()

    reclassify_removals(db_session, since=SINCE)

    row = db_session.get(Listing, "c-0")
    assert row.status == "quarantine"
    assert row.removal_reason == RemovalReason.DEALER_CLOSURE
    assert row.sold_at == SOLD_AT, "the disappearance date must survive the wait"


def test_the_observed_cases_do_not_wait(db_session):
    """A twin still on sale is not a hypothesis: if the car had sold, both
    listings would have gone. Waiting a month would confirm nothing."""
    _listing(db_session, "gone", status="sold", sold_at=SOLD_AT)
    _listing(db_session, "twin", status="active")
    db_session.commit()

    reclassify_removals(db_session, since=SINCE)

    assert db_session.get(Listing, "gone").status == "removed"


def test_quarantine_becomes_a_sale_once_the_absence_is_long_enough(db_session):
    gone_at = dt.datetime(2026, 7, 1, 5, 0)
    row = _listing(db_session, "waited", status="quarantine", sold_at=gone_at,
                   ref=None, seen=gone_at)
    row.removal_reason = RemovalReason.DEALER_CLOSURE
    db_session.commit()

    resolve_quarantine(db_session, now=dt.datetime(2026, 8, 5, 0, 0), days=30)

    row = db_session.get(Listing, "waited")
    assert row.status == "sold"
    assert row.sold_at == gone_at, "the sale is dated when the car vanished"
    assert row.removal_reason == RemovalReason.QUARANTINE_EXPIRED, \
        "a sale confirmed by absence is evidentially different from one observed"


def test_quarantine_is_not_resolved_early(db_session):
    gone_at = dt.datetime(2026, 8, 1, 5, 0)
    row = _listing(db_session, "recent", status="quarantine", sold_at=gone_at, seen=gone_at)
    row.removal_reason = RemovalReason.DEALER_CLOSURE
    db_session.commit()

    resolve_quarantine(db_session, now=dt.datetime(2026, 8, 5, 0, 0), days=30)

    assert db_session.get(Listing, "recent").status == "quarantine"


def test_resolving_quarantine_reports_what_it_confirmed(db_session):
    gone_at = dt.datetime(2026, 7, 1, 5, 0)
    for i in range(3):
        row = _listing(db_session, f"w-{i}", status="quarantine", sold_at=gone_at, seen=gone_at,
                       km=40000 + i * 1000)
        row.removal_reason = RemovalReason.DEALER_CLOSURE
    db_session.commit()

    assert resolve_quarantine(db_session, now=dt.datetime(2026, 8, 5, 0, 0), days=30) == 3


# --- chiusure estive ---------------------------------------------------------
#
# Trenta giorni bastano tutto l'anno tranne che d'estate. Un concessionario che
# chiude il primo agosto riapre a settembre: al trentesimo giorno e' ancora in
# ferie, e dichiarare venduto il suo magazzino sarebbe inventare un mese di
# vendite che non sono avvenute. Nell'agosto 2026 la quarantena ha raccolto
# quasi 6.000 annunci, quindi non e' un caso di scuola.

def _in_quarantena(session, lid, sparito_il):
    row = _listing(session, lid, status="quarantine", sold_at=sparito_il, seen=sparito_il)
    row.removal_reason = RemovalReason.DEALER_CLOSURE
    session.commit()
    return row


def test_a_summer_disappearance_is_not_resolved_after_the_usual_month(db_session):
    """Sparito il primo agosto: al 5 settembre i trenta giorni sono passati, ma
    i concessionari stanno ancora riaprendo."""
    _in_quarantena(db_session, "agosto", dt.datetime(2026, 8, 1, 5, 0))

    resolve_quarantine(db_session, now=dt.datetime(2026, 9, 5, 9, 0), days=30)

    assert db_session.get(Listing, "agosto").status == "quarantine"


def test_a_summer_disappearance_resolves_once_the_market_has_reopened(db_session):
    _in_quarantena(db_session, "agosto", dt.datetime(2026, 8, 1, 5, 0))

    resolve_quarantine(db_session, now=dt.datetime(2026, 9, 16, 9, 0), days=30)

    row = db_session.get(Listing, "agosto")
    assert row.status == "sold"
    assert row.sold_at == dt.datetime(2026, 8, 1, 5, 0), "la vendita resta datata alla sparizione"
    assert row.removal_reason == RemovalReason.QUARANTINE_EXPIRED


def test_the_reopening_date_is_a_floor_not_a_replacement(db_session):
    """Sparito il 25 agosto: i trenta giorni scadono il 24 settembre, dopo la
    ripresa. Vince la regola normale, altrimenti il pavimento accorcerebbe
    l'attesa invece di allungarla."""
    _in_quarantena(db_session, "fine-agosto", dt.datetime(2026, 8, 25, 5, 0))

    resolve_quarantine(db_session, now=dt.datetime(2026, 9, 16, 9, 0), days=30)

    assert db_session.get(Listing, "fine-agosto").status == "quarantine"

    resolve_quarantine(db_session, now=dt.datetime(2026, 9, 25, 9, 0), days=30)

    assert db_session.get(Listing, "fine-agosto").status == "sold"


def test_outside_the_summer_window_nothing_changes(db_session):
    """Una sparizione di marzo non ha niente a che vedere con le ferie."""
    _in_quarantena(db_session, "marzo", dt.datetime(2026, 3, 1, 5, 0))

    resolve_quarantine(db_session, now=dt.datetime(2026, 4, 1, 9, 0), days=30)

    assert db_session.get(Listing, "marzo").status == "sold"


def test_the_summer_rule_repeats_every_year(db_session):
    """Ricavata dall'anno della sparizione, non da date fisse: altrimenti
    varrebbe solo per il 2026 e nessuno se ne accorgerebbe."""
    _in_quarantena(db_session, "2027", dt.datetime(2027, 8, 3, 5, 0))

    resolve_quarantine(db_session, now=dt.datetime(2027, 9, 5, 9, 0), days=30)
    assert db_session.get(Listing, "2027").status == "quarantine"

    resolve_quarantine(db_session, now=dt.datetime(2027, 9, 16, 9, 0), days=30)
    assert db_session.get(Listing, "2027").status == "sold"


# --- strip_unverifiable_redirect_sales --------------------------------------
#
# 374 annunci Fiat il 23/08/2026: la loro pagina redirigeva alla lista del
# modello, mai arricchiti (dealer_id assente), quindi twin_on_sale/republished
# non avevano nulla con cui confrontarli. Passati come venduti non perche'
# verificati, ma perche' privi dei dati per esserlo.

def test_a_redirect_sale_with_no_dealer_id_is_stripped(db_session):
    row = _listing(db_session, "redirect-ignoto", dealer=None, status="sold",
                   sold_at=dt.datetime(2026, 8, 23, 13, 0))
    row.redirect_to = "/lst/fiat/panda"
    db_session.commit()

    removed = strip_unverifiable_redirect_sales(db_session)

    assert removed == 1
    row = db_session.get(Listing, "redirect-ignoto")
    assert row.status == "removed"
    assert row.removal_reason == RemovalReason.REDIRECT_UNVERIFIED
    assert row.sold_at is None, "non e' piu' contata come vendita, quindi niente data di vendita"


def test_a_redirect_sale_with_a_known_dealer_is_left_alone(db_session):
    """Se l'annuncio era gia' stato arricchito prima di sparire, ha un
    dealer_id vero: twin_on_sale/republished possono giudicarlo col criterio
    reale, e questa funzione non deve intromettersi."""
    row = _listing(db_session, "redirect-noto", dealer=42, status="sold",
                   sold_at=dt.datetime(2026, 8, 23, 13, 0))
    row.redirect_to = "/lst/fiat/panda"
    db_session.commit()

    removed = strip_unverifiable_redirect_sales(db_session)

    assert removed == 0
    assert db_session.get(Listing, "redirect-noto").status == "sold"


def test_a_sale_without_a_redirect_is_left_alone_even_with_no_dealer(db_session):
    """Una vendita 404/410 pulita non ha bisogno di verifica: e' il redirect
    a mancare di prova, non l'assenza del dealer da sola."""
    row = _listing(db_session, "vendita-pulita", dealer=None, status="sold",
                   sold_at=dt.datetime(2026, 8, 23, 13, 0))
    db_session.commit()

    removed = strip_unverifiable_redirect_sales(db_session)

    assert removed == 0
    assert db_session.get(Listing, "vendita-pulita").status == "sold"


def test_a_redirect_that_is_not_sold_is_left_alone(db_session):
    """Solo le vendite si ripuliscono: un annuncio ancora attivo o gia'
    riclassificato altrimenti non e' affare di questa funzione."""
    row = _listing(db_session, "ancora-attivo", dealer=None, status="active")
    row.redirect_to = "/lst/fiat/panda"
    db_session.commit()

    removed = strip_unverifiable_redirect_sales(db_session)

    assert removed == 0
