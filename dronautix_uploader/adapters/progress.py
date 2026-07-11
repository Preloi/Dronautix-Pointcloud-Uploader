"""Progress event adapters shared by UI and service layers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from dronautix_uploader.core.contracts import ProgressCallback, ProgressEvent


@dataclass(frozen=True)
class ProgressDispatchError:
    """Captured failure from a progress sink."""

    sink: ProgressCallback
    exception: Exception


class ProgressDispatcher:
    """Fan out ``ProgressEvent`` instances to registered sinks.

    By default every sink gets a chance to observe the event. Exceptions are
    captured and returned to the caller instead of aborting dispatch.
    """

    def __init__(
        self,
        sinks: Iterable[ProgressCallback] = (),
        *,
        isolate_exceptions: bool = True,
    ) -> None:
        self._sinks: list[ProgressCallback] = []
        self.isolate_exceptions = isolate_exceptions
        for sink in sinks:
            self.connect(sink)

    @property
    def sinks(self) -> tuple[ProgressCallback, ...]:
        return tuple(self._sinks)

    def connect(self, sink: ProgressCallback) -> ProgressCallback:
        if not callable(sink):
            raise TypeError("progress sink must be callable")
        self._sinks.append(sink)
        return sink

    def disconnect(self, sink: ProgressCallback) -> None:
        self._sinks.remove(sink)

    def clear(self) -> None:
        self._sinks.clear()

    def emit(self, event: ProgressEvent) -> tuple[ProgressDispatchError, ...]:
        errors: list[ProgressDispatchError] = []
        for sink in tuple(self._sinks):
            try:
                sink(event)
            except Exception as exc:
                if not self.isolate_exceptions:
                    raise
                errors.append(ProgressDispatchError(sink=sink, exception=exc))
        return tuple(errors)

    def __call__(self, event: ProgressEvent) -> None:
        self.emit(event)


class ProgressRecorder:
    """Sink implementation that keeps events in arrival order."""

    def __init__(self) -> None:
        self._events: list[ProgressEvent] = []

    @property
    def events(self) -> tuple[ProgressEvent, ...]:
        return tuple(self._events)

    def clear(self) -> None:
        self._events.clear()

    def record(self, event: ProgressEvent) -> None:
        self._events.append(event)

    def __call__(self, event: ProgressEvent) -> None:
        self.record(event)
