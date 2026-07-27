import datetime as dt

from autosmart24.queue_control import QueueController


def test_new_controller_is_not_halted():
    controller = QueueController()

    assert controller.is_halted() is False
    assert controller.state().reason is None


def test_halt_records_reason_and_timestamp():
    controller = QueueController(now_fn=lambda: dt.datetime(2026, 7, 27, 4, 12, 0))

    controller.halt("blocco rilevato su Toyota")

    state = controller.state()
    assert state.halted is True
    assert state.reason == "blocco rilevato su Toyota"
    assert state.halted_at == dt.datetime(2026, 7, 27, 4, 12, 0)


def test_resume_clears_the_halt():
    controller = QueueController()
    controller.halt("blocco")

    controller.resume()

    state = controller.state()
    assert state.halted is False
    assert state.reason is None
    assert state.halted_at is None


def test_halt_keeps_the_first_reason():
    """The first block is the diagnostic one: later runs exiting early must
    not overwrite it with their own message."""
    controller = QueueController(now_fn=lambda: dt.datetime(2026, 7, 27, 4, 12, 0))
    controller.halt("blocco rilevato su Toyota")

    controller.halt("blocco rilevato su Kia")

    assert controller.state().reason == "blocco rilevato su Toyota"
