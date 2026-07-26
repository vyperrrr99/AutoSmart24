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
