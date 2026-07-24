import datetime as dt

from autosmart24.db.models import Listing


def test_db_session_round_trips_a_listing(db_session):
    now = dt.datetime.utcnow()
    db_session.add(
        Listing(
            id="11111111-1111-1111-1111-111111111111",
            brand="Fiat",
            price=15000,
            url="https://www.autoscout24.it/annunci/example",
            first_seen_at=now,
            last_seen_at=now,
            last_checked_at=now,
            status="active",
            detail_scraped=False,
        )
    )
    db_session.commit()

    fetched = db_session.get(Listing, "11111111-1111-1111-1111-111111111111")
    assert fetched is not None
    assert fetched.brand == "Fiat"
    assert fetched.price == 15000
