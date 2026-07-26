from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from autosmart24.db.models import Dealer


def upsert_dealer(session: Session, dealer_info: dict | None, now: dt.datetime) -> int | None:
    if dealer_info is None:
        return None
    dealer = session.get(Dealer, dealer_info["id"])
    if dealer is None:
        dealer = Dealer(id=dealer_info["id"], synced_at=now)
        session.add(dealer)
    dealer.company_name = dealer_info["company_name"]
    dealer.ratings_stars = dealer_info["ratings_stars"]
    dealer.ratings_count = dealer_info["ratings_count"]
    dealer.recommend_percentage = dealer_info["recommend_percentage"]
    dealer.synced_at = now
    return dealer.id
