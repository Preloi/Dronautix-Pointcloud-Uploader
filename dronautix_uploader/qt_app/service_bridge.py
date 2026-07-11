"""Service bridge helpers for the QtWidgets V2 application."""

from __future__ import annotations

from dronautix_uploader.adapters.progress import ProgressDispatcher, ProgressRecorder
from dronautix_uploader.core.contracts import ProgressCallback, ProgressEvent


class QtServiceBridge:
    """Connect UI-free service callbacks with future Qt presentation objects."""

    def __init__(self, dispatcher: ProgressDispatcher | None = None) -> None:
        self.dispatcher = dispatcher or ProgressDispatcher()
        self.recorder = ProgressRecorder()
        self.dispatcher.connect(self.recorder)

    @property
    def progress_callback(self) -> ProgressCallback:
        return self.dispatcher

    @property
    def events(self) -> tuple[ProgressEvent, ...]:
        return self.recorder.events

    def connect_progress_sink(self, sink: ProgressCallback) -> ProgressCallback:
        return self.dispatcher.connect(sink)

    def disconnect_progress_sink(self, sink: ProgressCallback) -> None:
        self.dispatcher.disconnect(sink)

    def emit_progress(self, event: ProgressEvent) -> tuple[object, ...]:
        return self.dispatcher.emit(event)

    def attach_qt_progress_signal(self) -> object:
        """Create a QObject with a ``progress`` signal and connect it as a sink.

        PySide6 is imported lazily so this module remains importable in core
        tests and on machines that only run the service layer.
        """

        emitter = create_qt_progress_emitter()
        self.dispatcher.connect(emitter.progress.emit)
        return emitter


def create_qt_progress_emitter() -> object:
    """Return a small QObject emitting ``ProgressEvent`` objects via Qt Signal."""

    try:
        from PySide6 import QtCore
    except ImportError as exc:
        raise RuntimeError("PySide6 is required to create Qt progress signals") from exc

    class _QtProgressEmitter(QtCore.QObject):
        progress = QtCore.Signal(object)

    return _QtProgressEmitter()


__all__ = [
    "QtServiceBridge",
    "create_qt_progress_emitter",
]
