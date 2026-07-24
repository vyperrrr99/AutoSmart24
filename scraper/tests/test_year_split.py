from autosmart24.scraping.year_split import split_year_ranges


def test_no_split_needed_when_under_threshold():
    ranges = split_year_ranges(lambda f, t: 100, 1950, 2026, max_results=4000)
    assert ranges == [(1950, 2026)]


def test_splits_recursively_until_under_threshold():
    counts = {
        (1950, 2026): 10000,
        (1950, 1988): 3000,
        (1989, 2026): 8000,
        (1989, 2007): 3500,
        (2008, 2026): 4500,
        (2008, 2017): 2000,
        (2018, 2026): 2500,
    }

    def count_fn(year_from: int, year_to: int) -> int:
        return counts[(year_from, year_to)]

    ranges = split_year_ranges(count_fn, 1950, 2026, max_results=4000)
    assert ranges == [(1950, 1988), (1989, 2007), (2008, 2017), (2018, 2026)]


def test_stops_splitting_at_single_year_even_if_over_threshold():
    ranges = split_year_ranges(lambda f, t: 999999, 2020, 2020, max_results=4000)
    assert ranges == [(2020, 2020)]
