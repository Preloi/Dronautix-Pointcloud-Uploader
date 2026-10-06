"""New uploads and local-only conversion started from the upload page.

Mixin of the main window; see ``qt_app/main_window.py``.
"""

from __future__ import annotations

import dataclasses
import tempfile
import threading

from ..activity_model import ACTION_CONVERT as ACTIVITY_ACTION_CONVERT, ACTION_UPLOAD as ACTIVITY_ACTION_UPLOAD
from ..project_management_actions import ProjectOperationSummary
from .support import UPLOAD_TEMP_PREFIX, _remove_temp_dir_with_retry


class UploadMixin:
    def _handle_upload_action(self):
        if self._update_install_started:
            self.statusBar().showMessage("Update-Installation läuft; bitte warten.")
            return
        if self._has_active_background_tasks():
            self.statusBar().showMessage("Eine Aktion läuft bereits; bitte warten.")
            return
        pending = getattr(self._upload_page, "crs_detection_pending", None)
        if callable(pending) and pending():
            # Also covers Ctrl+Enter, which bypasses the disabled button.
            self.statusBar().showMessage("CRS der Punktwolken wird noch erkannt; bitte kurz warten.")
            return
        form = self._upload_page.read_form()
        if form.mode == "convert":
            self._run_local_conversion(form)
        else:
            self._run_new_upload(form)

    def _request_upload_cancel(self):
        cancel_event = self._upload_cancel_event
        if cancel_event is not None:
            cancel_event.set()
            self.statusBar().showMessage("Wird abgebrochen...")

    def _run_new_upload(self, form):
        upload_controller = self._runtime.get("upload_controller")
        if upload_controller is None:
            self._upload_page.show_error("Keine S3-Verbindung. AWS-Zugangsdaten in den Einstellungen hinterlegen.")
            self.statusBar().showMessage("Upload nicht möglich: keine S3-Verbindung.")
            return
        from ..upload_dialog_models import UploadDialogState, validate_upload_dialog_state

        # Convert into a temporary, app-writable folder instead of the user's
        # output directory. It is removed once the upload finishes.
        temp_output_dir = tempfile.mkdtemp(prefix=UPLOAD_TEMP_PREFIX)

        try:
            request = validate_upload_dialog_state(
                UploadDialogState(
                    customer=form.customer,
                    project=form.project,
                    source_paths=form.source_paths,
                    converter_path=form.converter_path,
                    output_base_dir=temp_output_dir,
                    overwrite=True,
                    horizontal_crs=form.horizontal_crs,
                    vertical_crs=form.vertical_crs,
                    model_inputs=self._upload_page.model_inputs(),
                )
            )
        except (ValueError, FileExistsError) as error:
            cleanup_warnings: list[str] = []
            _remove_temp_dir_with_retry(temp_output_dir, cleanup_warnings)
            self._upload_page.show_error(str(error))
            return

        # Attach automatically detected (and/or manually overridden) per-source CRS.
        detected_crs = self._upload_page.crs_info_by_source_path()
        if detected_crs:
            request = dataclasses.replace(request, crs_info_by_source_path=detected_crs)

        self._upload_page.set_running(True)
        cancel_event = threading.Event()
        self._upload_cancel_event = cancel_event
        progress_callback = self._make_progress_callback(
            ACTIVITY_ACTION_UPLOAD,
            project=request.projekt,
            customer=request.kunde,
            actor="Upload",
            extra_sink=self._upload_page.handle_progress,
        )

        def run_upload():
            return upload_controller.upload_new_project(
                request,
                on_progress=progress_callback,
                cancel_requested=cancel_event.is_set,
                confirm_spatial_warning=self._confirm_spatial_warning,
            )

        def handle_result(summary: ProjectOperationSummary):
            self._show_project_operation_summary(
                summary,
                action=ACTIVITY_ACTION_UPLOAD,
                project=request.projekt,
                customer=request.kunde,
                actor="Upload",
            )
            self._notify_operation_summary(summary, "Upload")
            self._refresh_projects_page()

        def finish_upload():
            self._upload_cancel_event = None
            self._upload_page.append_log("Räume temporäre Konvertierungsdateien auf...")
            self._upload_page.set_status("Temporäre Dateien werden aufgeräumt...")
            cleanup_warnings: list[str] = []
            _remove_temp_dir_with_retry(temp_output_dir, cleanup_warnings)
            if cleanup_warnings:
                for warning in cleanup_warnings:
                    self._upload_page.append_log(f"[WARNUNG] {warning}")
                self._upload_page.set_status("Upload beendet; temporäre Dateien benötigen Cleanup.")
            else:
                self._upload_page.append_log("Temporäre Dateien entfernt.")
                self._upload_page.set_status("Fertig.")
            self._upload_page.set_running(False)
            self._release_progress_callback(progress_callback)

        self.statusBar().showMessage("Upload gestartet")
        self._start_background_task(
            run_upload,
            on_result=handle_result,
            on_error=lambda error: self._notify_task_error(
                error,
                "Upload",
                action=ACTIVITY_ACTION_UPLOAD,
                project=request.projekt,
                customer=request.kunde,
                actor="Upload",
            ),
            on_finished=finish_upload,
        )

    def _run_local_conversion(self, form):
        if self._local_conversion_controller is None:
            self._upload_page.show_error("Konvertierung ist in dieser Umgebung nicht verfügbar.")
            self.statusBar().showMessage("Konvertierung nicht verfügbar.")
            return
        from ..local_conversion_dialog_models import LocalConversionDialogState

        source_file = form.source_paths[0] if form.source_paths else ""
        # Snapshot the cached detection/manual overrides on the GUI thread.
        # The controller validates paths in the worker; even stat can hang on NAS.
        request = LocalConversionDialogState(
            source_file=source_file,
            output_dir=form.output_base_dir,
            converter_path=form.converter_path,
            overwrite=form.overwrite,
            crs_info=self._upload_page.crs_info_by_source_path().get(source_file),
        )

        self._upload_page.set_running(True)
        cancel_event = threading.Event()
        self._upload_cancel_event = cancel_event
        progress_callback = self._make_progress_callback(
            ACTIVITY_ACTION_CONVERT,
            actor="Lokale Konvertierung",
            source_path=request.source_file,
            target_path=request.output_dir,
            extra_sink=self._upload_page.handle_progress,
        )

        def run_conversion():
            return self._local_conversion_controller.run_conversion(
                request,
                on_progress=progress_callback,
                cancel_requested=cancel_event.is_set,
            )

        def handle_result(summary: ProjectOperationSummary):
            self._show_project_operation_summary(
                summary,
                action=ACTIVITY_ACTION_CONVERT,
                actor="Lokale Konvertierung",
                source_path=request.source_file,
                target_path=request.output_dir,
            )
            self._notify_operation_summary(summary, "Lokale Konvertierung")

        def finish_conversion():
            self._upload_cancel_event = None
            self._upload_page.set_running(False)
            self._release_progress_callback(progress_callback)

        self.statusBar().showMessage("Lokale Konvertierung gestartet")
        self._start_background_task(
            run_conversion,
            on_result=handle_result,
            on_error=lambda error: self._notify_task_error(
                error,
                "Lokale Konvertierung",
                action=ACTIVITY_ACTION_CONVERT,
                actor="Lokale Konvertierung",
                source_path=request.source_file,
                target_path=request.output_dir,
            ),
            on_finished=finish_conversion,
        )

    def _settings_dialog_defaults(self):
        if self._settings_controller is None:
            return None
        try:
            return self._settings_controller.load_state()
        except Exception:
            return None
