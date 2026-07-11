import pytest

from dronautix_uploader.adapters.progress import ProgressDispatcher, ProgressRecorder
from dronautix_uploader.core.contracts import ProgressEvent


def test_progress_dispatcher_preserves_sink_and_event_order():
    calls = []

    def first(event):
        calls.append(("first", event.message))

    def second(event):
        calls.append(("second", event.message))

    dispatcher = ProgressDispatcher([first, second])

    dispatcher(ProgressEvent(kind="log", message="one"))
    dispatcher(ProgressEvent(kind="progress", percent=0.5))

    assert calls == [
        ("first", "one"),
        ("second", "one"),
        ("first", ""),
        ("second", ""),
    ]


def test_progress_dispatcher_isolates_sink_exceptions_by_default():
    calls = []

    def failing(event):
        calls.append(("failing", event.kind))
        raise ValueError("broken sink")

    def succeeding(event):
        calls.append(("succeeding", event.kind))

    dispatcher = ProgressDispatcher([failing, succeeding])

    errors = dispatcher.emit(ProgressEvent(kind="warning", message="check"))

    assert calls == [("failing", "warning"), ("succeeding", "warning")]
    assert len(errors) == 1
    assert errors[0].sink is failing
    assert isinstance(errors[0].exception, ValueError)


def test_progress_dispatcher_can_fail_fast():
    def failing(event):
        raise RuntimeError(event.message)

    dispatcher = ProgressDispatcher([failing], isolate_exceptions=False)

    with pytest.raises(RuntimeError, match="stop"):
        dispatcher.emit(ProgressEvent(kind="error", message="stop"))


def test_progress_recorder_keeps_events_in_arrival_order():
    recorder = ProgressRecorder()
    events = (
        ProgressEvent(kind="step", step=1, total_steps=2, message="prepare"),
        ProgressEvent(kind="detail", detail="copy files"),
        ProgressEvent(kind="progress", percent=1.0),
    )

    for event in events:
        recorder(event)

    assert recorder.events == events
