"""Background tasks, busy state, progress bridges and result/error reporting.

Mixin of the main window; see ``qt_app/main_window.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..activity_model import ACTION_UPDATE as ACTIVITY_ACTION_UPDATE
from ..project_management_actions import ProjectOperationSummary
from ..error_messages import describe_error, technical_details
from ..service_bridge import QtServiceBridge
from ..task_worker import create_task_worker


class TaskRunnerMixin:
    def _has_active_background_tasks(self) -> bool:
        page = getattr(self, "_projects_page", None)
        return bool(self._active_tasks or getattr(page, "_active_project_loads", ()))

    def _show_project_operation_summary(
        self,
        summary: ProjectOperationSummary,
        *,
        action: str = ACTIVITY_ACTION_UPDATE,
        project: str = "",
        customer: str = "",
        actor: str = "Service",
        source_path: str = "",
        target_path: str = "",
    ):
        self._activity_store.record_operation_summary(
            summary,
            action=action,
            project=project,
            customer=customer,
            actor=actor,
            source_path=source_path,
            target_path=target_path,
        )
        self._refresh_activity_page()
        self.statusBar().showMessage(summary.statusbar_text)

    def _show_task_error(
        self,
        error,
        *,
        action: str = ACTIVITY_ACTION_UPDATE,
        project: str = "",
        customer: str = "",
        actor: str = "Service",
        source_path: str = "",
        target_path: str = "",
    ):
        self._activity_store.record_error(
            error,
            action=action,
            project=project,
            customer=customer,
            actor=actor,
            source_path=source_path,
            target_path=target_path,
        )
        self._refresh_activity_page()
        self.statusBar().showMessage(describe_error(error))

    def _make_progress_callback(
        self,
        action: str,
        *,
        project: str = "",
        customer: str = "",
        actor: str = "Service",
        source_path: str = "",
        target_path: str = "",
        extra_sink: Callable[[Any], None] | None = None,
    ):
        bridge = QtServiceBridge()
        emitter = bridge.attach_qt_progress_signal()

        def receive_progress(event: Any):
            message = str(getattr(event, "message", "") or "")
            if message:
                self.statusBar().showMessage(message)
            if extra_sink is not None:
                extra_sink(event)
            if getattr(event, "kind", "") not in {"log", "step", "detail", "warning", "error"}:
                return
            self._activity_store.record_progress_event(
                event,
                action=action,
                project=project,
                customer=customer,
                actor=actor,
                source_path=source_path,
                target_path=target_path,
            )
            self._refresh_activity_page()

        emitter.progress.connect(receive_progress)
        progress_callback = bridge.progress_callback
        self._progress_bridges[progress_callback] = (bridge, emitter)
        return progress_callback

    def _release_progress_callback(self, progress_callback):
        self._progress_bridges.pop(progress_callback, None)

    def _notify_operation_summary(self, summary: ProjectOperationSummary, title: str):
        status = str(getattr(summary, "status", "") or "")
        text = "\n".join(getattr(summary, "activity_lines", ()) or (summary.message,))
        if status == "failed":
            self.QtWidgets.QMessageBox.critical(self, title, text)
        elif status in {"partial", "cancelled"}:
            self.QtWidgets.QMessageBox.warning(self, title, text)
        else:
            self.QtWidgets.QMessageBox.information(self, title, text)

    def _notify_task_error(self, error, title: str, **record_kwargs):
        self._show_task_error(error, **record_kwargs)
        message = describe_error(error)
        details = technical_details(error)
        # The original boto/urllib text stays visible for support.
        self.QtWidgets.QMessageBox.critical(self, title, f"{message}\n\nDetails: {details}" if details else message)

    def _start_background_task(
        self,
        task,
        *,
        on_result,
        on_error=None,
        on_finished=None,
    ):
        bundle = create_task_worker(self.QtCore, task)
        self._active_tasks.append(bundle)

        def handle_error(error):
            if on_error is not None:
                on_error(error)
            else:
                self._show_task_error(error)

        # The worker emits from its own thread. Connecting the signals to a
        # QObject that lives on the GUI thread forces a queued connection, so
        # the result/error/finished callbacks (which touch widgets and dialogs)
        # always run on the main thread instead of the worker thread.
        dispatcher = self._qt_types.MainThreadTaskDispatcher(on_result, handle_error, on_finished)
        self._task_records[bundle.thread] = (bundle, dispatcher)
        bundle.worker.result.connect(dispatcher.dispatch_result)
        bundle.worker.error.connect(dispatcher.dispatch_error)
        bundle.worker.finished.connect(dispatcher.dispatch_finished)
        # Das Bundle wird per Closure uebergeben: sender() ist bei queued
        # Cross-Thread-Signalen in PySide6 None, damit wuerde der Task nie
        # abgeraeumt und alle Folgeaktionen blieben blockiert.
        bundle.thread.finished.connect(lambda b=bundle: self._cleanup_task_bundle(b))
        bundle.thread.start()
        return bundle

    def _cleanup_task_bundle(self, bundle):
        self._task_records.pop(bundle.thread, None)
        if bundle in self._active_tasks:
            self._active_tasks.remove(bundle)
        self._resume_pending_update_if_idle()
