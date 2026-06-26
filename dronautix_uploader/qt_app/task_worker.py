"""Import-safe Qt one-shot worker helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class TaskWorkerBundle:
    thread: Any
    worker: Any


def create_task_worker(QtCore: Any, task: Callable[[], Any]) -> TaskWorkerBundle:
    """Create a QThread/QObject pair that runs ``task`` once.

    ``QtCore`` is passed in by the already-running Qt application so this module
    stays importable on machines without PySide6.
    """

    if not callable(task):
        raise TypeError("task must be callable")

    class TaskWorker(QtCore.QObject):
        result = QtCore.Signal(object)
        error = QtCore.Signal(object)
        finished = QtCore.Signal()

        def __init__(self, callable_task: Callable[[], Any]) -> None:
            super().__init__()
            self._task = callable_task

        @QtCore.Slot()
        def run(self) -> None:
            try:
                self.result.emit(self._task())
            except Exception as exc:
                self.error.emit(exc)
            finally:
                self.finished.emit()

    thread = QtCore.QThread()
    worker = TaskWorker(task)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(worker.deleteLater)
    worker.finished.connect(thread.quit, QtCore.Qt.DirectConnection)
    return TaskWorkerBundle(thread=thread, worker=worker)


__all__ = ["TaskWorkerBundle", "create_task_worker"]
