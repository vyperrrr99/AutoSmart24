import datetime as dt

from autosmart24.db.dealers import upsert_dealer
from autosmart24.db.models import Dealer


def test_upsert_dealer_returns_none_for_none_input(db_session):
    assert upsert_dealer(db_session, None, dt.datetime.utcnow()) is None


def test_upsert_dealer_creates_a_new_row(db_session):
    now = dt.datetime.utcnow()
    dealer_id = upsert_dealer(
        db_session,
        {"id": 42, "company_name": "Auto Test Srl", "ratings_stars": 4.5, "ratings_count": 10, "recommend_percentage": 80},
        now,
    )
    db_session.commit()

    assert dealer_id == 42
    row = db_session.get(Dealer, 42)
    assert row.company_name == "Auto Test Srl"
    assert row.ratings_stars == 4.5
    assert row.synced_at == now


def test_upsert_dealer_updates_an_existing_row_not_duplicate(db_session):
    now1 = dt.datetime.utcnow()
    upsert_dealer(db_session, {"id": 42, "company_name": "Old Name", "ratings_stars": 4.0, "ratings_count": 5, "recommend_percentage": 70}, now1)
    db_session.commit()

    now2 = now1 + dt.timedelta(days=1)
    upsert_dealer(db_session, {"id": 42, "company_name": "New Name", "ratings_stars": 4.8, "ratings_count": 12, "recommend_percentage": 90}, now2)
    db_session.commit()

    rows = db_session.query(Dealer).filter_by(id=42).all()
    assert len(rows) == 1
    assert rows[0].company_name == "New Name"
    assert rows[0].ratings_count == 12
    assert rows[0].synced_at == now2


def test_upsert_dealer_handles_two_new_listings_from_the_same_dealer_in_one_uncommitted_batch(db_session):
    """Reproduces a real production crash: processing multiple listings from the
    SAME dealer within one un-committed batch (e.g. one page of the detail
    backlog, or one run of the backfill script) must not create two pending
    Dealer objects with the same primary key. This project's session factory
    sets autoflush=False, so a naive get-or-create that never flushes will
    silently pass in isolation but crash with a UniqueViolation at commit time
    once a dealer has more than one listing in the same batch -- which is the
    normal case for any real dealer, not an edge case."""
    now = dt.datetime.utcnow()
    dealer_info = {"id": 777, "company_name": "Busy Dealer Srl", "ratings_stars": 4.5, "ratings_count": 50, "recommend_percentage": 90}

    first_id = upsert_dealer(db_session, dealer_info, now)
    second_id = upsert_dealer(db_session, dealer_info, now)  # same dealer, still within the same uncommitted transaction
    db_session.commit()  # must not raise IntegrityError

    assert first_id == 777
    assert second_id == 777
    rows = db_session.query(Dealer).filter_by(id=777).all()
    assert len(rows) == 1
