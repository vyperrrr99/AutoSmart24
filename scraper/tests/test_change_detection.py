from autosmart24.scraping.change_detection import diff_sweep


def test_diff_sweep_classifies_new_changed_unchanged_missing():
    current = {"a": 1000, "b": 2500, "c": 3000}
    active_in_db = {"b": 2000, "c": 3000, "d": 4000}

    diff = diff_sweep(current, active_in_db)

    assert diff.new_ids == {"a"}
    assert diff.price_changed == {"b": 2500}
    assert diff.unchanged_ids == {"c"}
    assert diff.missing_ids == {"d"}


def test_diff_sweep_handles_empty_db():
    diff = diff_sweep({"a": 1000}, {})
    assert diff.new_ids == {"a"}
    assert diff.missing_ids == set()


def test_diff_sweep_handles_empty_sweep():
    diff = diff_sweep({}, {"a": 1000})
    assert diff.missing_ids == {"a"}
    assert diff.new_ids == set()
