from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SweepDiff:
    new_ids: set[str] = field(default_factory=set)
    price_changed: dict[str, int] = field(default_factory=dict)
    unchanged_ids: set[str] = field(default_factory=set)
    missing_ids: set[str] = field(default_factory=set)


def diff_sweep(current_prices: dict[str, int], active_db_prices: dict[str, int]) -> SweepDiff:
    diff = SweepDiff()

    for listing_id, price in current_prices.items():
        if listing_id not in active_db_prices:
            diff.new_ids.add(listing_id)
        elif active_db_prices[listing_id] != price:
            diff.price_changed[listing_id] = price
        else:
            diff.unchanged_ids.add(listing_id)

    diff.missing_ids = set(active_db_prices) - set(current_prices)
    return diff
