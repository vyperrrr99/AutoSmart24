from __future__ import annotations

from typing import Callable


def split_year_ranges(
    count_fn: Callable[[int, int], int],
    year_from: int,
    year_to: int,
    max_results: int,
) -> list[tuple[int, int]]:
    count = count_fn(year_from, year_to)
    if count <= max_results or year_from >= year_to:
        return [(year_from, year_to)]

    midpoint = (year_from + year_to) // 2
    left = split_year_ranges(count_fn, year_from, midpoint, max_results)
    right = split_year_ranges(count_fn, midpoint + 1, year_to, max_results)
    return left + right
