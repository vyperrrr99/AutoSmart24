from autosmart24.scraping.rate_control import BlockRateTracker


def test_block_rate_tracker_starts_at_normal_rate():
    tracker = BlockRateTracker()
    assert tracker.delay_multiplier() == 1.0


def test_block_rate_tracker_backs_off_when_threshold_exceeded():
    tracker = BlockRateTracker(window_size=10, threshold=0.2, backoff_multiplier=2.0)
    for _ in range(7):
        tracker.record_success()
    for _ in range(3):
        tracker.record_blocked()
    assert tracker.delay_multiplier() == 2.0


def test_block_rate_tracker_stays_normal_exactly_at_threshold():
    tracker = BlockRateTracker(window_size=10, threshold=0.2, backoff_multiplier=2.0)
    for _ in range(8):
        tracker.record_success()
    for _ in range(2):
        tracker.record_blocked()
    assert tracker.delay_multiplier() == 1.0


def test_block_rate_tracker_recovers_when_rate_drops():
    tracker = BlockRateTracker(window_size=10, threshold=0.2, backoff_multiplier=2.0)
    for _ in range(3):
        tracker.record_blocked()
    assert tracker.delay_multiplier() == 2.0
    for _ in range(10):
        tracker.record_success()
    assert tracker.delay_multiplier() == 1.0


def test_block_rate_tracker_window_limits_history():
    tracker = BlockRateTracker(window_size=5, threshold=0.5, backoff_multiplier=2.0)
    for _ in range(5):
        tracker.record_blocked()
    assert tracker.delay_multiplier() == 2.0
    for _ in range(5):
        tracker.record_success()
    assert tracker.delay_multiplier() == 1.0


def test_block_rate_tracker_fires_callback_only_on_transitions():
    seen: list[float] = []
    tracker = BlockRateTracker(
        window_size=10, threshold=0.2, backoff_multiplier=2.0, on_backoff_change=seen.append
    )

    for _ in range(10):
        tracker.record_blocked()
    assert seen == [2.0]

    for _ in range(10):
        tracker.record_success()
    assert seen == [2.0, 1.0]
